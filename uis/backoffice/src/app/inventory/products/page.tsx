"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { attemptDirectStockEdit, listExpiring, listProducts, useInventoryError } from "@/lib/inventory";
import { useInventoryResource } from "@/lib/useInventoryResource";
import type { Product, ProductCategory } from "@/types/inventory";

const LOW_STOCK_THRESHOLD = 10;
const categoryLabels: Record<ProductCategory, string> = {
  medication: "Medicamento",
  ppe: "EPP",
  consumable: "Consumible",
  equipment: "Equipo",
};

function ExpiringPanel() {
  const { data: expiring, state, error, retry } = useInventoryResource(
    listExpiring,
    "No se pudieron cargar los vencimientos.",
  );
  return (
    <section className="panel" aria-label="Insumos por vencer">
      <h2>Por vencer en 30 días</h2>
      {state === "loading" && <p role="status">Cargando vencimientos...</p>}
      {state === "error" && (
        <div className="error" role="alert">
          <p>{error}</p>
          <button type="button" onClick={retry}>Reintentar</button>
        </div>
      )}
      {state === "ready" && expiring !== null && (
        expiring.length === 0 ? <p>No hay insumos con stock que venzan en los próximos 30 días.</p> : (
          <ul>
            {expiring.map((item) => (
              <li key={`${item.clinic_id}-${item.supply.id}-${item.expiry_date}`}>
                <strong>{item.supply.name}</strong> · clínica {item.clinic_id} · {item.quantity} {item.supply.unit} ·
                vence el {item.expiry_date} ({item.days_until_expiry} días). Prioriza su uso o su baja controlada.
              </li>
            ))}
          </ul>
        )
      )}
    </section>
  );
}

function DirectStockEdit({ products }: { products: Product[] }) {
  const [supplyId, setSupplyId] = useState("");
  const [clinicId, setClinicId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const errorMessage = useInventoryError();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const product = products.find((item) => String(item.id) === supplyId);
    const clinic = Number(clinicId);
    const amount = Number(quantity);
    if (!product || !Number.isInteger(clinic) || clinic < 1 || clinic > 12) {
      setResult("Selecciona un producto y una clínica válida (1–12).");
      return;
    }
    setBusy(true);
    try {
      await attemptDirectStockEdit(product, clinic, Number.isInteger(amount) && amount >= 0 ? amount : null);
    } catch (error) {
      setResult(errorMessage(error, "No se pudo ajustar el stock."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-label="Ajuste directo de stock">
      <h2>Ajuste directo de stock</h2>
      <p>
        Por trazabilidad, el stock solo cambia mediante órdenes de{" "}
        <Link href="/inventory/orders/inbound">entrada</Link> o <Link href="/inventory/orders/outbound">salida</Link>.
        Los intentos de ajuste directo se rechazan y quedan registrados.
      </p>
      <form className="auth-form" onSubmit={submit}>
        <label>
          Producto
          <select required value={supplyId} onChange={(event) => setSupplyId(event.target.value)}>
            <option value="">Selecciona un producto</option>
            {products.map((product) => <option key={product.id} value={product.id}>{product.name} ({product.sku})</option>)}
          </select>
        </label>
        <label>ID de clínica<input type="number" min="1" max="12" step="1" required value={clinicId} onChange={(event) => setClinicId(event.target.value)} /></label>
        <label>Nuevo stock<input type="number" min="0" step="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <button disabled={busy}>{busy ? "Enviando..." : "Ajustar stock"}</button>
        {result && <p className="error" role="alert">{result}</p>}
      </form>
    </section>
  );
}

export default function ProductsPage() {
  const { data: products, state, error, retry } = useInventoryResource(
    listProducts,
    "No se pudo cargar el catálogo.",
  );

  return (
    <main>
      <h1>Catálogo de inventario</h1>
      <p>
        Stock global de todas las clínicas. El indicador «Stock bajo» aparece con {LOW_STOCK_THRESHOLD} unidades
        o menos; los mínimos de reposición se configuran por clínica.
      </p>
      <section className="panel" aria-label="Productos">
        {state === "loading" && <p role="status">Cargando productos...</p>}
        {state === "error" && (
          <div className="error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={retry}>Reintentar</button>
          </div>
        )}
        {state === "ready" && products !== null && (
          <div className="table-scroll" role="region" aria-label="Tabla de productos" tabIndex={0}>
            <table>
              <caption className="sr-only">Catálogo de productos</caption>
              <thead><tr><th>Producto</th><th>SKU</th><th>Categoría</th><th>País</th><th>Vence</th><th>Stock</th><th>Acciones</th></tr></thead>
              <tbody>
                {products.map((product) => (
                  <tr key={product.id}>
                    <th scope="row">{product.name}</th>
                    <td>{product.sku}</td>
                    <td>{product.product_category ? categoryLabels[product.product_category] : "Sin categorizar"}</td>
                    <td>{product.country}</td>
                    <td>{product.expiry_date ?? "—"}</td>
                    <td>
                      {product.current_stock} {product.unit}
                      {product.current_stock <= LOW_STOCK_THRESHOLD && <span className="badge suspended">Stock bajo</span>}
                    </td>
                    <td>
                      <Link href={`/inventory/orders/inbound?supply_id=${product.id}`}>Entrada</Link>{" · "}
                      <Link href={`/inventory/orders/outbound?supply_id=${product.id}`}>Salida</Link>
                    </td>
                  </tr>
                ))}
                {products.length === 0 && <tr><td colSpan={7}>No hay productos registrados.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <ExpiringPanel />
      {state === "ready" && products !== null && products.length > 0 && <DirectStockEdit products={products} />}
    </main>
  );
}
