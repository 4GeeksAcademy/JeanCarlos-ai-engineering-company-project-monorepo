"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import {
  BRANCHES, CATEGORIES, IncidentRequestError, LIMITS, ORIGINS, STATUSES, createIncident,
  errorMessage, validateIncident, type FieldErrors, type Incident, type IncidentField,
  type IncidentInput,
} from "@/lib/incidents";

const EMPTY: IncidentInput = {
  title: "", description: "", category: "", status: "open", origin: "", branch: "",
};
const FIELD_ORDER: IncidentField[] = ["title", "description", "category", "status", "origin", "branch"];

function FieldError({ id, message }: { id: string; message?: string }) {
  return message ? <span className="field-error" id={id}>{message}</span> : null;
}

export default function IncidentForm() {
  const [values, setValues] = useState<IncidentInput>(EMPTY);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState("");
  const [created, setCreated] = useState<Incident | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const fromBranch = values.origin === "branch";

  function update(field: IncidentField, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  }

  function focusFirstError(fieldErrors: FieldErrors) {
    const first = FIELD_ORDER.find((field) => fieldErrors[field]);
    if (first) formRef.current?.querySelector<HTMLElement>(`[name="${first}"]`)?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");
    setCreated(null);
    const clientErrors = validateIncident(values);
    if (Object.keys(clientErrors).length) {
      setErrors(clientErrors);
      setFormError("Please complete the highlighted fields before submitting.");
      focusFirstError(clientErrors);
      return;
    }
    setSubmitting(true);
    try {
      const incident = await createIncident(values);
      setValues(EMPTY);
      setErrors({});
      setCreated(incident);
    } catch (error) {
      const fieldErrors = error instanceof IncidentRequestError ? error.fieldErrors : {};
      setErrors(fieldErrors);
      setFormError(errorMessage(error));
      focusFirstError(fieldErrors);
    } finally {
      setSubmitting(false);
    }
  }

  const describedBy = (field: IncidentField, extra?: string) =>
    [extra, errors[field] ? `${field}-error` : undefined].filter(Boolean).join(" ") || undefined;

  return (
    <form ref={formRef} className="panel incident-form" onSubmit={submit} noValidate aria-busy={submitting}>
      {created && (
        <div className="notice success-notice" role="status">
          <strong>Incident #{created.id} registered.</strong> “{created.title}” is now open in the{" "}
          <Link href="/incidents">incident dashboard</Link>. The form has been cleared for a new report.
        </div>
      )}
      {formError && <div className="notice error-notice" role="alert">{formError}</div>}

      <fieldset className="form-grid" disabled={submitting}>
        <legend className="sr-only">Incident details</legend>

        <label className="wide">
          <span className="label-text">Title <span className="required" aria-hidden="true">*</span></span>
          <input
            name="title" value={values.title} maxLength={LIMITS.title_max_length} required
            aria-invalid={Boolean(errors.title)} aria-describedby={describedBy("title", "title-hint")}
            onChange={(event) => update("title", event.target.value)}
          />
          <small id="title-hint">{values.title.length}/{LIMITS.title_max_length} characters</small>
          <FieldError id="title-error" message={errors.title} />
        </label>

        <div className="phi-warning wide" role="note" id="phi-warning">
          <strong>⚠ Do not enter patient-identifiable information.</strong>
          <p>
            No names, dates of birth, medical record or insurance numbers, addresses or contact
            details (HIPAA / UK GDPR). If a patient is involved, use only the internal opaque reference.
          </p>
        </div>

        <label className="wide">
          <span className="label-text">Description <span className="required" aria-hidden="true">*</span></span>
          <textarea
            name="description" rows={5} value={values.description} required
            maxLength={LIMITS.description_max_length}
            aria-invalid={Boolean(errors.description)}
            aria-describedby={describedBy("description", "phi-warning")}
            onChange={(event) => update("description", event.target.value)}
          />
          <FieldError id="description-error" message={errors.description} />
        </label>

        <label>
          <span className="label-text">Category <span className="required" aria-hidden="true">*</span></span>
          <select
            name="category" value={values.category} required aria-invalid={Boolean(errors.category)}
            aria-describedby={describedBy("category")}
            onChange={(event) => update("category", event.target.value)}
          >
            <option value="">Select a category</option>
            {CATEGORIES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <FieldError id="category-error" message={errors.category} />
        </label>

        <label>
          <span className="label-text">Status <span className="required" aria-hidden="true">*</span></span>
          <select
            name="status" value={values.status} required aria-invalid={Boolean(errors.status)}
            aria-describedby={describedBy("status")}
            onChange={(event) => update("status", event.target.value)}
          >
            {STATUSES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <FieldError id="status-error" message={errors.status} />
        </label>

        <label>
          <span className="label-text">Origin <span className="required" aria-hidden="true">*</span></span>
          <select
            name="origin" value={values.origin} required aria-invalid={Boolean(errors.origin)}
            aria-describedby={describedBy("origin")}
            onChange={(event) => update("origin", event.target.value)}
          >
            <option value="">Select an origin</option>
            {ORIGINS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <FieldError id="origin-error" message={errors.origin} />
        </label>

        <label className={fromBranch ? "branch-highlight" : undefined}>
          <span className="label-text">Clinic / branch <span className="required" aria-hidden="true">*</span></span>
          <select
            name="branch" value={values.branch} required aria-invalid={Boolean(errors.branch)}
            aria-describedby={describedBy("branch", "branch-hint")}
            onChange={(event) => update("branch", event.target.value)}
          >
            <option value="">Select a clinic</option>
            {BRANCHES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <small id="branch-hint">
            {fromBranch
              ? "You are reporting from a specific clinic: make sure this is the clinic where it happened."
              : "Use “Central — Austin Main Clinic” when the incident is not tied to a specific clinic."}
          </small>
          <FieldError id="branch-error" message={errors.branch} />
        </label>
      </fieldset>

      <div className="form-actions">
        <button type="submit" disabled={submitting}>
          {submitting ? <><span className="spinner" aria-hidden="true" /> Submitting…</> : "Register incident"}
        </button>
        {submitting && <span role="status">Saving the incident, please wait…</span>}
      </div>
    </form>
  );
}
