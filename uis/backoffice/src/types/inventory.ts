export type ProductCategory = "medication" | "ppe" | "consumable" | "equipment";
export type Department = "primary_care" | "general_consultation" | "specialty_care" | "chronic_care";

export const departments: { value: Department; label: string }[] = [
  { value: "primary_care", label: "Atención primaria" },
  { value: "general_consultation", label: "Consulta general" },
  { value: "specialty_care", label: "Especialidades" },
  { value: "chronic_care", label: "Enfermedades crónicas" },
];

export type Supply = {
  id: number;
  name: string;
  sku: string;
  category: "ppe" | "wound_care" | "diagnostics" | "medications" | "consumables";
  unit: "box" | "unit" | "pack" | "vial";
  country: "US" | "UK";
  /** BRIEFING category; null only for supplies created before it existed. */
  product_category: ProductCategory | null;
  expiry_date: string | null;
};

export type Product = Supply & { current_stock: number };

type Movement = {
  id: number;
  supply_id: number;
  quantity: number;
  clinic_id: number;
  created_at: string;
  user_uuid: string;
  supply: Supply;
};

export type InboundOrder = Movement & { vendor_name: string };
export type OutboundOrder = Movement & {
  consumption_type: "clinical_use" | "expiry_waste";
  department: Department | null;
};
export type Orders = { inbound: InboundOrder[]; outbound: OutboundOrder[] };
export type MovementInput = { supply_id: number; quantity: number; clinic_id: number };
export type InboundInput = MovementInput & { vendor_name: string };
export type OutboundInput = MovementInput & {
  consumption_type: OutboundOrder["consumption_type"];
  department: Department;
};
export type OutboundResult = { thresholdCrossed: { clinicStock: number; minimum: number } | null };
export type ExpiringSupply = {
  supply: Supply;
  clinic_id: number;
  quantity: number;
  expiry_date: string;
  days_until_expiry: number;
};
