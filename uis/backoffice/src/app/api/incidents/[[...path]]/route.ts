import { proxyApi } from "@/lib/api-proxy";

type Context = { params: Promise<{ path?: string[] }> };

function proxy(request: Request, context: Context) {
  return proxyApi("incidents", request, context);
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
