import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { apiUrl, SESSION_COOKIE } from "@/lib/server-api";

// Real-time stream of the RFP dashboard (Sistemas en Tiempo Real, Parte 1). Unlike the generic
// proxy (api-proxy.ts), which reads the whole upstream body, this route PIPES the
// text/event-stream through as it arrives. The browser authenticates with its session cookie; the
// backoffice JWT goes to the API as `Authorization: Bearer …` (the same JWT as every other call)
// and never reaches page JavaScript. Last-Event-ID is forwarded so the API can replay missed events.

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request) {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Sesión requerida." }, { status: 401 });
  const headers = new Headers({ Authorization: `Bearer ${token}`, Accept: "text/event-stream" });
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId && /^\d{1,18}$/.test(lastEventId)) headers.set("Last-Event-ID", lastEventId);
  let upstream: Response;
  try {
    // request.signal: when the browser goes away, the API connection is closed too
    upstream = await fetch(apiUrl("/api/rfp/events"), { headers, cache: "no-store", redirect: "manual", signal: request.signal });
  } catch (error) {
    console.error("rfp events request failed:", error instanceof Error ? error.name : "UnknownError");
    return NextResponse.json({ detail: "Servicio no disponible." }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    const status = upstream.status === 401 || upstream.status === 429 ? upstream.status : 502;
    return NextResponse.json({ detail: status === 401 ? "Sesión expirada." : "Servicio no disponible." }, { status });
  }
  // Piped explicitly: when the API ends the stream (restart) or the connection to it breaks, the
  // browser's stream ends/errors right away, so the client reconnects instead of waiting for its watchdog.
  const reader = upstream.body.getReader();
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { value, done } = await reader.read();
        if (done) controller.close();
        else controller.enqueue(value);
      } catch (error) {
        controller.error(error);
      }
    },
    cancel(reason) {
      void reader.cancel(reason); // the browser left: close the API connection too
    },
  });
  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
