import Link from "next/link";

export default function InventoryLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <nav className="inventory-nav" aria-label="Navegación de inventario">
        <Link href="/suppliers">Proveedores</Link>
        <Link href="/inventory/products">Catálogo</Link>
        <Link href="/inventory/orders/inbound">Entrada</Link>
        <Link href="/inventory/orders/outbound">Salida</Link>
        <Link href="/inventory/orders">Historial</Link>
      </nav>
      {children}
    </>
  );
}
