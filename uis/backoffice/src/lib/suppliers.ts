import type {
  Supplier,
  SupplierCreate,
  SupplierFilters,
  SupplierStatus,
} from "@/types/supplier";

interface ValidationError {
  loc: (string | number)[];
  msg: string;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/suppliers${path}`, {
    ...options,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const body = await response.text();
    let detail = body;
    if (response.headers.get("content-type")?.includes("application/json")) {
      const error: { detail?: string | ValidationError[] } = JSON.parse(body);
      detail = Array.isArray(error.detail)
        ? error.detail.map((item) => `${item.loc.join(".")}: ${item.msg}`).join("; ")
        : error.detail ?? body;
    }
    throw new Error(`Error ${response.status}: ${detail || response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export async function listSuppliers(
  filters: SupplierFilters,
  signal: AbortSignal,
): Promise<Supplier[]> {
  const query = new URLSearchParams();
  if (filters.country) query.set("country", filters.country);
  if (filters.category) query.set("category", filters.category);
  return await request<Supplier[]>(`?${query}`, { signal });
}

export async function createSupplier(payload: SupplierCreate): Promise<Supplier> {
  return await request<Supplier>("", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateSupplierRate(
  id: number,
  monthly_rate: number,
): Promise<Supplier> {
  return await request<Supplier>(`/${id}/rate`, {
    method: "PATCH",
    body: JSON.stringify({ monthly_rate }),
  });
}

export async function updateSupplierStatus(
  id: number,
  status: SupplierStatus,
): Promise<Supplier> {
  return await request<Supplier>(`/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}
