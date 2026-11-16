import { authenticatedFetch } from "@healthcore/auth/client";
import { captureAuthentication, track } from "@/services/telemetry";

export { passwordError } from "@healthcore/auth/client";

export async function authRequest(action: string, data: object): Promise<Response> {
  const request = action === "change-password" ? authenticatedFetch : fetch;
  try {
    const response = await request(`/api/auth/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Request-ID": crypto.randomUUID() },
      body: JSON.stringify(data),
      cache: "no-store",
    });
    void captureAuthentication(action, response);
    return response;
  } catch (error) {
    if (action === "login") track("auth_login_failed", { failure_reason: "service_unavailable" });
    throw error;
  }
}
