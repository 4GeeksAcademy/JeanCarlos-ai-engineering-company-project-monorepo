import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { apiUrl, SESSION_COOKIE } from "@/lib/server-api";

// What the chat page needs to open the WebSocket of the Compliance assistant directly against the
// API (a Next route handler cannot proxy a WebSocket upgrade): the URL, and the SAME backoffice JWT,
// which the page sends in the first `auth` frame — never in the URL, so it is not written to
// access logs. Only for same-origin fetches of the backoffice; never cached.

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  if (request.headers.get("sec-fetch-site") && request.headers.get("sec-fetch-site") !== "same-origin") {
    return NextResponse.json({ detail: "Origen no permitido." }, { status: 403 });
  }
  if (request.headers.get("x-healthcore-chat") !== "1") {
    return NextResponse.json({ detail: "Solicitud no válida." }, { status: 400 });
  }
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Sesión requerida." }, { status: 401 });
  const wsUrl = process.env.AGENT_WS_URL ?? apiUrl("/agent/chat/ws").replace(/^http/, "ws");
  return NextResponse.json({ token, ws_url: wsUrl }, { headers: { "Cache-Control": "no-store" } });
}
