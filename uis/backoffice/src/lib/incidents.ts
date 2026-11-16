"use client";

import catalog from "@/services/incident-catalog.json";

// --- Catalog (generated from packages/shared/incidents/catalog.json) ----------

export type Option = { value: string; label: string };

export const BRANCHES: Option[] = catalog.branches;
export const CATEGORIES: Option[] = catalog.categories;
export const STATUSES: Option[] = catalog.statuses;
export const ORIGINS: Option[] = catalog.origins;
export const TRANSITIONS: Record<string, string[]> = catalog.transitions;
export const LIMITS = catalog.limits;

const labels = (options: Option[]) => new Map(options.map((option) => [option.value, option.label]));
const branchLabels = labels(BRANCHES);
const categoryLabels = labels(CATEGORIES);
const statusLabels = labels(STATUSES);
const originLabels = labels(ORIGINS);

export const branchLabel = (value: string) => branchLabels.get(value) ?? value;
export const categoryLabel = (value: string) => categoryLabels.get(value) ?? value;
export const statusLabel = (value: string) => statusLabels.get(value) ?? value;
export const originLabel = (value: string) => originLabels.get(value) ?? value;

// --- Types ----------------------------------------------------------------------

export type IncidentField = "title" | "description" | "category" | "status" | "origin" | "branch";

export interface IncidentInput {
  title: string;
  description: string;
  category: string;
  status: string;
  origin: string;
  branch: string;
}

export interface Incident extends IncidentInput {
  id: number;
  created_at: string;
  updated_at: string;
}

export interface IncidentFilters {
  status: string;
  origin: string;
  branch: string;
  category: string;
}

export interface IncidentSummary {
  total: number;
  by_status: Record<string, number>;
  by_category: Record<string, number>;
  by_origin: Record<string, number>;
  by_branch: Record<string, number>;
}

export type FieldErrors = Partial<Record<IncidentField, string>>;

const FIELDS: IncidentField[] = ["title", "description", "category", "status", "origin", "branch"];

// --- Client-side validation (mirrors the shared server rules) ---------------------

export function validateIncident(input: IncidentInput): FieldErrors {
  const errors: FieldErrors = {};
  const title = input.title.trim();
  const description = input.description.trim();
  if (!title) errors.title = "Enter a short title for the incident.";
  else if (title.length > LIMITS.title_max_length) {
    errors.title = `Keep the title under ${LIMITS.title_max_length} characters.`;
  }
  if (!description) errors.description = "Describe what happened.";
  else if (description.length < LIMITS.description_min_length) {
    errors.description = `The description needs at least ${LIMITS.description_min_length} characters.`;
  } else if (description.length > LIMITS.description_max_length) {
    errors.description = `Keep the description under ${LIMITS.description_max_length} characters.`;
  }
  const choices: [IncidentField, Option[], string][] = [
    ["category", CATEGORIES, "Choose a category."],
    ["status", STATUSES, "Choose a status."],
    ["origin", ORIGINS, "Choose where the report comes from."],
    ["branch", BRANCHES, "Choose the clinic (use Central when it is not clinic-specific)."],
  ];
  for (const [field, options, message] of choices) {
    if (!options.some((option) => option.value === input[field])) errors[field] = message;
  }
  return errors;
}

// --- Errors shown to users (never raw server text) -------------------------------------

export class IncidentRequestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly fieldErrors: FieldErrors = {},
  ) {
    super(message);
  }
}

const FRIENDLY_FIELD_MESSAGES: Record<IncidentField, string> = {
  title: "Check the title: it is required and must be 120 characters or fewer.",
  description: "Check the description: it is required and needs at least 5 characters.",
  category: "Choose one of the listed categories.",
  status: "This status is not allowed for the incident right now.",
  origin: "Choose one of the listed origins.",
  branch: "Choose one of the listed clinics.",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function fieldErrorsFrom(response: Response): Promise<FieldErrors> {
  const errors: FieldErrors = {};
  try {
    const body: unknown = await response.json();
    if (!isRecord(body) || !Array.isArray(body.errors)) return errors;
    for (const item of body.errors) {
      if (!isRecord(item) || typeof item.field !== "string") continue;
      const field = item.field as IncidentField;
      if (!FIELDS.includes(field)) continue;
      // Choice fields: the server lists internal values, so show our own wording.
      // Text/status messages are plain language; fall back to ours if unexpected.
      const useServer = ["title", "description", "status"].includes(field)
        && typeof item.message === "string" && item.message.length <= 200;
      const message = useServer ? String(item.message) : FRIENDLY_FIELD_MESSAGES[field];
      errors[field] = message;
    }
  } catch {
    // Not JSON: use the generic message only.
  }
  return errors;
}

function messageForStatus(status: number): string {
  if (status === 401) return "Your session has expired. Please sign in again.";
  if (status === 403) return "You don't have permission to do this.";
  if (status === 404) return "This incident no longer exists. Refresh the list to see the latest data.";
  if (status === 400 || status === 422) return "Some information is missing or not valid. Please review the form.";
  return "The incident service is temporarily unavailable. Please try again in a moment.";
}

const TIMEOUT_MS = 15000;

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const timeout = AbortSignal.timeout(TIMEOUT_MS);
  const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  let response: Response;
  try {
    response = await fetch(`/api/incidents${path}`, {
      ...init,
      signal,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...init.headers },
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    if (timeout.aborted) {
      throw new IncidentRequestError("The request is taking too long. Please try again.", 0);
    }
    throw new IncidentRequestError(
      "We couldn't reach the incident service. Check your connection and try again.", 0,
    );
  }
  if (!response.ok) {
    const fieldErrors = response.status === 400 ? await fieldErrorsFrom(response) : {};
    const message = Object.keys(fieldErrors).length
      ? "Please review the highlighted fields."
      : messageForStatus(response.status);
    throw new IncidentRequestError(message, response.status, fieldErrors);
  }
  try {
    return await response.json();
  } catch {
    throw new IncidentRequestError("We received an unexpected response. Please try again.", response.status);
  }
}

function isIncident(value: unknown): value is Incident {
  return isRecord(value) && typeof value.id === "number"
    && FIELDS.every((field) => typeof value[field] === "string")
    && typeof value.created_at === "string" && typeof value.updated_at === "string";
}

function isCounts(value: unknown): value is Record<string, number> {
  return isRecord(value) && Object.values(value).every((count) => typeof count === "number");
}

function unexpected(): never {
  throw new IncidentRequestError("We received an unexpected response. Please try again.", 200);
}

// --- API -------------------------------------------------------------------------------

export async function listIncidents(filters: IncidentFilters, signal?: AbortSignal): Promise<Incident[]> {
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, value]) => value !== ""),
  ).toString();
  const data = await request(query ? `?${query}` : "", { signal });
  if (!Array.isArray(data) || !data.every(isIncident)) unexpected();
  return data;
}

export async function createIncident(input: IncidentInput): Promise<Incident> {
  const data = await request("", {
    method: "POST",
    body: JSON.stringify({
      ...input, title: input.title.trim(), description: input.description.trim(),
    }),
  });
  if (!isIncident(data)) unexpected();
  return data;
}

export async function updateIncidentStatus(id: number, status: string): Promise<Incident> {
  const data = await request(`/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) });
  if (!isIncident(data)) unexpected();
  return data;
}

export async function getSummary(signal?: AbortSignal): Promise<IncidentSummary> {
  const data = await request("/summary", { signal });
  if (!isRecord(data) || typeof data.total !== "number"
    || !["by_status", "by_category", "by_origin", "by_branch"].every((key) => isCounts(data[key]))) {
    unexpected();
  }
  return data as unknown as IncidentSummary;
}

export function errorMessage(error: unknown): string {
  return error instanceof IncidentRequestError
    ? error.message
    : "Something went wrong. Please try again.";
}
