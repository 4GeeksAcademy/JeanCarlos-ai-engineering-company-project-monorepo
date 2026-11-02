"use client";

import { useRef, useState, type FormEvent } from "react";
import { createCandidate, replaceCandidate } from "@/lib/api";
import type { Candidate, CandidateInput } from "@/types/tracker";

interface CandidateFormProps {
  candidate?: Candidate;
  onSaved: (candidate: Candidate) => void | Promise<void>;
}

export function CandidateForm({ candidate, onSaved }: CandidateFormProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isEditing = Boolean(candidate);

  function openForm() {
    setError("");
    const form = formRef.current;
    if (form) {
      for (const field of ["full_name", "email", "phone", "position", "experience_years", "linkedin_url", "cv_url"]) {
        const input = form.elements.namedItem(field) as HTMLInputElement | null;
        if (input) input.value = candidate?.[field as keyof Candidate] == null ? "" : String(candidate[field as keyof Candidate]);
      }
    }
    dialogRef.current?.showModal();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const experience = Number(data.get("experience_years"));
    if (!Number.isFinite(experience) || experience < 0) {
      setError("Los años de experiencia deben ser un número igual o mayor que cero.");
      return;
    }

    const input: CandidateInput = {
      full_name: String(data.get("full_name")).trim(),
      email: String(data.get("email")).trim(),
      phone: String(data.get("phone")).trim(),
      position: String(data.get("position")).trim(),
      linkedin_url: String(data.get("linkedin_url") || "").trim() || null,
      cv_url: String(data.get("cv_url") || "").trim() || null,
      experience_years: experience,
    };

    setBusy(true);
    setError("");
    try {
      const saved = isEditing && candidate
        ? await replaceCandidate(candidate.id, input)
        : await createCandidate(input);
      await onSaved(saved);
      dialogRef.current?.close();
      form.reset();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "No se pudo guardar la candidatura.");
    } finally {
      setBusy(false);
    }
  }

  return <>
    <button className={isEditing ? "button button-secondary" : "button button-primary"} type="button" onClick={openForm}>
      {isEditing ? "Editar datos" : <><span aria-hidden="true">＋</span> Nueva candidatura</>}
    </button>
    <dialog className="candidate-dialog" ref={dialogRef} aria-labelledby={isEditing ? "edit-dialog-title" : "new-dialog-title"}>
      <div className="dialog-heading"><div><p className="eyebrow">PEOPLE & TALENT</p><h2 id={isEditing ? "edit-dialog-title" : "new-dialog-title"}>{isEditing ? "Editar candidatura" : "Registrar candidatura"}</h2></div><button className="dialog-x" type="button" onClick={() => dialogRef.current?.close()} aria-label="Cerrar formulario">×</button></div>
      <form className="candidate-form" ref={formRef} onSubmit={submit}>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="form-grid">
          <label className="field-wide">Nombre completo<input name="full_name" type="text" defaultValue={candidate?.full_name ?? ""} required /></label>
          <label>Correo electrónico<input name="email" type="email" defaultValue={candidate?.email ?? ""} required /></label>
          <label>Teléfono<input name="phone" type="tel" defaultValue={candidate?.phone ?? ""} required /></label>
          <label className="field-wide">Puesto<input name="position" type="text" defaultValue={candidate?.position ?? ""} required /></label>
          <label>Experiencia (años)<input name="experience_years" type="number" min="0" step="0.5" defaultValue={candidate?.experience_years ?? ""} required /></label>
          <label>LinkedIn<input name="linkedin_url" type="url" placeholder="https://…" defaultValue={candidate?.linkedin_url ?? ""} /></label>
          <label className="field-wide">Enlace al CV<input name="cv_url" type="url" placeholder="https://…" defaultValue={candidate?.cv_url ?? ""} /></label>
        </div>
        <div className="form-actions"><button className="button button-secondary" type="button" onClick={() => dialogRef.current?.close()}>Cancelar</button><button className="button button-primary" type="submit" disabled={busy}>{busy ? "Guardando…" : isEditing ? "Guardar cambios" : "Registrar candidatura"}</button></div>
      </form>
    </dialog>
  </>;
}