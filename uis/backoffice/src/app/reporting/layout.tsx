import Link from "next/link";
import { requireSession } from "@/lib/server-api";

export default async function ReportingLayout({ children }: { children: React.ReactNode }) {
  await requireSession();
  return (
    <>
      <nav className="inventory-nav" aria-label="Navegación">
        <Link href="/suppliers">Proveedores</Link>
        <Link href="/inventory/products">Catálogo</Link>
        <Link href="/inventory/orders">Historial</Link>
        <Link href="/reporting">Reporte mensual</Link>
        <Link href="/telemetry">Telemetría</Link>
        <Link href="/knowledge">Asistente</Link>
        <Link href="/rfp">RFPs</Link>
      </nav>
      {children}
    </>
  );
}
