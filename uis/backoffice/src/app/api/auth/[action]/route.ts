import { forwardApi } from "@healthcore/auth/server";

const paths: Record<string, string> = {
  "forgot-password": "/auth/forgot-password",
  "reset-password": "/auth/reset-password",
  "change-password": "/auth/change-password",
};

export async function POST(
  request: Request,
  context: { params: Promise<{ action: string }> },
) {
  const { action } = await context.params;
  if (!Object.hasOwn(paths, action)) {
    return Response.json({ detail: "Ruta no encontrada." }, { status: 404 });
  }
  // forwardApi propagates X-Request-ID both ways for telemetry correlation.
  return await forwardApi(request, paths[action], action === "change-password");
}
