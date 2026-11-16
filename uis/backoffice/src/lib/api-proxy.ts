import { forwardApi } from "@healthcore/auth/server";

export async function proxyApi(
  resource: "suppliers" | "inventory" | "telemetry",
  request: Request,
  context: { params: Promise<{ path?: string[] }> },
) {
  const { path = [] } = await context.params;
  const suffix = path.length ? `/${path.map(encodeURIComponent).join("/")}` : "";
  const query = new URL(request.url).search;
  // forwardApi requires the Bearer token and forwards X-Request-ID / X-Available-Stock.
  return await forwardApi(request, `/${resource}${suffix}${query}`, true);
}
