import { captureAuthentication, track } from "@/services/telemetry";

export function passwordError(password: string, confirmation: string): string {
  if (Array.from(password).length < 8) return "La contraseña debe tener al menos 8 caracteres.";
  if (new TextEncoder().encode(password).length > 72) {
    return "La contraseña no puede superar 72 bytes UTF-8.";
  }
  if (password !== confirmation) return "Las contraseñas no coinciden.";
  return "";
}

export async function authRequest(action: string, data: object): Promise<Response> {
  try {
    const response = await fetch(`/api/auth/${action}`, {
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
