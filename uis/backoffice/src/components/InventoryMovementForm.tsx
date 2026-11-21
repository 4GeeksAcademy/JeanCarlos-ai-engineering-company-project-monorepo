"use client";

import { useEffect, useState, type FormEvent } from "react";
import { createInbound, createOutbound, InventoryError, listProducts, useInventoryError } from "@/lib/inventory";
import { departments, type Department, type Product } from "@/types/inventory";
import { useTelemetryFlow } from "@/lib/useTelemetryFlow";

export default function InventoryMovementForm({
  direction,
  initialSupplyId,
}: {
  direction: "inbound" | "outbound";
  initialSupplyId: string;
}) {
  const [products, setProducts] = useState<Product[]>([]);
  const [supplyId, setSupplyId] = useState(initialSupplyId);
  const [quantity, setQuantity] = useState("");
  const [clinicId, setClinicId] = useState("");
  const [vendor, setVendor] = useState("");
  const [consumptionType, setConsumptionType] = useState<"clinical_use" | "expiry_waste">("clinical_use");
  const [department, setDepartment] = useState<Department | "">("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const errorMessage = useInventoryError();
  const telemetryFlow = useTelemetryFlow(direction === "inbound" ? "inventory_inbound" : "inventory_outbound");

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const result = await listProducts(controller.signal);
        if (!controller.signal.aborted) {
          setProducts(result);
          setState("ready");
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setError(errorMessage(error, "No se pudieron cargar los productos."));
          setState("error");
        }
      }
    }
    void load();
    return () => controller.abort();
  }, [reload, errorMessage]);

  const selectedProduct = products.find((product) => String(product.id) === supplyId);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const amount = Number(quantity);
    const clinic = Number(clinicId);
    if (!selectedProduct || !Number.isInteger(amount) || amount < 1 || amount > 2147483647 ||
      !Number.isInteger(clinic) || clinic < 1 || clinic > 12 || (direction === "inbound" && !vendor.trim()) ||
      (direction === "outbound" && !department)) {
      setError("Comprueba el producto, la cantidad, la clínica, el departamento y los demás campos obligatorios.");
      telemetryFlow.invalid();
      return;
    }
    setBusy(true);
    telemetryFlow.submitting();
    setError("");
    setMessage("");
    try {
      const movement = { supply_id: selectedProduct.id, quantity: amount, clinic_id: clinic };
      let success = "Entrada registrada.";
      if (direction === "inbound") {
        await createInbound({ ...movement, vendor_name: vendor.trim() });
      } else if (department) {
        const { thresholdCrossed } = await createOutbound({ ...movement, consumption_type: consumptionType, department });
        success = thresholdCrossed
          ? `Salida registrada. La clínica ${clinic} queda con ${thresholdCrossed.clinicStock} ${selectedProduct.unit}, por debajo de su mínimo de ${thresholdCrossed.minimum}: requiere reabastecimiento.`
          : "Salida registrada.";
      }
      telemetryFlow.completed();
      setQuantity("");
      setMessage(success);
      try {
        setProducts(await listProducts());
      } catch (error) {
        setError(`Movimiento registrado, pero no se pudo actualizar el stock: ${errorMessage(error, "error desconocido")}`);
        setState("error");
      }
    } catch (error) {
      const detail = errorMessage(error, "No se pudo registrar el movimiento.");
      if (error instanceof InventoryError && error.status === 400 && direction === "outbound") {
        setError(`Stock insuficiente: ${detail}`);
        try {
          setProducts(await listProducts());
        } catch (refreshError) {
          setError(`${detail} No se pudo actualizar el stock: ${errorMessage(refreshError, "error desconocido")}`);
          setState("error");
        }
      } else {
        setError(`${detail} Comprueba el historial antes de volver a enviar.`);
      }
    } finally {
      telemetryFlow.settled();
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>{direction === "inbound" ? "Registrar entrada" : "Registrar salida"}</h1>
      <section className="panel">
        {state === "loading" && <p role="status">Cargando productos...</p>}
        {message && <p className="success" role="status">{message}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        {state === "error" && (
          <button type="button" onClick={() => { setError(""); setState("loading"); setReload((n) => n + 1); }}>Reintentar carga</button>
        )}
        {state === "ready" && (
          <form className="auth-form" onSubmit={submit} onChangeCapture={telemetryFlow.changed} onInvalid={telemetryFlow.invalid}>
            <label>
              Producto
              <select required value={selectedProduct ? supplyId : ""} onChange={(event) => {
                setSupplyId(event.target.value);
                telemetryFlow.productSelected();
              }}>
                <option value="">Selecciona un producto</option>
                {products.map((product) => <option key={product.id} value={product.id}>{product.name} ({product.sku})</option>)}
              </select>
            </label>
            {direction === "outbound" && selectedProduct && (
              <p role="status">Stock global: {selectedProduct.current_stock} {selectedProduct.unit}. La salida se descuenta del stock de la clínica indicada.</p>
            )}
            <label>Cantidad<input type="number" min="1" max="2147483647" step="1" required value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
            <label>ID de clínica<input type="number" min="1" max="12" step="1" required value={clinicId} onChange={(event) => setClinicId(event.target.value)} /></label>
            {direction === "inbound" ? (
              <label>Proveedor<input required value={vendor} onChange={(event) => setVendor(event.target.value)} /></label>
            ) : (
              <label>
                Tipo de consumo
                <select value={consumptionType} onChange={(event) => {
                  if (event.target.value === "clinical_use" || event.target.value === "expiry_waste") {
                    setConsumptionType(event.target.value);
                  }
                }}>
                  <option value="clinical_use">Uso clínico</option>
                  <option value="expiry_waste">Caducidad o desperdicio</option>
                </select>
              </label>
            )}
            {direction === "outbound" && (
              <label>
                Departamento
                <select required value={department} onChange={(event) => {
                  const match = departments.find((item) => item.value === event.target.value);
                  setDepartment(match ? match.value : "");
                }}>
                  <option value="">Selecciona un departamento</option>
                  {departments.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
            )}
            <button disabled={busy || products.length === 0}>{busy ? "Guardando..." : "Registrar movimiento"}</button>
            {products.length === 0 && <p>No hay productos disponibles.</p>}
          </form>
        )}
      </section>
    </main>
  );
}
