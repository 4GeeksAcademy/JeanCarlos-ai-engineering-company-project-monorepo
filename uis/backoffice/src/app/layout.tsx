import type { Metadata } from "next";
import "./globals.css";
import TelemetryProvider from "@/components/TelemetryProvider";

export const metadata: Metadata = {
  title: "HealthCore | Directorio de proveedores",
  description: "Directorio interno de proveedores de HealthCore",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body><TelemetryProvider />{children}</body>
    </html>
  );
}
