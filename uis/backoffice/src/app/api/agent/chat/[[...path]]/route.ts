import { proxyApi } from "@/lib/api-proxy";

// /api/agent/chat/* → API /agent/chat/* (sessions of the Compliance assistant chat; session cookie → Bearer).
type Context = { params: Promise<{ path?: string[] }> };

function proxy(request: Request, context: Context) {
  return proxyApi("agentChat", request, context);
}

export const GET = proxy;
export const POST = proxy;
