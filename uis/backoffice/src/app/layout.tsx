import type { Metadata } from "next";
import { Suspense } from "react";
import { SessionBoundary } from "@healthcore/auth/components";
import TelemetryProvider from "@/components/TelemetryProvider";
import "./globals.css";
import "@healthcore/auth/styles.css";

export const metadata: Metadata = {
  title: "HealthCore | Directorio de proveedores",
  description: "Directorio interno de proveedores de HealthCore",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>
        <TelemetryProvider />
        <Suspense fallback={<p role="status">Comprobando sesión...</p>}>
          <SessionBoundary homePath="/suppliers">{children}</SessionBoundary>
        </Suspense>
      </body>
    </html>
  );
}
