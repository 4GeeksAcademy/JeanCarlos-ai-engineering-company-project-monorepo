import Link from "next/link";
import { requireSession } from "@/lib/server-api";

export default async function InventoryLayout({ children }: { children: React.ReactNode }) {
  await requireSession();
  return (
    <>
      <nav className="inventory-nav" aria-label="Navegación de inventario">
        <Link href="/suppliers">Proveedores</Link>
        <Link href="/inventory/products">Suministros médicos</Link>
        <Link href="/inventory/orders/inbound">Entrega de proveedor</Link>
        <Link href="/inventory/orders/outbound">Consumo clínico</Link>
        <Link href="/inventory/orders">Historial de órdenes</Link>
      </nav>
      {children}
    </>
  );
}
