import Link from "next/link";
import { requireSession } from "@/lib/server-api";

export default async function InventoryLayout({ children }: { children: React.ReactNode }) {
  await requireSession();
  return (
    <>
      <nav className="inventory-nav" aria-label="Navegación de inventario">
        <Link href="/suppliers">Proveedores</Link>
        <Link href="/inventory/products">Catálogo</Link>
        <Link href="/inventory/orders/inbound">Entrada</Link>
        <Link href="/inventory/orders/outbound">Salida</Link>
        <Link href="/inventory/orders">Historial</Link>
        <Link href="/reporting">Reporte mensual</Link>
        <Link href="/telemetry">Telemetría</Link>
        <Link href="/knowledge">Asistente</Link>
      </nav>
      {children}
    </>
  );
}
