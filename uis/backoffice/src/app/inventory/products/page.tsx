"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { listProducts, useInventoryError } from "@/lib/inventory";
import type { Product } from "@/types/inventory";

const LOW_STOCK_THRESHOLD = 10;

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const errorMessage = useInventoryError();

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
          setError(errorMessage(error, "No se pudo cargar el catálogo."));
          setState("error");
        }
      }
    }
    void load();
    return () => controller.abort();
  }, [reload, errorMessage]);

  return (
    <main>
      <h1>Catálogo de inventario</h1>
      <p>Stock global. Se considera bajo cuando hay {LOW_STOCK_THRESHOLD} unidades o menos.</p>
      <section className="panel" aria-label="Productos">
        {state === "loading" && <p role="status">Cargando productos...</p>}
        {state === "error" && (
          <div className="error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={() => { setState("loading"); setReload((n) => n + 1); }}>Reintentar</button>
          </div>
        )}
        {state === "ready" && (
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
