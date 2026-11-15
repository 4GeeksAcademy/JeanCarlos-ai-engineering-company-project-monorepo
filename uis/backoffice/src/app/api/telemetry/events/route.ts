import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { apiUrl, sameOrigin, SESSION_COOKIE } from "@/lib/server-api";

export async function POST(request: Request) {
  if (!sameOrigin(request)) {
    return NextResponse.json({ detail: "Origen no permitido." }, { status: 403 });
  }
  const body = await request.text();
  if (new TextEncoder().encode(body).length > 60000) {
    return NextResponse.json({ detail: "Lote demasiado grande." }, { status: 413 });
  }
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  const headers = new Headers({ "Content-Type": "application/json" });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  try {
    const response = await fetch(process.env.TELEMETRY_ENDPOINT ?? apiUrl("/telemetry/events"), {
      method: "POST", headers, body, cache: "no-store", redirect: "manual",
    });
    if (response.status !== 200) {
      console.error("Telemetry receiver rejected batch:", response.status);
      return NextResponse.json({ detail: "Lote no aceptado." }, {
        status: response.status === 422 ? 422 : 502,
      });
    }
    return new NextResponse(await response.text(), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (error) {
    console.error("Telemetry receiver unavailable:", error instanceof Error ? error.name : "UnknownError");
    return NextResponse.json({ detail: "Telemetría no disponible." }, { status: 502 });
  }
}
