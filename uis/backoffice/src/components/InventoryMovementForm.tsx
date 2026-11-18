"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import {
  createInbound, createOutbound, getProduct, InventoryError, listProducts, stockLevel, useInventoryError,
} from "@/lib/inventory";
import {
  CLINIC_IDS, CONSUMPTION_LABELS, UNIT_LABELS, clinicLabel, quantityLabel,
} from "@/lib/inventory-labels";
import type { OutboundOrder, Product } from "@/types/inventory";
import { useTelemetryFlow } from "@/lib/useTelemetryFlow";

type Direction = "inbound" | "outbound";
type FieldErrors = Partial<Record<"supply" | "quantity" | "clinic" | "vendor", string>>;
type SelectedStock =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "ready"; product: Product }
  | { state: "error"; message: string };

const COPY = {
  inbound: {
    title: "Registrar entrega de proveedor",
    intro: "Suministros recibidos de un proveedor en una clínica HealthCore.",
    submit: "Registrar entrega",
    done: "Entrega registrada",
  },
  outbound: {
    title: "Registrar consumo clínico",
    intro: "Suministros usados en la atención al paciente o desechados por caducidad.",
    submit: "Registrar consumo",
    done: "Consumo registrado",
  },
} as const;

export default function InventoryMovementForm({
  direction,
  initialSupplyId,
}: {
  direction: Direction;
  initialSupplyId: string;
}) {
  const copy = COPY[direction];
  const [products, setProducts] = useState<Product[]>([]);
  const [listState, setListState] = useState<"loading" | "ready" | "error">("loading");
  const [listError, setListError] = useState("");
  const [reloadList, setReloadList] = useState(0);
  const [supplyId, setSupplyId] = useState(initialSupplyId);
  const [selected, setSelected] = useState<SelectedStock>(
    initialSupplyId ? { state: "loading" } : { state: "idle" },
  );
  const [reloadStock, setReloadStock] = useState(0);
  const [quantity, setQuantity] = useState("");
  const [clinicId, setClinicId] = useState("");
  const [vendor, setVendor] = useState("");
  const [consumptionType, setConsumptionType] = useState<OutboundOrder["consumption_type"]>("clinical_use");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const errorMessage = useInventoryError();
  const telemetryFlow = useTelemetryFlow(direction === "inbound" ? "inventory_inbound" : "inventory_outbound");

  // Supplies for the selector (by name, never a raw id).
  useEffect(() => {
    const controller = new AbortController();
    listProducts(controller.signal)
      .then((result) => { setProducts(result); setListState("ready"); })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setListError(errorMessage(error, "No se pudieron cargar los suministros médicos."));
        setListState("error");
      });
    return () => controller.abort();
  }, [reloadList, errorMessage]);

  // Current stock of the selected supply, fetched again whenever the selection changes.
  useEffect(() => {
    if (!supplyId) return;
    const controller = new AbortController();
    getProduct(Number(supplyId), controller.signal)
      .then((product) => setSelected({ state: "ready", product }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setSelected({ state: "error", message: errorMessage(error, "No se pudo consultar el stock.") });
        }
      });
    return () => controller.abort();
  }, [supplyId, reloadStock, errorMessage]);

  function selectSupply(value: string) {
    setSupplyId(value);
    setSelected(value ? { state: "loading" } : { state: "idle" });
    setFieldErrors((current) => ({ ...current, supply: undefined, quantity: undefined }));
    telemetryFlow.productSelected();
  }

  function refreshStock() {
    if (supplyId) {
      setSelected({ state: "loading" });
      setReloadStock((n) => n + 1);
    }
  }

  // Selected supply with the stock just fetched from GET /inventory/products/{id}.
  const selectedProduct = selected.state === "ready" ? selected.product : null;
  const amount = Number(quantity);
  // Client-side guard: an outbound quantity above the available stock cannot be
  // submitted. The API still enforces the rule (HTTP 400) if stock changes meanwhile.
  const exceedsStock = direction === "outbound" && selectedProduct !== null && quantity !== ""
    && Number.isInteger(amount) && amount > selectedProduct.current_stock;

  function validate(): FieldErrors {
    const errors: FieldErrors = {};
    if (!supplyId) errors.supply = "Selecciona un suministro médico.";
    if (!Number.isInteger(amount) || amount < 1 || amount > 2147483647) {
      errors.quantity = "Indica una cantidad entera mayor que 0.";
    }
    if (!clinicId) errors.clinic = "Selecciona la clínica.";
    if (direction === "inbound" && !vendor.trim()) errors.vendor = "Indica el proveedor que hizo la entrega.";
    return errors;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");
    setMessage("");
    const errors = validate();
    if (exceedsStock && selectedProduct) {
      errors.quantity = `La cantidad supera el stock disponible (${quantityLabel(selectedProduct.current_stock, selectedProduct.unit)}).`;
    }
    setFieldErrors(errors);
    if (Object.keys(errors).length) {
      telemetryFlow.invalid();
      return;
    }
    setBusy(true);
    telemetryFlow.submitting();
    const supplyName = selectedProduct?.name ?? products.find((p) => String(p.id) === supplyId)?.name ?? "el suministro";
    const unit = selectedProduct?.unit ?? products.find((p) => String(p.id) === supplyId)?.unit ?? "unit";
    try {
      const movement = { supply_id: Number(supplyId), quantity: amount, clinic_id: Number(clinicId) };
      if (direction === "inbound") {
        await createInbound({ ...movement, vendor_name: vendor.trim() });
      } else {
        await createOutbound({ ...movement, consumption_type: consumptionType });
      }
      telemetryFlow.completed();
      setMessage(`${copy.done}: ${quantityLabel(amount, unit)} de ${supplyName} en ${clinicLabel(Number(clinicId))}`);
      // Clear the whole form for the next entry.
      setSupplyId("");
      setSelected({ state: "idle" });
      setQuantity("");
      setClinicId("");
      setVendor("");
      setConsumptionType("clinical_use");
      setFieldErrors({});
      setReloadList((n) => n + 1);
    } catch (error) {
      const detail = errorMessage(error, "No se pudo registrar el movimiento.");
      if (error instanceof InventoryError && error.status === 400 && direction === "outbound") {
        // Insufficient stock: show the API message next to the quantity field.
        setFieldErrors({ quantity: `Stock insuficiente. ${detail}` });
        refreshStock();
      } else {
        setFormError(`${detail} No se registró el movimiento; revisa los datos y vuelve a intentarlo.`);
      }
    } finally {
      telemetryFlow.settled();
      setBusy(false);
    }
  }

  const describedBy = (field: keyof FieldErrors, extra?: string) =>
    [extra, fieldErrors[field] ? `${field}-error` : undefined].filter(Boolean).join(" ") || undefined;

  return (
    <main>
      <h1>{copy.title}</h1>
      <p>{copy.intro}</p>
      <section className="panel">
        {message && <p className="success notice-box" role="status">✅ {message} <Link href="/inventory/orders">Ver historial</Link></p>}
        {formError && <p className="error notice-box" role="alert">{formError}</p>}
        {listState === "loading" && <p role="status">Cargando suministros médicos...</p>}
        {listState === "error" && (
          <div className="error" role="alert">
            <p>{listError}</p>
            <button type="button" onClick={() => { setListState("loading"); setReloadList((n) => n + 1); }}>Reintentar</button>
          </div>
        )}
        {listState === "ready" && (
          <form className="auth-form" onSubmit={submit} noValidate onChangeCapture={telemetryFlow.changed} onInvalid={telemetryFlow.invalid}>
            <label>
              Suministro médico
              <select
                name="supply_id" value={supplyId} aria-invalid={Boolean(fieldErrors.supply)}
                aria-describedby={describedBy("supply", "stock-status")}
                onChange={(event) => selectSupply(event.target.value)}
              >
                <option value="">Selecciona un suministro</option>
                {products.map((product) => (
                  <option key={product.id} value={product.id}>{product.name} ({product.sku})</option>
                ))}
              </select>
              {fieldErrors.supply && <span className="field-error" id="supply-error">{fieldErrors.supply}</span>}
            </label>

            <div id="stock-status" className="stock-status" aria-live="polite">
              {selected.state === "loading" && <span>Consultando stock actual…</span>}
              {selected.state === "ready" && (
                <span className={`stock-level stock-${stockLevel(selected.product.current_stock)}`}>
                  Stock actual disponible: {quantityLabel(selected.product.current_stock, selected.product.unit)}
                </span>
              )}
              {selected.state === "error" && (
                <span className="error">
                  {selected.message} <button type="button" className="link-button" onClick={refreshStock}>Reintentar</button>
                </span>
              )}
            </div>

            <label>
              Cantidad{selectedProduct ? ` (${UNIT_LABELS[selectedProduct.unit]})` : ""}
              <input
                name="quantity" type="number" min="1" step="1" inputMode="numeric" value={quantity}
                aria-invalid={Boolean(fieldErrors.quantity) || exceedsStock}
                aria-describedby={describedBy("quantity", exceedsStock ? "quantity-warning" : undefined)}
                onChange={(event) => {
                  setQuantity(event.target.value);
                  setFieldErrors((current) => ({ ...current, quantity: undefined }));
                }}
              />
              {exceedsStock && selectedProduct && (
                <span className="field-warning" id="quantity-warning" role="alert">
                  ⚠️ La cantidad supera el stock disponible
                  ({quantityLabel(selectedProduct.current_stock, selectedProduct.unit)}).
                  Reduce la cantidad para poder registrar el consumo.
                </span>
              )}
              {fieldErrors.quantity && <span className="field-error" id="quantity-error" role="alert">{fieldErrors.quantity}</span>}
            </label>

            <label>
              Clínica
              <select
                name="clinic_id" value={clinicId} aria-invalid={Boolean(fieldErrors.clinic)}
                aria-describedby={describedBy("clinic")} onChange={(event) => setClinicId(event.target.value)}
              >
                <option value="">Selecciona la clínica</option>
                {CLINIC_IDS.map((id) => <option key={id} value={id}>{clinicLabel(id)}</option>)}
              </select>
              {fieldErrors.clinic && <span className="field-error" id="clinic-error">{fieldErrors.clinic}</span>}
            </label>

            {direction === "inbound" ? (
              <label>
                Proveedor
                <input
                  name="vendor_name" value={vendor} placeholder="Ej.: MedLine Industries"
                  aria-invalid={Boolean(fieldErrors.vendor)} aria-describedby={describedBy("vendor")}
                  onChange={(event) => setVendor(event.target.value)}
                />
                {fieldErrors.vendor && <span className="field-error" id="vendor-error">{fieldErrors.vendor}</span>}
              </label>
            ) : (
              <label>
                Tipo de consumo
                <select
                  name="consumption_type" value={consumptionType}
                  onChange={(event) => {
                    if (event.target.value === "clinical_use" || event.target.value === "expiry_waste") {
                      setConsumptionType(event.target.value);
                    }
                  }}
                >
                  {(Object.keys(CONSUMPTION_LABELS) as OutboundOrder["consumption_type"][]).map((type) => (
                    <option key={type} value={type}>{CONSUMPTION_LABELS[type]}</option>
                  ))}
                </select>
              </label>
            )}

            <button
              disabled={busy || products.length === 0 || exceedsStock}
              aria-describedby={exceedsStock ? "quantity-warning" : undefined}
            >
              {busy ? "Guardando…" : copy.submit}
            </button>
            {products.length === 0 && <p>No hay suministros médicos registrados.</p>}
          </form>
        )}
      </section>
    </main>
  );
}
