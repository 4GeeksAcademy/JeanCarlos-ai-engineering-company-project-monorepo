import { proxyApi } from "@/lib/api-proxy";

export async function GET(request: Request) {
  return await proxyApi("telemetry", request, {
    params: Promise.resolve({ path: ["session"] }),
  });
}
