"use client";

import { useRef, useState, type FormEvent } from "react";

// Knowledge-base assistant for patient coordinators (HealthCore BRIEFING, Hito 7).
// Sends the question to POST /api/knowledge/query (→ API POST /knowledge/query) and shows
// only the model-generated answer. A failed call is shown as an error, never as an empty answer.

const EXAMPLES = [
  "¿Cobran cargo por cancelación con 12 horas de anticipación?",
  "¿Qué debo traer a mi primera cita?",
  "¿Aceptan Medicaid en la clínica de Georgia?",
  "¿Cuánto tarda una referencia a un especialista?",
];
const MAX_LENGTH = 1000;

type State =
  | { status: "idle" }
  | { status: "loading"; question: string }
  | { status: "answered"; question: string; answer: string }
  | { status: "error"; question: string; message: string };

function errorMessage(status: number, body: unknown): string {
  const detail = typeof body === "object" && body !== null && "detail" in body ? (body as { detail: unknown }).detail : null;
  if (status === 401) return "Tu sesión expiró. Vuelve a iniciar sesión.";
  if (status === 422) return "Revisa la pregunta: debe tener entre 3 y 1000 caracteres.";
  if (typeof detail === "string" && status < 500) return detail;
  return "El asistente no está disponible en este momento. Inténtalo de nuevo en unos minutos.";
}

export default function KnowledgeAssistantPage() {
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<State>({ status: "idle" });
  const controller = useRef<AbortController | null>(null);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (trimmed.length < 3) {
      setState({ status: "error", question: trimmed, message: "Escribe una pregunta de al menos 3 caracteres." });
      return;
    }
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setState({ status: "loading", question: trimmed });
    try {
      const response = await fetch("/api/knowledge/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
        signal: current.signal,
      });
      const body: unknown = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(response.status, body));
      const answer = typeof body === "object" && body !== null ? (body as { answer?: unknown }).answer : null;
      if (typeof answer !== "string" || !answer.trim()) throw new Error("El asistente devolvió una respuesta vacía. Inténtalo de nuevo.");
      setState({ status: "answered", question: trimmed, answer });
    } catch (error) {
      if (current.signal.aborted) return;
      setState({
        status: "error",
        question: trimmed,
        message: error instanceof Error && error.message !== "Failed to fetch"
          ? error.message
          : "No se pudo conectar con el asistente. Revisa tu conexión e inténtalo de nuevo.",
      });
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask(question);
  }

  const loading = state.status === "loading";
  const [body, source] = state.status === "answered" ? splitSource(state.answer) : ["", ""];

  return (
    <main className="knowledge-page">
      <h1>Asistente de políticas para coordinadores</h1>
      <p>
        Pregunta sobre seguros aceptados, citas y cancelaciones, referencias internas o el checklist de pacientes nuevos.
        Las respuestas salen solo de los documentos oficiales de HealthCore. No escribas datos de pacientes (nombres,
        diagnósticos ni números de historia clínica).
      </p>

      <section className="panel" aria-label="Pregunta">
        <form className="knowledge-form" onSubmit={submit}>
          <label htmlFor="knowledge-question">Tu pregunta</label>
          <textarea
            id="knowledge-question"
            rows={3}
            maxLength={MAX_LENGTH}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="Ej.: ¿Cobran cargo por cancelación con 12 horas de anticipación?"
            disabled={loading}
          />
          <div className="knowledge-actions">
            <button disabled={loading}>{loading ? "Consultando…" : "Preguntar"}</button>
            <small>Enter para enviar · Shift + Enter para nueva línea</small>
          </div>
        </form>
        <div className="knowledge-examples" aria-label="Preguntas de ejemplo">
          {EXAMPLES.map((example) => (
            <button key={example} type="button" className="knowledge-chip" disabled={loading}
              onClick={() => { setQuestion(example); void ask(example); }}>
              {example}
            </button>
          ))}
        </div>
      </section>

      <section className="panel knowledge-result" aria-live="polite" aria-busy={loading}>
        {state.status === "idle" && <p className="knowledge-muted">La respuesta aparecerá aquí.</p>}
        {state.status !== "idle" && <p className="knowledge-question"><strong>Pregunta:</strong> {state.question}</p>}
        {loading && <p role="status" className="knowledge-muted">Buscando en la base de conocimiento…</p>}
        {state.status === "error" && <p className="error" role="alert">{state.message}</p>}
        {state.status === "answered" && (
          <>
            <div className="knowledge-answer">{body}</div>
            {source && <p className="knowledge-source">{source}</p>}
          </>
        )}
      </section>
    </main>
  );
}

/** The model ends with "Fuente: …" (usually on its own line); show it apart from the answer body. */
function splitSource(answer: string): [string, string] {
  const text = answer.trim();
  const match = /(?:^|\s)(\*{0,2}Fuentes?:\*{0,2}[^\n]*)$/.exec(text);
  if (!match) return [text, ""];
  const source = match[1].replace(/\*\*|\[|\]/g, "").trim();
  return [text.slice(0, match.index).trim(), source];
}
