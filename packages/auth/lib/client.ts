"use client";

import type { Account, Profile } from "../types";

export const TOKEN_KEY = "healthcore_access_token";
export const SESSION_EVENT = "healthcore-session-changed";

export class AuthError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly fields: Record<string, string> = {},
  ) {
    super(message);
  }
}

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    throw new Error("El navegador bloquea el almacenamiento de la sesión.");
  }
}

export function storeToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    throw new Error("No se pudo guardar la sesión. Permite el almacenamiento local.");
  }
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    throw new Error("No se pudo eliminar la sesión del navegador.");
  }
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export async function authenticatedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error("Las peticiones autenticadas deben usar rutas internas.");
  }
  const token = getToken();
  if (!token) {
    clearSession();
    throw new AuthError("Inicia sesión para continuar.", 401);
  }
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers, cache: "no-store" });
  if (response.status === 401) clearSession();
  return response;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isProfile(value: unknown): value is Profile {
  return isRecord(value) && typeof value.id === "string" && typeof value.user_id === "string"
    && ["name", "phone", "address"].every((key) => value[key] === null || typeof value[key] === "string");
}

export async function responseJson(response: Response): Promise<unknown> {
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new AuthError("El servicio devolvió una respuesta inválida.", response.status);
  }
  if (!response.ok) {
    const fields: Record<string, string> = {};
    let message = response.status === 401
      ? "Correo o contraseña incorrectos."
      : `No se pudo completar la solicitud (${response.status}).`;
    if (isRecord(data) && typeof data.detail === "string") {
      message = data.detail;
      if (response.status === 409 && message === "Email already exists") {
        message = fields.email = "Este correo ya está registrado.";
      }
    } else if (isRecord(data) && Array.isArray(data.detail)) {
      for (const issue of data.detail) {
        if (isRecord(issue) && Array.isArray(issue.loc) && typeof issue.msg === "string") {
          const field: unknown = issue.loc.at(-1);
          if (typeof field === "string") fields[field] = issue.msg;
        }
      }
      message = "Revisa los campos indicados.";
    }
    throw new AuthError(message, response.status, fields);
  }
  return data;
}

/**
 * Called once per login attempt: with the API response (after the token is stored
 * on success), or with null when the service could not be reached.
 */
export type AuthResponseListener = (response: Response | null) => void;

export async function login(
  email: string, password: string, onResponse?: AuthResponseListener,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch("/api/account/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Request-ID": crypto.randomUUID() },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });
  } catch (error) {
    onResponse?.(null);
    throw error;
  }
  let data: unknown;
  try {
    data = await responseJson(response);
  } catch (error) {
    onResponse?.(response);
    throw error;
  }
  if (!isRecord(data) || typeof data.access_token !== "string" || !data.access_token.trim()
    || data.token_type !== "bearer") {
    throw new Error("El servicio devolvió una sesión inválida.");
  }
  storeToken(data.access_token);
  onResponse?.(response);
}

export async function getAccount(signal?: AbortSignal): Promise<Account> {
  const data = await responseJson(await authenticatedFetch("/api/account/me", { signal }));
  if (!isRecord(data) || typeof data.id !== "string" || typeof data.username !== "string"
    || !(data.email === null || typeof data.email === "string")
    || !["admin", "manager", "user"].includes(String(data.role))
    || !(data.profile === null || isProfile(data.profile))) {
    throw new Error("El servicio devolvió una cuenta inválida.");
  }
  return {
    id: data.id, username: data.username, email: data.email,
    role: data.role === "admin" ? "admin" : data.role === "manager" ? "manager" : "user",
    profile: data.profile,
  };
}

export async function saveProfile(payload: object): Promise<Profile> {
  const data = await responseJson(await authenticatedFetch("/api/account/profile", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
  if (!isProfile(data)) throw new Error("El servicio devolvió un perfil inválido.");
  return data;
}

export function passwordError(password: string, confirmation: string): string {
  if (Array.from(password).length < 8) return "La contraseña debe tener al menos 8 caracteres.";
  if (new TextEncoder().encode(password).length > 72) return "La contraseña no puede superar 72 bytes UTF-8.";
  if (password !== confirmation) return "Las contraseñas no coinciden.";
  return "";
}
