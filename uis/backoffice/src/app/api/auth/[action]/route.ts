import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { apiUrl, sameOrigin, SESSION_COOKIE } from "@/lib/server-api";

const paths: Record<string, string> = {
  "forgot-password": "/auth/forgot-password",
  "reset-password": "/auth/reset-password",
  "change-password": "/auth/change-password",
  login: "/login",
};

export async function POST(
  request: Request,
  context: { params: Promise<{ action: string }> },
) {
  if (!sameOrigin(request)) {
    return NextResponse.json({ detail: "Origen no permitido." }, { status: 403 });
  }
  const { action } = await context.params;
  if (!Object.hasOwn(paths, action)) {
    return NextResponse.json({ detail: "Ruta no encontrada." }, { status: 404 });
  }
  const headers = new Headers();
  let body: string;
  if (action === "login") {
    const data: unknown = await request.json().catch(() => null);
    if (
      typeof data !== "object" || data === null ||
      !("username" in data) || typeof data.username !== "string" ||
      !("password" in data) || typeof data.password !== "string"
    ) {
      return NextResponse.json({ detail: "Datos inválidos." }, { status: 400 });
    }
    headers.set("Content-Type", "application/x-www-form-urlencoded");
    body = new URLSearchParams({ username: data.username, password: data.password }).toString();
  } else {
    headers.set("Content-Type", "application/json");
    body = await request.text();
  }
  if (action === "change-password") {
    const token = (await cookies()).get(SESSION_COOKIE)?.value;
    if (!token) return NextResponse.json({ detail: "Sesión requerida." }, { status: 401 });
    headers.set("Authorization", `Bearer ${token}`);
  }
  let upstream: Response;
  try {
    upstream = await fetch(apiUrl(paths[action]), {
      method: "POST", headers, body, cache: "no-store", redirect: "manual",
    });
  } catch (error) {
    console.error("Authentication API request failed:", error);
    return NextResponse.json({ detail: "Servicio no disponible." }, { status: 502 });
  }
  if (action === "login" && upstream.ok) {
    const data: unknown = await upstream.json();
    if (
      typeof data !== "object" || data === null ||
      !("access_token" in data) || typeof data.access_token !== "string"
    ) {
      console.error("Authentication API returned an invalid token response");
      return NextResponse.json({ detail: "Respuesta inválida." }, { status: 502 });
    }
    const response = NextResponse.json({ message: "Sesión iniciada." });
    response.cookies.set(SESSION_COOKIE, data.access_token, {
      httpOnly: true, secure: process.env.NODE_ENV === "production",
      sameSite: "strict", path: "/",
    });
    return response;
  }
  if (upstream.status >= 300 && upstream.status < 400) {
    console.error("Unexpected redirect from authentication API");
    return NextResponse.json({ detail: "Respuesta inválida." }, { status: 502 });
  }
  const text = await upstream.text();
  const response = new NextResponse(text, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
  });
  if (action === "change-password" && upstream.ok) {
    response.cookies.delete(SESSION_COOKIE);
  }
  return response;
}
