import type {
  Supplier,
  SupplierCreate,
  SupplierListItem,
  SupplierFilters,
  SupplierStatus,
} from "@/types/supplier";
import { CATEGORIES } from "@/types/supplier";
import { telemetryFetch, track, withResponseTelemetry } from "@/services/telemetry";

function isSupplierListItem(value: unknown): value is SupplierListItem {
  if (typeof value !== "object" || value === null) return false;
  const supplier = value as Record<string, unknown>;
  return typeof supplier.id === "number" && Number.isInteger(supplier.id) && supplier.id > 0
    && typeof supplier.name === "string"
    && (supplier.country === "USA" || supplier.country === "UK")
    && Array.isArray(supplier.categories)
    && supplier.categories.every((value: unknown) => CATEGORIES.some((category) => category === value))
    && typeof supplier.monthly_rate === "number" && Number.isFinite(supplier.monthly_rate)
    && (supplier.currency === "USD" || supplier.currency === "GBP")
    && (supplier.status === "active" || supplier.status === "suspended")
    && (supplier.compliance_agreement === null || supplier.compliance_agreement === "BAA" ||
      supplier.compliance_agreement === "DPA" || supplier.compliance_agreement === "both")
    && (supplier.contact_email === null || typeof supplier.contact_email === "string")
    && typeof supplier.updated_at === "string";
}

/** Full supplier (detail and create/update responses): list fields + detail-only fields. */
function isSupplier(value: unknown): value is Supplier {
  if (!isSupplierListItem(value)) return false;
  const supplier = value as Record<string, unknown>;
  return ["contract_renewal_date", "notes"].every(
    (field) => supplier[field] === null || typeof supplier[field] === "string",
  );
}

async function request<T>(
  path: string,
  options?: RequestInit,
  capture?: (data: unknown, response: Response) => void,
): Promise<T> {
  let response: Response;
  try {
    response = await telemetryFetch(`/api/suppliers${path}`, {
      ...options,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...options?.headers },
    });
  } catch (error) {
    if (options?.signal?.aborted) throw error;
    throw new Error("No se pudo conectar con el servicio. Comprueba tu conexión.");
  }
  if (!response.ok) {
    if (response.status === 401) {
      throw new Error("Tu sesión ha caducado. Inicia sesión de nuevo.");
    }
    let detail = `No se pudo completar la solicitud (${response.status}).`;
    if (response.status < 500 && response.headers.get("content-type")?.includes("application/json")) {
      try {
        const body: unknown = await response.json();
        if (typeof body === "object" && body !== null && "detail" in body) {
          if (typeof body.detail === "string") detail = body.detail;
          else if (Array.isArray(body.detail)) {
            const messages = body.detail.flatMap((item: unknown) =>
              typeof item === "object" && item !== null && "msg" in item &&
              typeof item.msg === "string" ? [item.msg] : [],
            );
            if (messages.length) detail = messages.join("; ");
          }
        }
      } catch {
        // Keep the generic error when the server does not return valid JSON.
      }
    }
    throw new Error(detail);
  }
  try {
    const data: unknown = await response.json();
    capture?.(data, response);
    return data as T;
  } catch {
    throw new Error("El servicio devolvió una respuesta inválida.");
  }
}

async function supplierRequest(
  path: string,
  options: RequestInit,
  capture?: (supplier: Supplier) => void,
): Promise<Supplier> {
  const result = await request<unknown>(path, options, (data, response) => {
    if (capture && isSupplier(data)) withResponseTelemetry(response, () => capture(data));
  });
  if (!isSupplier(result)) throw new Error("El servicio devolvió un proveedor inválido.");
  return result;
}

export async function listSuppliers(
  filters: SupplierFilters,
  signal: AbortSignal,
): Promise<SupplierListItem[]> {
  const query = new URLSearchParams();
  if (filters.country) query.set("country", filters.country);
  if (filters.category) query.set("category", filters.category);
  const result = await request<unknown>(`?${query}`, { signal }, (data, response) => {
    if (Array.isArray(data) && data.every(isSupplierListItem)) withResponseTelemetry(response, () => track("supplier_filter_applied", {
      country: filters.country || null,
      category: filters.category || null,
      result_count_bucket: data.length === 0 ? "0" : data.length <= 5 ? "1_5" : data.length <= 20 ? "6_20" : "over_20",
    }));
  });
  if (!Array.isArray(result) || !result.every(isSupplierListItem)) {
    throw new Error("El servicio devolvió un listado inválido.");
  }
  return result;
}

export async function createSupplier(payload: SupplierCreate): Promise<Supplier> {
  return await supplierRequest("", {
    method: "POST",
    body: JSON.stringify(payload),
  }, (supplier) => track("supplier_created", {
    country: supplier.country, categories_count: supplier.categories.length, status: supplier.status,
  }));
}

export async function updateSupplierRate(
  id: number,
  monthly_rate: number,
  previous_rate?: number,
): Promise<Supplier> {
  return await supplierRequest(`/${id}/rate`, {
    method: "PATCH",
    body: JSON.stringify({ monthly_rate }),
  }, (supplier) => {
    if (previous_rate !== undefined) track("supplier_rate_updated", {
      currency: supplier.currency,
      change_band: supplier.monthly_rate === previous_rate ? "unchanged" : supplier.monthly_rate > previous_rate ? "increased" : "decreased",
    });
  });
}

export async function updateSupplierStatus(
  id: number,
  status: SupplierStatus,
  previous_status?: SupplierStatus,
): Promise<Supplier> {
  return await supplierRequest(`/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  }, (supplier) => {
    if (previous_status && previous_status !== supplier.status) track("supplier_status_changed", {
      country: supplier.country, previous_status, new_status: supplier.status,
    });
  });
}
