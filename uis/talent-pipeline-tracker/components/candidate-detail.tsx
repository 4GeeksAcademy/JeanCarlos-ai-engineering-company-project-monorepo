"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { CandidateForm } from "@/components/candidate-form";
import {
  addCandidateNote,
  deleteCandidateNote,
  getCandidate,
  getCandidateNotes,
  patchCandidate,
} from "@/lib/api";
import { candidateStages, candidateStatuses, type Candidate, type CandidateNote, type CandidateStage, type CandidateStatus } from "@/types/tracker";

const statusLabels: Record<CandidateStatus, string> = {
  received: "Recibida",
  in_progress: "En proceso",
  selected: "Seleccionada",
  discarded: "Descartada",
};

const stageLabels: Record<CandidateStage, string> = {
  pending: "Pendiente",
  review: "Revisión",
  personal_interview: "Entrevista personal",
  technical_interview: "Entrevista técnica",
  offer_presented: "Oferta presentada",
};

function displayDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Sin fecha" : new Intl.DateTimeFormat("es", { dateStyle: "long" }).format(date);
}

function validExternalUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

interface CandidateDetailProps {
  id: string;
}

export function CandidateDetail({ id }: CandidateDetailProps) {
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [notes, setNotes] = useState<CandidateNote[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "success" | "error">("loading");
  const [notesState, setNotesState] = useState<"loading" | "success" | "error">("loading");
  const [error, setError] = useState("");
  const [notesError, setNotesError] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [updating, setUpdating] = useState<"status" | "stage" | null>(null);
  const [noteBusy, setNoteBusy] = useState(false);
  const [deletingNote, setDeletingNote] = useState<string | null>(null);

  const loadCandidate = useCallback(async () => {
    setLoadState("loading");
    setError("");
    try {
      const result = await getCandidate(id);
      setCandidate(result);
      setLoadState("success");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "No se pudo cargar la candidatura.");
      setLoadState("error");
    }
  }, [id]);

  const loadNotes = useCallback(async () => {
    setNotesState("loading");
    setNotesError("");
    try {
      setNotes(await getCandidateNotes(id));
      setNotesState("success");
    } catch (loadError) {
      setNotesError(loadError instanceof Error ? loadError.message : "No se pudieron cargar las notas.");
      setNotesState("error");
    }
  }, [id]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadCandidate();
      void loadNotes();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadCandidate, loadNotes]);

  async function updatePipeline(field: "status" | "stage", value: CandidateStatus | CandidateStage) {
    if (!candidate) return;
    setUpdating(field);
    setMutationError("");
    setFeedback("");
    try {
      const updated = await patchCandidate(candidate.id, { [field]: value });
      setCandidate(updated);
      setFeedback(field === "status" ? "Estado actualizado." : "Etapa actualizada.");
    } catch (updateError) {
      setMutationError(updateError instanceof Error ? updateError.message : "No se pudo actualizar el pipeline.");
    } finally {
      setUpdating(null);
    }
  }

  async function submitNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!candidate) return;
    const form = event.currentTarget;
    const content = String(new FormData(form).get("content") ?? "").trim();
    if (!content) return;
    setNoteBusy(true);
    setMutationError("");
    setFeedback("");
    try {
      await addCandidateNote(candidate.id, content);
      await loadNotes();
      setCandidate((current) => current ? { ...current, notes_count: current.notes_count + 1 } : current);
      form.reset();
      setFeedback("Nota añadida.");
    } catch (noteError) {
      setMutationError(noteError instanceof Error ? noteError.message : "No se pudo añadir la nota.");
    } finally {
      setNoteBusy(false);
    }
  }

  async function removeNote(noteId: string) {
    if (!candidate || !window.confirm("¿Eliminar esta nota interna? Esta acción no se puede deshacer.")) return;
    setDeletingNote(noteId);
    setMutationError("");
    setFeedback("");
    try {
      await deleteCandidateNote(candidate.id, noteId);
      setNotes((current) => current.filter((note) => note.id !== noteId));
      setCandidate((current) => current ? { ...current, notes_count: Math.max(0, current.notes_count - 1) } : current);
      setFeedback("Nota eliminada.");
    } catch (deleteError) {
      setMutationError(deleteError instanceof Error ? deleteError.message : "No se pudo eliminar la nota.");
    } finally {
      setDeletingNote(null);
    }
  }

  async function handleCandidateSaved(saved: Candidate) {
    setCandidate((current) => current ? { ...saved, status: current.status, stage: current.stage, notes_count: current.notes_count } : saved);
    setFeedback("Datos de la candidatura actualizados.");
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="People and Talent, listado de candidaturas">
          <span className="brand-mark" aria-hidden="true">HC</span>
          <span><strong>HealthCore</strong><small>People & Talent</small></span>
        </Link>
        <div className="topbar-context"><span className="online-dot" /> Talent pipeline</div>
        <Link className="back-link" href="/">← <span>Volver al listado</span></Link>
      </header>

      <div className="page-content detail-page">
        <nav className="breadcrumb" aria-label="Ruta de navegación"><Link href="/">Candidaturas</Link><span aria-hidden="true">/</span><span>Detalle</span></nav>
        {loadState === "loading" && <div className="feedback-state detail-loading" role="status"><span className="spinner" />Cargando candidatura…</div>}
        {loadState === "error" && <section className="feedback-state error-state" role="alert"><div><strong>No se pudo cargar la candidatura</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => void loadCandidate()}>Reintentar</button></section>}

        {loadState === "success" && candidate && <>
          <section className="detail-hero">
            <div className="detail-identity"><span className="candidate-avatar avatar-large" aria-hidden="true">{candidate.full_name.split(/\s+/).slice(0, 2).map((part) => part[0] ?? "").join("").toLocaleUpperCase("es")}</span><div><p className="eyebrow">DETALLE DE CANDIDATURA</p><h1>{candidate.full_name}</h1><p className="detail-subtitle">{candidate.position} <span>·</span> {candidate.experience_years} años de experiencia</p></div></div>
            <CandidateForm candidate={candidate} onSaved={handleCandidateSaved} />
          </section>

          {(mutationError || feedback) && <div className={mutationError ? "operation-feedback operation-error" : "operation-feedback operation-success"} role={mutationError ? "alert" : "status"}>{mutationError || feedback}</div>}

          <div className="detail-grid">
            <section className="detail-card" aria-labelledby="candidate-info-title">
              <div className="card-title"><div><p className="eyebrow">PERFIL</p><h2 id="candidate-info-title">Información de candidatura</h2></div><span className="section-index">01</span></div>
              <dl className="candidate-fields">
                <div><dt>Nombre completo</dt><dd>{candidate.full_name}</dd></div>
                <div><dt>Correo electrónico</dt><dd><a href={`mailto:${candidate.email}`}>{candidate.email}</a></dd></div>
                <div><dt>Teléfono</dt><dd><a href={`tel:${candidate.phone}`}>{candidate.phone}</a></dd></div>
                <div><dt>Puesto</dt><dd>{candidate.position}</dd></div>
                <div><dt>LinkedIn</dt><dd>{validExternalUrl(candidate.linkedin_url) ? <a href={validExternalUrl(candidate.linkedin_url) ?? undefined} target="_blank" rel="noreferrer">Abrir perfil ↗</a> : <span className="muted-value">No proporcionado</span>}</dd></div>
                <div><dt>Enlace al CV</dt><dd>{validExternalUrl(candidate.cv_url) ? <a href={validExternalUrl(candidate.cv_url) ?? undefined} target="_blank" rel="noreferrer">Ver CV ↗</a> : <span className="muted-value">No proporcionado</span>}</dd></div>
                <div><dt>Años de experiencia</dt><dd>{candidate.experience_years}</dd></div>
                <div><dt>Fecha de aplicación</dt><dd>{displayDate(candidate.applied_at)}</dd></div>
                <div><dt>Última actualización</dt><dd>{displayDate(candidate.updated_at)}</dd></div>
                <div><dt>ID de candidatura</dt><dd className="record-id">{candidate.id}</dd></div>
              </dl>
            </section>

            <div className="detail-side-column">
              <section className="detail-card pipeline-card" aria-labelledby="pipeline-title">
                <div className="card-title"><div><p className="eyebrow">PROCESO DE SELECCIÓN</p><h2 id="pipeline-title">Estado y etapa</h2></div><span className="section-index">02</span></div>
                <label className="detail-select-label">Estado<select value={candidate.status} disabled={updating !== null} onChange={(event) => void updatePipeline("status", event.target.value as CandidateStatus)}>{candidateStatuses.map((status) => <option key={status} value={status}>{statusLabels[status]}</option>)}</select></label>
                <label className="detail-select-label">Etapa<select value={candidate.stage} disabled={updating !== null} onChange={(event) => void updatePipeline("stage", event.target.value as CandidateStage)}>{candidateStages.map((stage) => <option key={stage} value={stage}>{stageLabels[stage]}</option>)}</select></label>
                {updating && <p className="field-feedback" role="status"><span className="spinner spinner-small" />Guardando {updating === "status" ? "estado" : "etapa"}…</p>}
              </section>

              <section className="detail-card notes-card" aria-labelledby="notes-title">
                <div className="card-title"><div><p className="eyebrow">SEGUIMIENTO INTERNO</p><h2 id="notes-title">Notas <span className="note-count">{candidate.notes_count}</span></h2></div><span className="section-index">03</span></div>
                <form className="note-form" onSubmit={submitNote}><label className="sr-only" htmlFor="note-content">Añadir nota interna</label><textarea id="note-content" name="content" rows={3} maxLength={2000} placeholder="Añade contexto para el equipo…" required /><div className="note-form-footer"><small>Visible solo para el equipo</small><button className="button button-primary" type="submit" disabled={noteBusy}>{noteBusy ? "Añadiendo…" : "+ Añadir nota"}</button></div></form>
                {notesState === "loading" && <p className="notes-feedback" role="status">Cargando notas…</p>}
                {notesState === "error" && <div className="notes-error" role="alert"><span>{notesError}</span><button type="button" className="text-action" onClick={() => void loadNotes()}>Reintentar</button></div>}
                {notesState === "success" && notes.length === 0 && <p className="notes-empty">Todavía no hay notas para esta candidatura.</p>}
                {notesState === "success" && notes.length > 0 && <div className="notes-list">{notes.map((note) => <article className="note-item" key={note.id}><div className="note-meta"><time dateTime={note.created_at}>{displayDate(note.created_at)}</time><button type="button" className="text-action danger-action" onClick={() => void removeNote(note.id)} disabled={deletingNote !== null}>{deletingNote === note.id ? "Eliminando…" : "Eliminar"}</button></div><p>{note.content}</p></article>)}</div>}
              </section>
            </div>
          </div>
        </>}
        <footer className="page-footer"><span>People & Talent <i>·</i> Talent pipeline</span><span>Seguimiento interno de selección</span></footer>
      </div>
    </main>
  );
}