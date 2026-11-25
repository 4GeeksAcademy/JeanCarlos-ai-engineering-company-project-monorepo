"use client";

import { listOrders } from "@/lib/inventory";
import { useInventoryResource } from "@/lib/useInventoryResource";
import { departments } from "@/types/inventory";

export default function OrdersPage() {
  const { data: orders, state, error, retry } = useInventoryResource(
    listOrders,
    "No se pudo cargar el historial.",
  );

  const movements = orders ? [
    ...orders.inbound.map((order) => ({ ...order, kind: "Entrada", detail: order.vendor_name })),
    ...orders.outbound.map((order) => ({
      ...order,
      kind: "Salida",
      detail: `${order.consumption_type === "clinical_use" ? "Uso clínico" : "Caducidad o desperdicio"}` +
        (order.department ? ` · ${departments.find((item) => item.value === order.department)?.label}` : ""),
    })),
  ].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)) : [];

  return (
    <main>
      <h1>Historial de órdenes</h1>
      <section className="panel" aria-label="Historial de inventario">
        {state === "loading" && <p role="status">Cargando historial...</p>}
        {state === "error" && (
          <div className="error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={retry}>Reintentar</button>
          </div>
        )}
        {state === "ready" && (
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
