import { NextResponse } from "next/server";
import { proxyApi } from "@/lib/api-proxy";

// Read-only proxy for the business dashboard: only the two GET endpoints it needs.
const ALLOWED = new Set(["monthly-clinic-supply-performance", "pipeline-runs/latest"]);

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (!ALLOWED.has(path.join("/"))) {
    return NextResponse.json({ detail: "No encontrado." }, { status: 404 });
  }
  return proxyApi("reporting", request, { params: Promise.resolve({ path }) });
}
