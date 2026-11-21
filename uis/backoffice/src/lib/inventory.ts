"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";
import type { InboundInput, Orders, OutboundInput, Product, Supply } from "@/types/inventory";
import { telemetryFetch, track, withResponseTelemetry } from "@/services/telemetry";

// Last validated catalog, used to enrich events whose API response omits the supply.
const knownSupplies = new Map<number, Supply>();

type ProductCategory = "medication" | "ppe" | "consumable" | "equipment";

// Transitional API → BRIEFING mapping (telemetry-plan.md, dry-gap 3). `diagnostics`
// has no safe default: it is decided per product, so those events are not emitted.
const briefingCategory: Record<Supply["category"], ProductCategory | null> = {
  medications: "medication",
  ppe: "ppe",
  consumables: "consumable",
  wound_care: "consumable",
  diagnostics: null,
};

function inventoryFields(supply: Supply, clinicId: number) {
  const productCategory = briefingCategory[supply.category];
  if (productCategory === null) return null;
  // Transitional: the API has no clinic table yet, so country comes from the product.
  return { clinic_id: clinicId, country: supply.country, product_id: supply.id, product_category: productCategory };
}

export class InventoryError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
  }
}

export function useInventoryError() {
  const router = useRouter();
  return useCallback((error: unknown, fallback: string): string => {
    if (error instanceof InventoryError && error.status === 401) router.replace("/login");
    return error instanceof Error ? error.message : fallback;
  }, [router]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isSupply(value: unknown): value is Supply {
  return isRecord(value) && Number.isInteger(value.id) && Number(value.id) > 0 &&
    typeof value.name === "string" && typeof value.sku === "string" &&
    ["ppe", "wound_care", "diagnostics", "medications", "consumables"].includes(String(value.category)) &&
    ["box", "unit", "pack", "vial"].includes(String(value.unit)) &&
    (value.country === "US" || value.country === "UK");
}

function isProduct(value: unknown): value is Product {
  return isSupply(value) && "current_stock" in value && Number.isInteger(value.current_stock);
}

function isMovement(value: unknown): boolean {
  return isRecord(value) && Number.isInteger(value.id) && Number.isInteger(value.supply_id) &&
    Number.isInteger(value.quantity) && Number.isInteger(value.clinic_id) &&
    typeof value.created_at === "string" && !Number.isNaN(Date.parse(value.created_at)) &&
    typeof value.user_uuid === "string" && isSupply(value.supply);
}

function isOrders(value: unknown): value is Orders {
  return isRecord(value) && Array.isArray(value.inbound) && Array.isArray(value.outbound) &&
    value.inbound.every((item: unknown) =>
      isMovement(item) && isRecord(item) && typeof item.vendor_name === "string") &&
    value.outbound.every((item: unknown) =>
      isMovement(item) && isRecord(item) &&
      (item.consumption_type === "clinical_use" || item.consumption_type === "expiry_waste"));
}

async function request(
  path: string,
  options: RequestInit = {},
  payload?: InboundInput | OutboundInput,
): Promise<{ data: unknown; response: Response }> {
  let response: Response;
  try {
    response = await telemetryFetch(`/api/inventory${path}`, {
      ...options,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...options.headers },
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new Error("No se pudo conectar con el servicio.");
  }
  if (response.status === 401) {
    throw new InventoryError("Tu sesión ha caducado.", 401);
  }
  if (!response.ok) {
    let message = `No se pudo completar la solicitud (${response.status}).`;
    if (response.headers.get("content-type")?.includes("application/json")) {
      try {
        const body: unknown = await response.json();
        if (isRecord(body) && typeof body.detail === "string") message = body.detail;
        if (response.status === 422 && payload && isRecord(body) && Array.isArray(body.detail)) {
          const fieldNames = new Set<string>();
          const errorCodes = new Set<string>();
          for (const issue of body.detail) {
            if (!isRecord(issue)) continue;
            if (Array.isArray(issue.loc)) {
              const field = issue.loc[1];
              if (typeof field === "string" && ["supply_id", "quantity", "clinic_id", "vendor_name", "consumption_type"].includes(field)) fieldNames.add(field);
            }
            const type = typeof issue.type === "string" ? issue.type : "";
            errorCodes.add(type === "missing" ? "missing" : type === "extra_forbidden" ? "extra_forbidden" :
              type.includes("greater_than") || type.includes("less_than") ? "out_of_range" :
                type === "literal_error" ? "invalid_choice" : "invalid_type");
          }
          withResponseTelemetry(response, () => track("inventory_order_validation_failed", {
            operation: path === "/orders/inbound" ? "inbound" : "outbound",
            field_names: [...fieldNames], error_codes: [...errorCodes], occurrence_count: 1,
          }));
        }
      } catch {
        console.error("La API de inventario devolvió un error inválido.");
      }
      const available = response.headers.get("x-available-stock");
      if (response.status === 400 && path === "/orders/outbound" && payload &&
        available !== null && /^\d+$/.test(available)) {
        const supply = knownSupplies.get(payload.supply_id);
        const fields = supply ? inventoryFields(supply, payload.clinic_id) : null;
        if (fields) {
          withResponseTelemetry(response, () => track("inventory_outbound_insufficient_stock_rejected", {
            ...fields, requested_quantity: payload.quantity, available_quantity: Number(available), department: null,
          }));
        }
      }
    }
    throw new InventoryError(message, response.status);
  }
  try {
    const data: unknown = await response.json();
    return { data, response };
  } catch {
    throw new Error("El servicio devolvió una respuesta inválida.");
  }
}

export async function listProducts(signal?: AbortSignal): Promise<Product[]> {
  const { data: result } = await request("/products", { signal });
  if (!Array.isArray(result) || !result.every(isProduct)) {
    throw new Error("El servicio devolvió un catálogo inválido.");
  }
  // stock_threshold_triggered is emitted by the backend against the per-clinic
  // minimum; the informative <=10 badge is not that threshold and emits nothing.
  knownSupplies.clear();
  for (const product of result) knownSupplies.set(product.id, product);
  return result;
}

export async function listOrders(signal?: AbortSignal): Promise<Orders> {
  const { data: result, response } = await request("/orders", { signal });
  if (!isOrders(result)) throw new Error("El servicio devolvió un historial inválido.");
  withResponseTelemetry(response, () => track("inventory_orders_viewed", { view_mode: "read_only_history" }));
  return result;
}

export async function createInbound(payload: InboundInput): Promise<void> {
  const { data, response } = await request("/orders/inbound", { method: "POST", body: JSON.stringify(payload) }, payload);
  if (isRecord(data) && Number.isInteger(data.id) && isSupply(data.supply)) {
    const fields = inventoryFields(data.supply, payload.clinic_id);
    if (fields) {
      // supplier_id stays null until inbound orders reference the supplier directory.
      withResponseTelemetry(response, () => track("inbound_order_created", {
        ...fields, quantity: payload.quantity, supplier_id: null,
      }));
    } else {
      console.warn("Inbound telemetry skipped: product category has no BRIEFING mapping.");
    }
  } else {
    console.warn("Inbound telemetry skipped: invalid movement response.");
  }
}

export async function createOutbound(payload: OutboundInput): Promise<void> {
  // outbound_order_created requires `department`, which outbound orders do not
  // record yet (telemetry-plan.md, dry-gap 4). Emitting it without that field would
  // break the contract, so it is not emitted until the API captures it.
  await request("/orders/outbound", { method: "POST", body: JSON.stringify(payload) }, payload);
}
