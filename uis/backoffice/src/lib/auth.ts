export function passwordError(password: string, confirmation: string): string {
  if (Array.from(password).length < 8) return "La contraseña debe tener al menos 8 caracteres.";
  if (new TextEncoder().encode(password).length > 72) {
    return "La contraseña no puede superar 72 bytes UTF-8.";
  }
  if (password !== confirmation) return "Las contraseñas no coinciden.";
  return "";
}

export async function authRequest(action: string, data: object): Promise<Response> {
  return await fetch(`/api/auth/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    cache: "no-store",
  });
}
