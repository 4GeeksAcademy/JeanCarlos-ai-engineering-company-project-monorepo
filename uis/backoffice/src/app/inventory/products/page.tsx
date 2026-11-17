"use client";

import Link from "next/link";
import { LOW_STOCK_THRESHOLD, listProducts, stockLevel, type StockLevel } from "@/lib/inventory";
import { CATEGORY_LABELS, COUNTRY_LABELS, quantityLabel } from "@/lib/inventory-labels";
import { useInventoryResource } from "@/lib/useInventoryResource";

// Colour + icon + text: the level is readable without relying on colour alone.
const LEVELS: Record<StockLevel, { icon: string; label: string }> = {
  out: { icon: "⛔", label: "Agotado" },
  low: { icon: "⚠️", label: "Stock bajo" },
  ok: { icon: "✅", label: "Stock suficiente" },
};

export default function ProductsPage() {
  const { data: products, state, error, retry } = useInventoryResource(
    listProducts,
    "No se pudieron cargar los suministros médicos.",
  );
  const lowCount = products?.filter((product) => stockLevel(product.current_stock) !== "ok").length ?? 0;

  return (
    <main>
      <h1>Suministros médicos</h1>
      <p>Stock global de cada suministro: entregas de proveedor menos consumos clínicos.</p>
      <ul className="stock-legend" aria-label="Niveles de stock">
        <li><span className="stock-level stock-ok">{LEVELS.ok.icon} {LEVELS.ok.label}</span> más de {LOW_STOCK_THRESHOLD}</li>
        <li><span className="stock-level stock-low">{LEVELS.low.icon} {LEVELS.low.label}</span> de 1 a {LOW_STOCK_THRESHOLD}</li>
        <li><span className="stock-level stock-out">{LEVELS.out.icon} {LEVELS.out.label}</span> 0</li>
      </ul>
      <section className="panel" aria-label="Suministros médicos">
        {state === "loading" && <p role="status">Cargando suministros médicos...</p>}
        {state === "error" && (
          <div className="error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={retry}>Reintentar</button>
          </div>
        )}
        {state === "ready" && products !== null && (
          <>
            {lowCount > 0 && (
              <p className="stock-alert" role="status">
                {LEVELS.low.icon} {lowCount} suministro{lowCount === 1 ? "" : "s"} necesita{lowCount === 1 ? "" : "n"} reposición.
              </p>
            )}
            <div className="table-scroll" role="region" aria-label="Tabla de suministros médicos" tabIndex={0}>
              <table>
                <caption className="sr-only">Suministros médicos con su stock actual</caption>
                <thead>
                  <tr><th>Suministro</th><th>SKU</th><th>Categoría</th><th>País</th><th>Stock actual</th><th>Registrar</th></tr>
                </thead>
                <tbody>
                  {products.map((product) => {
                    const level = stockLevel(product.current_stock);
                    return (
                      <tr key={product.id} className={`stock-row-${level}`}>
                        <th scope="row">{product.name}</th>
                        <td>{product.sku}</td>
                        <td>{CATEGORY_LABELS[product.category]}</td>
                        <td>{COUNTRY_LABELS[product.country]}</td>
                        <td>
                          <strong>{quantityLabel(product.current_stock, product.unit)}</strong>
                          <span className={`stock-level stock-${level}`}>{LEVELS[level].icon} {LEVELS[level].label}</span>
                        </td>
                        <td className="row-actions">
                          <Link className="action-link" href={`/inventory/orders/inbound?supply_id=${product.id}`}>
                            ⬇ Entrega de proveedor
                          </Link>
                          {level === "out" ? (
                            <span className="action-disabled" title="Sin stock para registrar consumos">⬆ Consumo clínico</span>
                          ) : (
                            <Link className="action-link" href={`/inventory/orders/outbound?supply_id=${product.id}`}>
                              ⬆ Consumo clínico
                            </Link>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {products.length === 0 && (
                    <tr><td colSpan={6}>No hay suministros médicos registrados.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
