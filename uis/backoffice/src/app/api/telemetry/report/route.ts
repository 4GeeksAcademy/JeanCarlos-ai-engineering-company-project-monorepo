import { proxyApi } from "@/lib/api-proxy";

// Forwards GET /telemetry/report (with its start_date/end_date query) using the session token.
export function GET(request: Request) {
  return proxyApi("telemetry", request, { params: Promise.resolve({ path: ["report"] }) });
}
