import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { apiUrl, sameOrigin, SESSION_COOKIE } from "@/lib/server-api";

async function proxy(
  request: Request,
  context: { params: Promise<{ path?: string[] }> },
) {
  if (request.method !== "GET" && !sameOrigin(request)) {
    return NextResponse.json({ detail: "Origen no permitido." }, { status: 403 });
  }
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Sesión requerida." }, { status: 401 });
  const { path = [] } = await context.params;
  const suffix = path.length ? `/${path.map(encodeURIComponent).join("/")}` : "";
  const query = new URL(request.url).search;
  const headers = new Headers({ Authorization: `Bearer ${token}` });
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  let upstream: Response;
  try {
    upstream = await fetch(apiUrl(`/suppliers${suffix}${query}`), {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store",
      redirect: "manual",
    });
  } catch (error) {
    console.error("Supplier API request failed:", error);
    return NextResponse.json({ detail: "Servicio no disponible." }, { status: 502 });
  }
  if (upstream.status >= 300 && upstream.status < 400) {
    console.error("Unexpected redirect from supplier API");
    return NextResponse.json({ detail: "Respuesta inválida." }, { status: 502 });
  }
  return new NextResponse(upstream.status === 204 ? null : await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
