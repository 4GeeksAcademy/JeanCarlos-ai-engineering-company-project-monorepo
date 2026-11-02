import type {
  Candidate,
  CandidateInput,
  CandidateNote,
  CandidatePatch,
  NotesResponse,
  PaginatedResponse,
} from "@/types/tracker";

const API_URL = "/api/tracker";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!process.env.NEXT_PUBLIC_API_URL) {
    throw new Error("Falta configurar NEXT_PUBLIC_API_URL.");
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new Error("No se pudo conectar con la API. Comprueba tu conexión e inténtalo de nuevo.");
  }

  if (!response.ok) {
    let message = `La solicitud falló (${response.status}).`;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body) {
        const detail = body.detail;
        message = Array.isArray(detail)
          ? detail.map((item) => (typeof item === "object" && item !== null && "msg" in item ? item.msg : "Error de validación")).join(" ")
          : typeof detail === "string" ? detail : message;
      }
    } catch {
      // The response body may not contain JSON.
    }
    throw new Error(message);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function getCandidates(params: URLSearchParams): Promise<PaginatedResponse<Candidate>> {
  const search = new URLSearchParams(params);
  search.set("page", "1");
  search.set("limit", "1000");
  return request<PaginatedResponse<Candidate>>(`/records?${search.toString()}`);
}

export function getCandidate(id: string): Promise<Candidate> {
  return request<Candidate>(`/records/${encodeURIComponent(id)}`);
}

export function createCandidate(input: CandidateInput): Promise<Candidate> {
  return request<Candidate>("/records", { method: "POST", body: JSON.stringify(input) });
}

export function replaceCandidate(id: string, input: CandidateInput): Promise<Candidate> {
  return request<Candidate>(`/records/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function patchCandidate(id: string, patch: CandidatePatch): Promise<Candidate> {
  return request<Candidate>(`/records/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function getCandidateNotes(id: string): Promise<CandidateNote[]> {
  const result = await request<NotesResponse>(`/records/${encodeURIComponent(id)}/notes`);
  return result.data;
}

export function addCandidateNote(id: string, content: string): Promise<CandidateNote> {
  return request<CandidateNote>(`/records/${encodeURIComponent(id)}/notes`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function deleteCandidateNote(id: string, noteId: string): Promise<void> {
  return request<void>(`/records/${encodeURIComponent(id)}/notes/${encodeURIComponent(noteId)}`, {
    method: "DELETE",
  });
}