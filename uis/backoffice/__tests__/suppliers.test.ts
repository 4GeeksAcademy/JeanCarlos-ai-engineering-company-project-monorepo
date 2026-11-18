/**
 * src/lib/suppliers.ts — supplier API client: response parsing and error messages.
 */
import { createSupplier, listSuppliers, updateSupplierRate } from "@/lib/suppliers";
import type { Supplier, SupplierCreate } from "@/types/supplier";

jest.mock("@/services/telemetry", () => ({
  telemetryFetch: (input: string, init: RequestInit) => fetch(input, init),
  track: jest.fn(),
  withResponseTelemetry: (_response: Response, capture: () => void) => capture(),
}));

const fetchMock = jest.fn();
const signal = new AbortController().signal;
const NO_FILTERS = { country: "", category: "" } as const;

const SUPPLIER: Supplier = {
  id: 1, name: "MedLine", country: "USA", categories: ["medical_supplies"], monthly_rate: 1200,
  currency: "USD", status: "active", compliance_agreement: "BAA", contract_renewal_date: null,
  contact_email: null, notes: null, updated_at: "2024-01-01T00:00:00Z",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => { global.fetch = fetchMock; });

describe("listSuppliers", () => {
  it("returns the parsed list and sends only the filters that are set", async () => {
    fetchMock.mockResolvedValue(json([SUPPLIER]));
    await expect(listSuppliers({ country: "UK", category: "" }, signal)).resolves.toEqual([SUPPLIER]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/suppliers?country=UK");
  });

  it("accepts an empty directory", async () => {
    fetchMock.mockResolvedValue(json([]));
    await expect(listSuppliers(NO_FILTERS, signal)).resolves.toEqual([]);
  });

  it.each([
    ["not a list", { suppliers: [] }],
    ["an unknown category", [{ ...SUPPLIER, categories: ["catering"] }]],
    ["a non-numeric rate", [{ ...SUPPLIER, monthly_rate: "1200" }]],
    ["a missing updated_at", [{ ...SUPPLIER, updated_at: undefined }]],
  ])("rejects a response with %s instead of rendering bad data", async (_case, body) => {
    fetchMock.mockResolvedValue(json(body));
    await expect(listSuppliers(NO_FILTERS, signal)).rejects.toThrow("listado inválido");
  });

  it("explains an expired session in plain language", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Invalid or expired credentials" }, 401));
    await expect(listSuppliers(NO_FILTERS, signal)).rejects.toThrow("Tu sesión ha caducado");
  });

  it("does not leak server errors (5xx) to the user", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Traceback: KeyError" }, 502));
    await expect(listSuppliers(NO_FILTERS, signal)).rejects.toThrow("No se pudo completar la solicitud (502).");
  });

  it("maps network failures to a connection message", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(listSuppliers(NO_FILTERS, signal)).rejects.toThrow("No se pudo conectar con el servicio");
  });

  it("rejects a 200 response whose body is not JSON", async () => {
    fetchMock.mockResolvedValue(new Response("<html>", { status: 200 }));
    await expect(listSuppliers(NO_FILTERS, signal)).rejects.toThrow("respuesta inválida");
  });
});

describe("createSupplier / updateSupplierRate", () => {
  const payload: SupplierCreate = {
    name: SUPPLIER.name, country: SUPPLIER.country, categories: SUPPLIER.categories,
    monthly_rate: SUPPLIER.monthly_rate, currency: SUPPLIER.currency, status: SUPPLIER.status,
    compliance_agreement: SUPPLIER.compliance_agreement, contract_renewal_date: null,
    contact_email: null, notes: null,
  };

  it("returns the created supplier", async () => {
    fetchMock.mockResolvedValue(json(SUPPLIER, 201));
    await expect(createSupplier(payload)).resolves.toEqual(SUPPLIER);
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("joins FastAPI validation messages for the form", async () => {
    fetchMock.mockResolvedValue(json({ detail: [{ msg: "USA requires currency USD" }, { msg: "Field required" }] }, 422));
    await expect(createSupplier(payload)).rejects.toThrow("USA requires currency USD; Field required");
  });

  it("surfaces a 404 detail when updating a supplier that no longer exists", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Supplier not found" }, 404));
    await expect(updateSupplierRate(99, 10)).rejects.toThrow("Supplier not found");
  });

  it("rejects an updated supplier with an invalid shape", async () => {
    fetchMock.mockResolvedValue(json({ ...SUPPLIER, status: "deleted" }));
    await expect(updateSupplierRate(1, 10)).rejects.toThrow("proveedor inválido");
  });
});
