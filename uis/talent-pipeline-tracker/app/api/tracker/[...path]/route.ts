import { sameOrigin, verifySession } from "@healthcore/auth/server";

async function proxy(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  if (request.method !== "GET" && !sameOrigin(request)) {
    return Response.json({ detail: "Origen no permitido." }, { status: 403 });
  }
  const rejected = await verifySession(request);
  if (rejected) return rejected;
  const { path } = await context.params;
  if (path[0] !== "records") {
    return Response.json({ detail: "Ruta no encontrada." }, { status: 404 });
  }
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) {
    console.error("Tracker API URL is not configured");
    return Response.json({ detail: "Falta configurar la API de candidaturas." }, { status: 503 });
  }
  try {
    const target = new URL(`${base.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}${new URL(request.url).search}`);
    if (!["http:", "https:"].includes(target.protocol)) {
      console.error("Tracker API URL has an invalid protocol");
      return Response.json({ detail: "Configuración inválida de la API." }, { status: 503 });
    }
    const headers = new Headers({ Accept: "application/json" });
    if (request.headers.has("content-type")) headers.set("Content-Type", "application/json");
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store",
      redirect: "manual",
    });
    if (upstream.status >= 300 && upstream.status < 400 || upstream.status >= 500) {
      console.error("Tracker API returned an unexpected status:", upstream.status);
      return Response.json({ detail: "Servicio de candidaturas no disponible." }, { status: 502 });
    }
    return new Response(upstream.status === 204 ? null : await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch (error) {
    console.error("Tracker API request failed:", error instanceof Error ? error.name : "UnknownError");
    return Response.json({ detail: "No se pudo conectar con la API de candidaturas." }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
