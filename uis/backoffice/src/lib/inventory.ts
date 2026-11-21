"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";
import {
  departments,
  type ExpiringSupply,
  type InboundInput,
  type Orders,
  type OutboundInput,
  type OutboundResult,
  type Product,
  type Supply,
} from "@/types/inventory";
import { telemetryFetch, track, withResponseTelemetry } from "@/services/telemetry";

// Last validated catalog, used to enrich events whose API response omits the supply.
const knownSupplies = new Map<number, Supply>();
const DIRECT_EDIT_ROUTE = "/inventory/clinics/{clinic_id}/products/{product_id}/stock";
const EXPIRY_WINDOW_DAYS = 30;
const EXPIRY_FLAGS_KEY = "healthcore.telemetry.expiry-flagged";

/** Minimum inventory properties shared by the BRIEFING events, or null if they cannot be filled. */
function inventoryFields(supply: Supply, clinicId: number) {
  // Supplies created before product_category existed cannot satisfy the contract.
  if (supply.product_category === null) return null;
  // Transitional: the API has no clinic table yet, so country comes from the product.
  return { clinic_id: clinicId, country: supply.country, product_id: supply.id, product_category: supply.product_category };
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
    (value.country === "US" || value.country === "UK") &&
    (value.product_category === null ||
      ["medication", "ppe", "consumable", "equipment"].includes(String(value.product_category))) &&
    (value.expiry_date === null || isIsoDate(value.expiry_date));
}

function isIsoDate(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value));
}

function isDepartment(value: unknown): value is OutboundInput["department"] {
  return departments.some((department) => department.value === value);
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
      (item.consumption_type === "clinical_use" || item.consumption_type === "expiry_waste") &&
      (item.department === null || isDepartment(item.department)));
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
            ...fields, requested_quantity: payload.quantity, available_quantity: Number(available),
            department: "department" in payload ? payload.department : null,
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
      console.warn("Inbound telemetry skipped: product has no BRIEFING category.");
    }
  } else {
    console.warn("Inbound telemetry skipped: invalid movement response.");
  }
}

export async function createOutbound(payload: OutboundInput): Promise<OutboundResult> {
  const { data, response } = await request("/orders/outbound", { method: "POST", body: JSON.stringify(payload) }, payload);
  if (!isRecord(data) || !Number.isInteger(data.id) || !isSupply(data.supply)) {
    console.warn("Outbound telemetry skipped: invalid movement response.");
    return { thresholdCrossed: null };
  }
  const threshold = isRecord(data.stock_threshold) &&
    Number.isInteger(data.stock_threshold.clinic_stock) &&
    Number.isInteger(data.stock_threshold.min_stock_threshold)
    ? { clinicStock: Number(data.stock_threshold.clinic_stock), minimum: Number(data.stock_threshold.min_stock_threshold) }
    : null;
  const fields = inventoryFields(data.supply, payload.clinic_id);
  if (fields) {
    withResponseTelemetry(response, () => {
      track("outbound_order_created", { ...fields, quantity: payload.quantity, department: payload.department });
      // The API reports the falling edge below the clinic's configured minimum.
      if (threshold) {
        track("stock_threshold_triggered", {
          ...fields, quantity: threshold.clinicStock, min_stock_threshold: threshold.minimum,
          department: payload.department,
        });
      }
    });
  } else {
    console.warn("Outbound telemetry skipped: product has no BRIEFING category.");
  }
  return { thresholdCrossed: threshold };
}

/**
 * Attempts to set a clinic's stock outside an order. The API always rejects it
 * (stock only changes through orders); the rejection is the BRIEFING signal.
 */
export async function attemptDirectStockEdit(product: Supply, clinicId: number, quantity: number | null): Promise<never> {
  let response: Response;
  try {
    response = await telemetryFetch(`/api/inventory/clinics/${clinicId}/products/${product.id}/stock`, {
      method: "PUT",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quantity }),
    });
  } catch {
    throw new Error("No se pudo conectar con el servicio.");
  }
  if (response.status === 401) throw new InventoryError("Tu sesión ha caducado.", 401);
  let message = `No se pudo completar la solicitud (${response.status}).`;
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body.detail === "string") message = body.detail;
  } catch {
    // Keep the generic message.
  }
  const fields = inventoryFields(product, clinicId);
  if ((response.status === 403 || response.status === 405) && fields) {
    withResponseTelemetry(response, () => track("direct_stock_edit_rejected", {
      ...fields, quantity, attempted_method: "PUT", route_template: DIRECT_EDIT_ROUTE,
      response_status: response.status,
    }));
  }
  throw new InventoryError(message, response.status);
}

function isExpiringSupply(value: unknown): value is ExpiringSupply {
  return isRecord(value) && isSupply(value.supply) && Number.isInteger(value.clinic_id) &&
    Number.isInteger(value.quantity) && Number(value.quantity) > 0 && isIsoDate(value.expiry_date) &&
    Number.isInteger(value.days_until_expiry) && Number(value.days_until_expiry) >= 0;
}

function loadFlags(): Set<string> {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(EXPIRY_FLAGS_KEY) ?? "[]");
    return new Set(Array.isArray(stored) ? stored.filter((key): key is string => typeof key === "string") : []);
  } catch {
    return new Set();
  }
}

let expiryFlagKeys: Set<string> | null = null;

/** Supplies whose clinic stock expires within 30 days; flags each one once per browser. */
export async function listExpiring(signal?: AbortSignal): Promise<ExpiringSupply[]> {
  const { data: result, response } = await request(`/expiring?within_days=${EXPIRY_WINDOW_DAYS}`, { signal });
  if (!Array.isArray(result) || !result.every(isExpiringSupply)) {
    throw new Error("El servicio devolvió una lista de vencimientos inválida.");
  }
  expiryFlagKeys ??= loadFlags();
  const flags = expiryFlagKeys;
  withResponseTelemetry(response, () => {
    for (const item of result) {
      const key = `${item.clinic_id}:${item.supply.id}:${item.expiry_date}`;
      const fields = inventoryFields(item.supply, item.clinic_id);
      if (flags.has(key) || !fields || item.days_until_expiry > EXPIRY_WINDOW_DAYS) continue;
      track("supply_expiry_flagged", {
        ...fields, quantity: item.quantity, expiry_date: item.expiry_date,
        days_until_expiry: item.days_until_expiry, expiry_window_days: EXPIRY_WINDOW_DAYS,
      });
      flags.add(key);
    }
    try {
      window.localStorage.setItem(EXPIRY_FLAGS_KEY, JSON.stringify([...flags].slice(-500)));
    } catch {
      // Without storage the flag is deduplicated for this page lifetime only.
    }
  });
  return result;
}
