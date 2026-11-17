/**
 * HealthCore inventory vocabulary (Hito 5 BRIEFING): MedicalSupply,
 * SupplyDelivery (entrega de proveedor) and SupplyConsumption (consumo clínico).
 * API values stay in English; these are the labels shown to operations staff.
 */
import type { OutboundOrder, Supply } from "@/types/inventory";

export const CATEGORY_LABELS: Record<Supply["category"], string> = {
  ppe: "EPI",
  wound_care: "Material de curas",
  diagnostics: "Diagnóstico",
  medications: "Medicamentos",
  consumables: "Consumibles",
};

export const UNIT_LABELS: Record<Supply["unit"], string> = {
  box: "caja(s)",
  unit: "unidad(es)",
  pack: "pack(s)",
  vial: "vial(es)",
};

export const COUNTRY_LABELS: Record<Supply["country"], string> = {
  US: "EE. UU.",
  UK: "Reino Unido",
};

export const CONSUMPTION_LABELS: Record<OutboundOrder["consumption_type"], string> = {
  clinical_use: "Uso clínico",
  expiry_waste: "Caducado y desechado",
};

/** Clinic ids 1–12: 9 clinics in the US (1–9) and 3 in the UK (10–12). */
export const CLINIC_IDS = Array.from({ length: 12 }, (_, index) => index + 1);

export function clinicLabel(clinicId: number): string {
  return `Clínica ${clinicId} · ${clinicId <= 9 ? COUNTRY_LABELS.US : COUNTRY_LABELS.UK}`;
}

export function quantityLabel(quantity: number, unit: Supply["unit"]): string {
  return `${quantity} ${UNIT_LABELS[unit]}`;
}
