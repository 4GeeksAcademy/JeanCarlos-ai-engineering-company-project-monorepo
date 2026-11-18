"use client";

import { listOrders } from "@/lib/inventory";
import { CONSUMPTION_LABELS, clinicLabel, quantityLabel } from "@/lib/inventory-labels";
import { useInventoryResource } from "@/lib/useInventoryResource";

const dateFormat = new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" });

/** Read-only history of supply deliveries and clinical consumptions. */
export default function OrdersPage() {
  const { data: orders, state, error, retry } = useInventoryResource(
    listOrders,
    "No se pudo cargar el historial de órdenes.",
  );

  const movements = orders ? [
    ...orders.inbound.map((order) => ({
      ...order, kind: "inbound" as const, detail: `Proveedor: ${order.vendor_name}`,
    })),
    ...orders.outbound.map((order) => ({
      ...order, kind: "outbound" as const, detail: CONSUMPTION_LABELS[order.consumption_type],
    })),
  ].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)) : [];

  return (
    <main>
      <h1>Historial de órdenes</h1>
      <p>Entregas de proveedor y consumos clínicos, de la más reciente a la más antigua. Solo lectura.</p>
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
              <thead>
                <tr>
                  <th>Tipo</th><th>Suministro médico</th><th>Cantidad</th><th>Fecha de creación</th>
                  <th>Clínica</th><th>Detalle</th><th>Creada por (user_uuid)</th>
                </tr>
              </thead>
              <tbody>
                {movements.map((order) => (
                  <tr key={`${order.kind}-${order.id}`} className={`order-row-${order.kind}`}>
                    <td>
                      <span className={`order-kind order-${order.kind}`}>
                        {order.kind === "inbound" ? "⬇ Entrada" : "⬆ Salida"}
                      </span>
                      <small>#{order.id}</small>
                    </td>
                    <th scope="row">{order.supply.name} <small>{order.supply.sku}</small></th>
                    <td className={`quantity-${order.kind}`}>
                      {order.kind === "inbound" ? "+" : "−"}{quantityLabel(order.quantity, order.supply.unit)}
                    </td>
                    <td><time dateTime={order.created_at}>{dateFormat.format(new Date(order.created_at))}</time></td>
                    <td>{clinicLabel(order.clinic_id)}</td>
                    <td>{order.detail}</td>
                    <td><code className="uuid">{order.user_uuid}</code></td>
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
