import type {
  Candidate,
  CandidateInput,
  CandidateNote,
  CandidatePatch,
  NotesResponse,
  PaginatedResponse,
} from "@/types/tracker";
import { candidateStages, candidateStatuses } from "@/types/tracker";

const API_URL = "/api/tracker";

function isCandidate(value: unknown): value is Candidate {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return ["id", "full_name", "email", "phone", "position", "applied_at", "updated_at"]
    .every((field) => typeof record[field] === "string")
    && (record.linkedin_url === null || typeof record.linkedin_url === "string")
    && (record.cv_url === null || typeof record.cv_url === "string")
    && typeof record.experience_years === "number" && Number.isFinite(record.experience_years)
    && typeof record.notes_count === "number" && Number.isFinite(record.notes_count)
    && candidateStatuses.some((status) => status === record.status)
    && candidateStages.some((stage) => stage === record.stage);
}

function isNote(value: unknown): value is CandidateNote {
  if (typeof value !== "object" || value === null) return false;
  const note = value as Record<string, unknown>;
  return ["id", "record_id", "content", "created_at"]
    .every((field) => typeof note[field] === "string");
}

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
  } catch (error) {
    if (init?.signal?.aborted) throw error;
    throw new Error("No se pudo conectar con la API. Comprueba tu conexión e inténtalo de nuevo.");
  }

  if (!response.ok) {
    let message = `La solicitud falló (${response.status}).`;
    if (response.status < 500) {
      try {
        const body: unknown = await response.json();
        if (typeof body === "object" && body !== null && "detail" in body) {
          const detail = body.detail;
          if (typeof detail === "string" && detail) message = detail;
          else if (Array.isArray(detail)) {
            const messages = detail.flatMap((item: unknown) =>
              typeof item === "object" && item !== null && "msg" in item &&
              typeof item.msg === "string" ? [item.msg] : [],
            );
            if (messages.length) message = messages.join(" ");
          }
        }
      } catch {
        // Keep the generic error when the response is not valid JSON.
      }
    }
    throw new Error(message);
  }

  if (response.status === 204) return undefined as T;
  try {
    return await response.json() as T;
  } catch {
    throw new Error("La API devolvió una respuesta inválida.");
  }
}

async function candidateRequest(path: string, init?: RequestInit): Promise<Candidate> {
  const result = await request<unknown>(path, init);
  if (!isCandidate(result)) throw new Error("La API devolvió una candidatura inválida.");
  return result;
}

export async function getCandidates(params: URLSearchParams): Promise<PaginatedResponse<Candidate>> {
  const search = new URLSearchParams(params);
  search.set("page", "1");
  search.set("limit", "1000");
  const result = await request<PaginatedResponse<Candidate>>(`/records?${search.toString()}`);
  if (!result || !Array.isArray(result.data) || !result.data.every(isCandidate) ||
      typeof result.total !== "number" || !Number.isFinite(result.total)) {
    throw new Error("La API devolvió un listado inválido.");
  }
  return result;
}

export function getCandidate(id: string): Promise<Candidate> {
  return candidateRequest(`/records/${encodeURIComponent(id)}`);
}

export function createCandidate(input: CandidateInput): Promise<Candidate> {
  return candidateRequest("/records", { method: "POST", body: JSON.stringify(input) });
}

export function replaceCandidate(id: string, input: CandidateInput): Promise<Candidate> {
  return candidateRequest(`/records/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function patchCandidate(id: string, patch: CandidatePatch): Promise<Candidate> {
  return candidateRequest(`/records/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function getCandidateNotes(id: string): Promise<CandidateNote[]> {
  const result = await request<NotesResponse>(`/records/${encodeURIComponent(id)}/notes`);
  if (!result || !Array.isArray(result.data) || !result.data.every(isNote)) {
    throw new Error("La API devolvió notas inválidas.");
  }
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