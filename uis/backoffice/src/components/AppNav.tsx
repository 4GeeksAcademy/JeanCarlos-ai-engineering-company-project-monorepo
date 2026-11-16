"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/suppliers", label: "Proveedores" },
  { href: "/inventory/products", label: "Inventario" },
  { href: "/incidents", label: "Incidents" },
  { href: "/incidents/new", label: "Report incident" },
  { href: "/incidents/summary", label: "Incident summary" },
];

/** Application menu shared by the protected sections. */
export default function AppNav() {
  const pathname = usePathname();
  return (
    <nav className="app-nav" aria-label="Main menu">
      {LINKS.map((link) => (
        <Link key={link.href} href={link.href} aria-current={pathname === link.href ? "page" : undefined}>
          {link.label}
        </Link>
      ))}
    </nav>
  );
}
