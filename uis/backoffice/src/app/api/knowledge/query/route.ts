import { proxyApi } from "@/lib/api-proxy";

// POST /api/knowledge/query → API POST /knowledge/query (session cookie → Bearer token).
export async function POST(request: Request) {
  return proxyApi("knowledge", request, { params: Promise.resolve({ path: ["query"] }) });
}
