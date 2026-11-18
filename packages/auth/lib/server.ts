import "server-only";

// Telemetry correlation (X-Request-ID) and inventory stock hints travel through the proxy.
const REQUEST_ID = /^[a-f0-9-]{36}$/i;
const FORWARDED_RESPONSE_HEADERS = ["X-Request-ID", "X-Available-Stock"];

export function apiUrl(path: string): string {
  const base = process.env.AUTH_API_URL ?? process.env.SUPPLIERS_API_URL ?? "http://127.0.0.1:8000";
  return `${base.replace(/\/$/, "")}${path}`;
}

export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  const protocol = request.headers.get("x-forwarded-proto") ?? new URL(request.url).protocol.slice(0, -1);
  try {
    const parsed = new URL(origin);
    return parsed.origin === origin && parsed.host === host.toLowerCase() && parsed.protocol === `${protocol}:`;
  } catch {
    return false;
  }
}

export async function forwardApi(request: Request, path: string, authenticated: boolean): Promise<Response> {
  if (request.method !== "GET" && !sameOrigin(request)) {
    return Response.json({ detail: "Origen no permitido." }, { status: 403 });
  }
  const headers = new Headers({ Accept: "application/json" });
  if (authenticated) {
    const authorization = request.headers.get("authorization");
    if (!authorization || !/^Bearer \S+$/i.test(authorization)) {
      return Response.json({ detail: "Sesión requerida." }, { status: 401 });
    }
    headers.set("Authorization", authorization);
  }
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  const requestId = request.headers.get("x-request-id");
  if (requestId && REQUEST_ID.test(requestId)) headers.set("X-Request-ID", requestId);
  try {
    const upstream = await fetch(apiUrl(path), {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store",
      redirect: "manual",
    });
    if (upstream.status >= 300 && upstream.status < 400 || upstream.status >= 500) {
      console.error("HealthCore API returned an unexpected status:", upstream.status);
      return Response.json({ detail: "Servicio no disponible." }, { status: 502 });
    }
    const responseHeaders = new Headers({
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
    });
    for (const name of FORWARDED_RESPONSE_HEADERS) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(upstream.status === 204 ? null : await upstream.text(), {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("HealthCore API request failed:", error instanceof Error ? error.name : "UnknownError");
    return Response.json({ detail: "No se pudo conectar con el servicio." }, { status: 502 });
  }
}

const accountRoutes: Record<string, { method: string; path: string; authenticated: boolean }> = {
  login: { method: "POST", path: "/auth/login", authenticated: false },
  register: { method: "POST", path: "/users", authenticated: false },
  me: { method: "GET", path: "/auth/me", authenticated: true },
  profile: { method: "PUT", path: "/profiles/me", authenticated: true },
};

export async function accountProxy(
  request: Request,
  context: { params: Promise<{ action: string }> },
): Promise<Response> {
  const { action } = await context.params;
  if (!Object.hasOwn(accountRoutes, action)) {
    return Response.json({ detail: "Ruta no encontrada." }, { status: 404 });
  }
  const route = accountRoutes[action];
  if (request.method !== route.method) {
    return Response.json({ detail: "Método no permitido." }, {
      status: 405, headers: { Allow: route.method },
    });
  }
  return await forwardApi(request, route.path, route.authenticated);
}

export async function verifySession(request: Request): Promise<Response | null> {
  const validation = await forwardApi(new Request(request.url, {
    headers: { Authorization: request.headers.get("authorization") ?? "" },
  }), "/auth/me", true);
  return validation.ok ? null : validation;
}
