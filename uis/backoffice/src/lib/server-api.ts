import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export const SESSION_COOKIE = "healthcore_session";

export function apiUrl(path: string): string {
  // /inventory may live on its own service (NEXT_PUBLIC_INVENTORY_API_URL in .env.local).
  // It is only read here, on the server: the browser never calls the API directly.
  const inventoryBase = path.startsWith("/inventory")
    ? process.env.NEXT_PUBLIC_INVENTORY_API_URL || process.env.INVENTORY_API_URL
    : undefined;
  const base = inventoryBase || process.env.SUPPLIERS_API_URL || "http://127.0.0.1:8000";
  return `${base.replace(/\/$/, "")}${path}`;
}

export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  const protocol = request.headers.get("x-forwarded-proto") ??
    new URL(request.url).protocol.slice(0, -1);
  try {
    const parsed = new URL(origin);
    return parsed.origin === origin && parsed.host === host.toLowerCase() &&
      parsed.protocol === `${protocol}:`;
  } catch {
    return false;
  }
}

export async function requireSession(): Promise<void> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");
  let response: Response;
  try {
    response = await fetch(apiUrl("/me"), {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      redirect: "manual",
    });
  } catch (error) {
    console.error("Could not verify session:", error instanceof Error ? error.name : "UnknownError");
    throw new Error("No se pudo verificar la sesión.");
  }
  if (response.status === 401) redirect("/login");
  if (!response.ok) throw new Error("No se pudo verificar la sesión.");
}
