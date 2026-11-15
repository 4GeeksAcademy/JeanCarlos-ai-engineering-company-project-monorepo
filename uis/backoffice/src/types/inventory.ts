export type Supply = {
  id: number;
  name: string;
  sku: string;
  category: "ppe" | "wound_care" | "diagnostics" | "medications" | "consumables";
  unit: "box" | "unit" | "pack" | "vial";
  country: "US" | "UK";
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
export type OutboundOrder = Movement & { consumption_type: "clinical_use" | "expiry_waste" };
export type Orders = { inbound: InboundOrder[]; outbound: OutboundOrder[] };
export type MovementInput = { supply_id: number; quantity: number; clinic_id: number };
export type InboundInput = MovementInput & { vendor_name: string };
export type OutboundInput = MovementInput & { consumption_type: OutboundOrder["consumption_type"] };
