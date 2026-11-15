"use client";

import Link from "next/link";
import { listProducts } from "@/lib/inventory";
import { useInventoryResource } from "@/lib/useInventoryResource";

const LOW_STOCK_THRESHOLD = 10;

export default function ProductsPage() {
  const { data: products, state, error, retry } = useInventoryResource(
    listProducts,
    "No se pudo cargar el catálogo.",
  );

  return (
    <main>
      <h1>Catálogo de inventario</h1>
      <p>Stock global. Se considera bajo cuando hay {LOW_STOCK_THRESHOLD} unidades o menos.</p>
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
              <thead><tr><th>Producto</th><th>SKU</th><th>Categoría</th><th>País</th><th>Stock</th><th>Acciones</th></tr></thead>
              <tbody>
                {products.map((product) => (
                  <tr key={product.id}>
                    <th scope="row">{product.name}</th>
                    <td>{product.sku}</td><td>{product.category}</td><td>{product.country}</td>
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
                {products.length === 0 && <tr><td colSpan={6}>No hay productos registrados.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
