/**
 * src/lib/inventory.ts — inventory API client: parsers and error handling.
 */
import { InventoryError, createOutbound, listOrders, listProducts } from "@/lib/inventory";
import { track } from "@/services/telemetry";

jest.mock("next/navigation", () => ({ useRouter: jest.fn() }));
jest.mock("@/services/telemetry", () => ({
  telemetryFetch: (input: string, init: RequestInit) => fetch(input, init),
  track: jest.fn(),
  withResponseTelemetry: (_response: Response, capture: () => void) => capture(),
}));

const fetchMock = jest.fn();
const SUPPLY = { id: 1, name: "Gloves", sku: "HCR-PPE-001", category: "ppe", unit: "box", country: "US" };
const MOVEMENT = { id: 5, supply_id: 1, quantity: 10, clinic_id: 2, created_at: "2024-01-01T10:00:00Z", user_uuid: "u-1", supply: SUPPLY };

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

beforeEach(() => { global.fetch = fetchMock; });

describe("listProducts", () => {
  it("returns products with their current stock", async () => {
    fetchMock.mockResolvedValue(json([{ ...SUPPLY, current_stock: 40 }]));
    await expect(listProducts()).resolves.toEqual([{ ...SUPPLY, current_stock: 40 }]);
  });

  it("raises the low-stock event only when a product crosses the threshold", async () => {
    // Uses its own id so the module-level stock memory starts empty for it.
    const product = { ...SUPPLY, id: 77 };
    fetchMock.mockResolvedValueOnce(json([{ ...product, current_stock: 11 }]));
    await listProducts();
    fetchMock.mockResolvedValueOnce(json([{ ...product, current_stock: 10 }]));
    await listProducts();
    fetchMock.mockResolvedValueOnce(json([{ ...product, current_stock: 3 }]));
    await listProducts();
    // 11 → 10 crosses the threshold once; 10 → 3 is already below it.
    expect(track).toHaveBeenCalledTimes(1);
    expect(track).toHaveBeenCalledWith("inventory_stock_threshold_triggered", expect.objectContaining({ supply_id: 77, current_stock: 10 }));
  });

  it.each([
    ["a fractional stock", [{ ...SUPPLY, current_stock: 1.5 }]],
    ["an unknown unit", [{ ...SUPPLY, unit: "pallet", current_stock: 1 }]],
    ["a missing stock", [SUPPLY]],
  ])("rejects a catalogue with %s", async (_case, body) => {
    fetchMock.mockResolvedValue(json(body));
    await expect(listProducts()).rejects.toThrow("catálogo inválido");
  });

  it("flags an expired session with status 401 so the page can redirect", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Invalid or expired credentials" }, 401));
    await expect(listProducts()).rejects.toEqual(new InventoryError("Tu sesión ha caducado.", 401));
  });
});

describe("listOrders", () => {
  it("returns inbound and outbound movements", async () => {
    const orders = { inbound: [{ ...MOVEMENT, vendor_name: "MedLine" }], outbound: [] };
    fetchMock.mockResolvedValue(json(orders));
    await expect(listOrders()).resolves.toEqual(orders);
  });

  it("rejects movements with an unknown consumption type or an invalid date", async () => {
    fetchMock.mockResolvedValueOnce(json({ inbound: [], outbound: [{ ...MOVEMENT, consumption_type: "theft" }] }));
    await expect(listOrders()).rejects.toThrow("historial inválido");
    fetchMock.mockResolvedValueOnce(json({ inbound: [{ ...MOVEMENT, vendor_name: "x", created_at: "yesterday" }], outbound: [] }));
    await expect(listOrders()).rejects.toThrow("historial inválido");
  });
});

describe("createOutbound", () => {
  const payload = { supply_id: 1, quantity: 50, clinic_id: 2, consumption_type: "clinical_use" as const };

  it("records the created order", async () => {
    fetchMock.mockResolvedValue(json({ ...MOVEMENT, consumption_type: "clinical_use" }, 201));
    await createOutbound(payload);
    expect(track).toHaveBeenCalledWith("inventory_outbound_order_created", expect.objectContaining({ quantity: 50, country: "US" }));
  });

  it("shows the API's stock message and reports the available quantity", async () => {
    fetchMock.mockResolvedValue(json(
      { detail: "Insufficient stock for supply 'Gloves'. Available: 3, requested: 50." }, 400, { "X-Available-Stock": "3" },
    ));
    const error = await createOutbound(payload).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(InventoryError);
    expect((error as InventoryError).status).toBe(400);
    expect((error as InventoryError).message).toContain("Available: 3");
    expect(track).toHaveBeenCalledWith("inventory_outbound_insufficient_stock_rejected",
      expect.objectContaining({ requested_quantity: 50, available_quantity: 3 }));
  });

  it("maps network failures to a connection message", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(createOutbound(payload)).rejects.toThrow("No se pudo conectar con el servicio.");
  });
});
