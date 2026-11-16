import type { Metadata } from "next";
import { Suspense } from "react";
import { SessionBoundary } from "@healthcore/auth/components";
import "./globals.css";
import "@healthcore/auth/styles.css";

export const metadata: Metadata = {
  title: "Pipeline de talento | People & Talent",
  description: "Herramienta interna para gestionar candidaturas y etapas de selección.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es" className="h-full antialiased">
      <body className="min-h-full">
        <Suspense fallback={<p role="status">Comprobando sesión...</p>}>
          <SessionBoundary homePath="/">{children}</SessionBoundary>
        </Suspense>
      </body>
    </html>
  );
}
