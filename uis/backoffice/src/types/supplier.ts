export const CATEGORIES = [
  "medical_supplies",
  "laboratory_services",
  "pharmaceutical",
  "clinical_software",
  "it_infrastructure",
  "hr_and_payroll_software",
  "cleaning_and_facilities",
  "patient_communication",
  "billing_and_coding_software",
  "training_platforms",
] as const;

export type Category = (typeof CATEGORIES)[number];
export type Country = "USA" | "UK";
export type SupplierStatus = "active" | "suspended";

export interface SupplierCreate {
  name: string;
  country: Country;
  categories: Category[];
  monthly_rate: number;
  currency: "USD" | "GBP";
  status: SupplierStatus;
  compliance_agreement: "BAA" | "DPA" | "both" | null;
  contract_renewal_date: string | null;
  contact_email: string | null;
  notes: string | null;
}

export interface Supplier extends SupplierCreate {
  id: number;
  updated_at: string;
}

/** Row of GET /suppliers. Detail-only fields (notes, contract_renewal_date) are not sent. */
export type SupplierListItem = Omit<Supplier, "notes" | "contract_renewal_date">;

export interface SupplierFilters {
  country: Country | "";
  category: Category | "";
}
