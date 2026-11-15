import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export const SESSION_COOKIE = "healthcore_session";

export function apiUrl(path: string): string {
  const base = process.env.SUPPLIERS_API_URL ?? "http://127.0.0.1:8000";
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
    console.error("Could not verify session:", error);
    throw new Error("No se pudo verificar la sesión.");
  }
  if (response.status === 401) redirect("/login");
  if (!response.ok) throw new Error("No se pudo verificar la sesión.");
}
