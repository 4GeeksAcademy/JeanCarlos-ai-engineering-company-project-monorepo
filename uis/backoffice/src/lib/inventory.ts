"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";
import type { InboundInput, Orders, OutboundInput, Product, Supply } from "@/types/inventory";

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

async function request(path: string, options: RequestInit = {}): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`/api/inventory${path}`, {
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
      } catch {
        console.error("La API de inventario devolvió un error inválido.");
      }
    }
    throw new InventoryError(message, response.status);
  }
  try {
    return await response.json() as unknown;
  } catch {
    throw new Error("El servicio devolvió una respuesta inválida.");
  }
}

export async function listProducts(signal?: AbortSignal): Promise<Product[]> {
  const result = await request("/products", { signal });
  if (!Array.isArray(result) || !result.every(isProduct)) {
    throw new Error("El servicio devolvió un catálogo inválido.");
  }
  return result;
}

export async function listOrders(signal?: AbortSignal): Promise<Orders> {
  const result = await request("/orders", { signal });
  if (!isOrders(result)) throw new Error("El servicio devolvió un historial inválido.");
  return result;
}

export async function createInbound(payload: InboundInput): Promise<void> {
  await request("/orders/inbound", { method: "POST", body: JSON.stringify(payload) });
}

export async function createOutbound(payload: OutboundInput): Promise<void> {
  await request("/orders/outbound", { method: "POST", body: JSON.stringify(payload) });
}
