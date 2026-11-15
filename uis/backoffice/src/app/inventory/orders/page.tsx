"use client";

import { useEffect, useState } from "react";
import { listOrders, useInventoryError } from "@/lib/inventory";
import type { Orders } from "@/types/inventory";

export default function OrdersPage() {
  const [orders, setOrders] = useState<Orders | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const errorMessage = useInventoryError();

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const result = await listOrders(controller.signal);
        if (!controller.signal.aborted) setOrders(result);
      } catch (error) {
        if (!controller.signal.aborted) setError(errorMessage(error, "No se pudo cargar el historial."));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [reload, errorMessage]);

  const movements = orders ? [
    ...orders.inbound.map((order) => ({ ...order, kind: "Entrada", detail: order.vendor_name })),
    ...orders.outbound.map((order) => ({
      ...order,
      kind: "Salida",
      detail: order.consumption_type === "clinical_use" ? "Uso clínico" : "Caducidad o desperdicio",
    })),
  ].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)) : [];

  return (
    <main>
      <h1>Historial de órdenes</h1>
      <section className="panel" aria-label="Historial de inventario">
        {loading && <p role="status">Cargando historial...</p>}
        {error && (
          <div className="error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={() => { setError(""); setLoading(true); setReload((n) => n + 1); }}>Reintentar</button>
          </div>
        )}
        {!loading && !error && (
          <div className="table-scroll" role="region" aria-label="Tabla de órdenes" tabIndex={0}>
            <table>
              <caption className="sr-only">Movimientos de inventario</caption>
              <thead><tr><th>Fecha</th><th>Tipo</th><th>Producto</th><th>Cantidad</th><th>Clínica</th><th>Detalle</th><th>Usuario</th></tr></thead>
              <tbody>
                {movements.map((order) => (
                  <tr key={`${order.kind}-${order.id}`}>
                    <td>{new Date(order.created_at).toLocaleString("es")}</td>
                    <td>{order.kind} #{order.id}</td>
                    <th scope="row">{order.supply.name} ({order.supply.sku})</th>
                    <td>{order.quantity} {order.supply.unit}</td><td>{order.clinic_id}</td>
                    <td>{order.detail}</td><td>{order.user_uuid}</td>
                  </tr>
                ))}
                {movements.length === 0 && <tr><td colSpan={7}>No hay órdenes registradas.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
