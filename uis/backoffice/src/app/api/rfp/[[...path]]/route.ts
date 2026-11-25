import { proxyApi } from "@/lib/api-proxy";

type Context = { params: Promise<{ path?: string[] }> };

function proxy(request: Request, context: Context) {
  return proxyApi("rfp", request, context);
}

export const GET = proxy;
export const POST = proxy;
