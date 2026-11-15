"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { addCandidateNote, deleteCandidateNote, getCandidateNotes } from "@/lib/api";
import type { CandidateNote } from "@/types/tracker";

interface CandidateNotesProps {
  candidateId: string;
  noteCount: number;
  onCountChange: (difference: number) => void;
  onClose: () => void;
}

function displayDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Sin fecha" : new Intl.DateTimeFormat("es", { dateStyle: "long" }).format(date);
}

export function CandidateNotes({ candidateId, noteCount, onCountChange, onClose }: CandidateNotesProps) {
  const [notes, setNotes] = useState<CandidateNote[]>([]);
  const [state, setState] = useState<"loading" | "success" | "error">("loading");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const requestId = useRef(0);

  const loadNotes = useCallback(async () => {
    const currentRequest = ++requestId.current;
    setState("loading");
    setError("");
    try {
      const result = await getCandidateNotes(candidateId);
      if (currentRequest === requestId.current) {
        setNotes(result);
        setState("success");
      }
    } catch (loadError) {
      if (currentRequest === requestId.current) {
        setError(loadError instanceof Error ? loadError.message : "No se pudieron cargar las notas.");
        setState("error");
      }
    }
  }, [candidateId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadNotes(), 0);
    return () => {
      window.clearTimeout(timer);
      requestId.current += 1;
    };
  }, [loadNotes]);

  async function submitNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const content = String(new FormData(form).get("content") ?? "").trim();
    if (!content) return;
    setBusy(true);
    setError("");
    try {
      await addCandidateNote(candidateId, content);
      await loadNotes();
      onCountChange(1);
      form.reset();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "No se pudo añadir la nota.");
    } finally {
      setBusy(false);
    }
  }

  async function removeNote(noteId: string) {
    if (!window.confirm("¿Eliminar esta nota interna? Esta acción no se puede deshacer.")) return;
    setDeleting(noteId);
    setError("");
    try {
      await deleteCandidateNote(candidateId, noteId);
      setNotes((current) => current.filter((note) => note.id !== noteId));
      onCountChange(-1);
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "No se pudo eliminar la nota.");
    } finally {
      setDeleting(null);
    }
  }

  return (
    <section className="detail-card notes-card" aria-labelledby="notes-title">
      <div className="card-title">
        <div><p className="eyebrow">SEGUIMIENTO INTERNO</p><h2 id="notes-title">Notas <span className="note-count">{noteCount}</span></h2></div>
        <button type="button" className="text-action" onClick={onClose}>Cerrar notas</button>
      </div>
      {error && <p className="notes-error" role="alert">{error}</p>}
      <form className="note-form" onSubmit={submitNote}>
        <label className="sr-only" htmlFor={`note-content-${candidateId}`}>Añadir nota interna</label>
        <textarea id={`note-content-${candidateId}`} name="content" rows={3} maxLength={2000} placeholder="Añade contexto para el equipo…" required />
        <div className="note-form-footer"><small>Visible solo para el equipo</small><button className="button button-primary" type="submit" disabled={busy}>{busy ? "Añadiendo…" : "+ Añadir nota"}</button></div>
      </form>
      {state === "loading" && <p className="notes-feedback" role="status">Cargando notas…</p>}
      {state === "error" && <div className="notes-error" role="alert"><button type="button" className="text-action" onClick={() => void loadNotes()}>Reintentar</button></div>}
      {state === "success" && notes.length === 0 && <p className="notes-empty">Todavía no hay notas para esta candidatura.</p>}
      {state === "success" && notes.length > 0 && (
        <div className="notes-list">
          {notes.map((note) => (
            <article className="note-item" key={note.id}>
              <div className="note-meta">
                <time dateTime={note.created_at}>{displayDate(note.created_at)}</time>
                <button type="button" className="text-action danger-action" onClick={() => void removeNote(note.id)} disabled={deleting !== null}>
                  {deleting === note.id ? "Eliminando…" : "Eliminar"}
                </button>
              </div>
              <p>{note.content}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
