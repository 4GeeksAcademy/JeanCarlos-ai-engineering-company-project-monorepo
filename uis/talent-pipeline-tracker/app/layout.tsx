import type { Metadata } from "next";
import { DM_Sans, Space_Grotesk } from "next/font/google";
import "./globals.css";

// Self-hosted at build time by next/font: no render-blocking request to
// fonts.googleapis.com, the files are preloaded, and the size-adjusted fallback
// keeps text metrics stable when the web font arrives (performance audit).
const dmSans = DM_Sans({
  subsets: ["latin"], weight: ["400", "500", "600", "700"], display: "swap", variable: "--font-dm-sans",
});
const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"], weight: ["500", "600", "700"], display: "swap", variable: "--font-space-grotesk",
});

export const metadata: Metadata = {
  title: "Pipeline de talento | People & Talent",
  description: "Herramienta interna para gestionar candidaturas y etapas de selección.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es" className={`${dmSans.variable} ${spaceGrotesk.variable} h-full antialiased`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
