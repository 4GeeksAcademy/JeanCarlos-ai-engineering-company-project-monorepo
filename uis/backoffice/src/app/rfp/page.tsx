"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

// RFP intake in ticket mode (HealthCore BRIEFING, Hito 9 Parte 1). Revenue Cycle uploads the PDF
// (POST /api/rfp/tickets → 202 + ticket_id); the agentic flow runs in the background and this page
// polls the ticket until it is `analisis_completo` or `descartado`. Part 2: the per-department
// generators and the parallel evaluators move it to `generando_borrador` → `en_evaluación` (→
// `needs_human_review`); each section shows its draft and its EvaluationResult.

type Status = "analizando" | "descartado" | "analisis_completo" | string;

type TicketRow = {
  ticket_id: string; rfp_id: string; status: Status; stage: string; error: string | null; phi_detected: boolean;
  ready_for_generation: boolean; updated_at: string; client_name: string | null; client_country: string | null; created_at: string;
};

type Aspect = { aspect: string; detail: string; source: string };
type Workstream = {
  department_id: string; department: string; owner: string; owner_role: string; key_aspects: Aspect[];
  requests: string[]; open_questions: string[]; worker: string;
};
type Violation = { rule_id: string; message: string };
type Evaluation = {
  iteration?: number; overall_pass: boolean; contains_phi: boolean; feedback_for_generator: string; generator_error?: string;
  readability?: { pass: boolean; score: number | null; details: { words?: number; flesch_reading_ease?: number; issues?: string[] } };
  relevance?: { pass: boolean; score: number; missing_aspects: string[]; covered_aspects: string[]; evaluator: string };
  compliance?: { pass: boolean; rule_ids: string[]; violations: Violation[]; contains_phi: boolean };
};
type Section = {
  department_id: string; owner: string; generation_status: string; iterations: number; draft_content: string | null;
  evaluation_results: Evaluation | null; evaluation_history: { iteration: number; evaluation: Evaluation }[] | null;
};
type Ticket = TicketRow & {
  classification: { decision: string; document_type: string; confidence: number; reasons: string[] } | null;
  phi_categories: string[] | null; compliance_review_required: boolean; ready_for_generation: boolean;
  metadata: Record<string, unknown> | null;
  synthesis: {
    summary: string; next_steps: string[]; workstreams: Workstream[];
    conflicts: { field: string; resolution: string }[]; open_questions: { department_id: string; question: string }[];
  } | null;
  handoff: { contract: string; route: string; required_clauses: string[] } | null;
  sections: Section[];
  response_handoff: { contract: string; max_iterations: number; needs_human_review: string[]; signals: { trigger: string; department_id: string }[] } | null;
  ready_for_approval: boolean;
};

const STATUS_LABEL: Record<string, string> = {
  analizando: "Analizando", descartado: "Descartado", analisis_completo: "Análisis completo",
  generando_borrador: "Generando borrador", "en_evaluación": "En evaluación", needs_human_review: "Revisión humana",
};
const SECTION_LABEL: Record<string, string> = {
  not_started: "Pendiente", generating: "Generando", evaluating: "Evaluando", revising: "Corrigiendo con feedback",
  passed: "Pasó la evaluación", needs_human_review: "Revisión humana",
};
const DEPARTMENT_NAME: Record<string, string> = {
  revenue: "Revenue Cycle", clinical: "Clinical Operations", compliance: "Compliance and Data Governance",
};
const RUNNING = new Set(["analizando", "generando_borrador", "en_evaluación"]);
const STOPPED_STAGES = new Set(["failed", "generation_failed", "generated", "done", "discarded"]);
const STAGE_LABEL: Record<string, string> = {
  received: "Recibido", converting: "Convirtiendo PDF → Markdown", screening_phi: "Revisando PHI",
  classifying: "Clasificando", extracting: "Extrayendo metadatos", orchestrating: "Orquestando departamentos",
  workers: "Workers por departamento", synthesizing: "Sintetizando", done: "Listo", discarded: "Descartado",
  failed: "Error", generating: "Generadores por departamento", evaluating: "Evaluadores en paralelo",
  generated: "Borradores evaluados", generation_failed: "Error en la generación",
};
const POLL_MS = 3000;

async function readJson(response: Response): Promise<unknown> {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof body === "object" && body && "detail" in body ? (body as { detail: unknown }).detail : null;
    if (response.status === 401) throw new Error("Tu sesión expiró. Vuelve a iniciar sesión.");
    throw new Error(typeof detail === "string" && response.status < 500 ? detail : "El servicio no está disponible.");
  }
  return body;
}

export default function RfpIntakePage() {
  const [tickets, setTickets] = useState<TicketRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Ticket | null>(null);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [justQueued, setJustQueued] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const rows = (await readJson(await fetch("/api/rfp/tickets", { cache: "no-store" }))) as TicketRow[];
      setTickets(rows);
      // a ticket that just finished Part 1 is picked up by Part 2 automatically: keep polling briefly
      setJustQueued(rows.some((t) => t.status === "analisis_completo" && t.ready_for_generation
        && Date.now() - new Date(t.updated_at).getTime() < 60_000));
      if (selected) setDetail((await readJson(await fetch(`/api/rfp/tickets/${selected}`, { cache: "no-store" }))) as Ticket);
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "Error al consultar los tickets." });
    }
  }, [selected]);

  const pending = justQueued || tickets.some((t) => RUNNING.has(t.status) && !STOPPED_STAGES.has(t.stage));
  useEffect(() => {
    const timer = setTimeout(() => void refresh(), 0); // first load and whenever the selected ticket changes
    return () => clearTimeout(timer);
  }, [refresh]);
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [pending, refresh]);

  async function generate(ticketId: string, retry: boolean) {
    setMessage(null);
    try {
      await readJson(await fetch(`/api/rfp/tickets/${ticketId}/generate${retry ? "?retry=true" : ""}`, { method: "POST" }));
      setMessage({ kind: "ok", text: "Generación en curso: generadores por departamento y evaluadores en paralelo." });
      await refresh();
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "No se pudo iniciar la generación." });
    }
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) return setMessage({ kind: "error", text: "Selecciona un PDF." });
    if (file.type && file.type !== "application/pdf") return setMessage({ kind: "error", text: "Solo se aceptan PDF." });
    const form = new FormData();
    form.append("file", file);
    setUploading(true);
    setMessage(null);
    try {
      const body = (await readJson(await fetch("/api/rfp/tickets", { method: "POST", body: form }))) as TicketRow;
      setMessage({ kind: "ok", text: `Ticket ${body.rfp_id} creado: analizando en segundo plano.` });
      setSelected(body.ticket_id);
      if (fileInput.current) fileInput.current.value = "";
      await refresh();
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "No se pudo subir el PDF." });
    } finally {
      setUploading(false);
    }
  }

  return (
    <main className="rfp-page">
      <h1>Recepción de RFPs</h1>
      <p>
        Sube la RFP institucional en PDF. El flujo la convierte a Markdown, retiene cualquier dato de paciente, decide si
        es una RFP de HealthCore y reparte el análisis entre Revenue Cycle, Clinical Operations y Compliance.
      </p>

      <section className="panel" aria-label="Subir RFP">
        <form className="rfp-upload" onSubmit={upload}>
          <label htmlFor="rfp-file">RFP (PDF, máx. 10 MB)</label>
          <input id="rfp-file" ref={fileInput} type="file" accept="application/pdf,.pdf" disabled={uploading} />
          <button disabled={uploading}>{uploading ? "Subiendo…" : "Crear ticket"}</button>
        </form>
        {message && <p className={message.kind === "error" ? "error" : "rfp-ok"} role={message.kind === "error" ? "alert" : "status"}>{message.text}</p>}
      </section>

      <section className="panel" aria-label="Tickets" aria-busy={pending}>
        <h2>Tickets {pending && <small className="rfp-muted">· actualizando cada {POLL_MS / 1000} s</small>}</h2>
        {tickets.length === 0 ? <p className="rfp-muted">Aún no hay RFPs.</p> : (
          <div className="rfp-table-wrap">
            <table className="rfp-table">
              <thead><tr><th>Ticket</th><th>Cliente</th><th>País</th><th>Estado</th><th>Etapa</th><th /></tr></thead>
              <tbody>
                {tickets.map((t) => (
                  <tr key={t.ticket_id} className={t.ticket_id === selected ? "rfp-selected" : undefined}>
                    <td>{t.rfp_id}</td>
                    <td>{t.client_name ?? "—"}</td>
                    <td>{t.client_country ?? "—"}</td>
                    <td><StatusBadge status={t.status} failed={t.stage === "failed" || t.stage === "generation_failed"} phi={t.phi_detected} /></td>
                    <td>{STAGE_LABEL[t.stage] ?? t.stage}</td>
                    <td><button type="button" className="rfp-link" onClick={() => setSelected(t.ticket_id)}>Ver</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && detail && detail.ticket_id === selected && <TicketDetail ticket={detail} onGenerate={generate} />}
    </main>
  );
}

function StatusBadge({ status, failed, phi }: { status: Status; failed: boolean; phi: boolean }) {
  return (
    <span className="rfp-badges">
      <span className={`rfp-badge rfp-${failed ? "failed" : status.replace("ó", "o")}`}>{failed ? "Error (reintentar)" : STATUS_LABEL[status] ?? status}</span>
      {phi && <span className="rfp-badge rfp-phi" title="Se detectó y redactó posible PHI">PHI redactada</span>}
    </span>
  );
}

function TicketDetail({ ticket, onGenerate }: { ticket: Ticket; onGenerate: (id: string, retry: boolean) => void }) {
  const m = ticket.metadata ?? {};
  const s = ticket.synthesis;
  const field = (key: string) => (m[key] === null || m[key] === undefined || m[key] === "" ? "—" : String(m[key]));
  return (
    <section className="panel rfp-detail" aria-live="polite">
      <h2>{ticket.rfp_id} · {STATUS_LABEL[ticket.status] ?? ticket.status}</h2>
      {ticket.stage === "failed" && <p className="error" role="alert">El análisis falló en «{ticket.error}». El ticket sigue en analizando; puede reprocesarse.</p>}
      {ticket.status === "analizando" && ticket.stage !== "failed" && <p className="rfp-muted" role="status">{STAGE_LABEL[ticket.stage] ?? ticket.stage}…</p>}
      {ticket.phi_detected && (
        <p className="rfp-warning">Se detectó posible información de pacientes ({(ticket.phi_categories ?? []).join(", ")}). El contenido fue
          redactado antes de cualquier agente y el ticket requiere revisión humana de Compliance (Claire Whitfield) antes de la Parte 2.</p>
      )}
      {ticket.classification && (
        <p><strong>Clasificador:</strong> {ticket.classification.decision === "discard" ? "no es una RFP" : "RFP válida"} ·{" "}
          {ticket.classification.document_type} · confianza {Math.round(ticket.classification.confidence * 100)} % —{" "}
          {ticket.classification.reasons.join(" ")}</p>
      )}
      {ticket.metadata && (
        <dl className="rfp-meta">
          <dt>Cliente</dt><dd>{field("client_name")} ({field("client_country")})</dd>
          <dt>Programa</dt><dd>{((m.program_types as string[]) ?? []).join(", ") || "—"}</dd>
          <dt>Población cubierta</dt><dd>{field("covered_population")} {m.population_unit ? String(m.population_unit) : ""}</dd>
          <dt>Fecha límite</dt><dd>{field("deadline")} {m.deadline_text ? `(«${String(m.deadline_text)}»)` : ""}</dd>
          <dt>Presupuesto</dt><dd>{field("budget_range")}</dd>
          <dt>Moneda · regulación</dt><dd>{field("currency")} · {field("regulation")} · {field("agreement_type")}</dd>
          <dt>Legibilidad / costo</dt>
          <dd>{field("word_count")} palabras · ~{field("estimated_tokens")} tokens · FK {field("flesch_kincaid_grade")} · Fog {field("gunning_fog")} · costo {field("cost_band")}</dd>
        </dl>
      )}
      {s && (
        <>
          <h3>Resumen para Revenue Cycle</h3>
          <p className="rfp-summary">{s.summary}</p>
          <h3>Próximos pasos</h3>
          <ul>{s.next_steps.map((step) => <li key={step}>{step}</li>)}</ul>
          <div className="rfp-workstreams">
            {s.workstreams.map((w) => (
              <article key={w.department_id} className="rfp-card">
                <h4>{w.department}</h4>
                <p className="rfp-muted">Pedírselo a <strong>{w.owner}</strong> ({w.owner_role}){w.worker === "fallback" ? " · revisión manual" : ""}</p>
                <ul>{w.key_aspects.map((a) => <li key={a.aspect}><strong>{a.aspect}:</strong> {a.detail}</li>)}</ul>
                {w.requests.length > 0 && <><h5>Qué pedirle</h5><ul>{w.requests.map((r) => <li key={r}>{r}</li>)}</ul></>}
                {w.open_questions.length > 0 && <><h5>Preguntas abiertas</h5><ul>{w.open_questions.map((q) => <li key={q}>{q}</li>)}</ul></>}
              </article>
            ))}
          </div>
          {s.open_questions.length > 0 && (<><h3>Datos que faltan en la RFP</h3><ul>{s.open_questions.map((q) => <li key={q.question}>{q.question} ({q.department_id})</li>)}</ul></>)}
          {s.conflicts.length > 0 && (<><h3>Contradicciones detectadas</h3><ul>{s.conflicts.map((c) => <li key={c.field}>{c.field}: {c.resolution}</li>)}</ul></>)}
          {ticket.handoff && (
            <p className="rfp-muted">Handoff <code>{ticket.handoff.contract}</code> → <strong>{ticket.handoff.route}</strong> ·
              cláusulas: {ticket.handoff.required_clauses.join(", ")} · {ticket.ready_for_generation ? "listo para la Parte 2" : ticket.handoff.route === "proposal_generation" ? "tomado por la Parte 2" : "bloqueado hasta revisión de Compliance"}</p>
          )}
          {ticket.status === "analisis_completo" && ticket.ready_for_generation && (
            <button type="button" onClick={() => onGenerate(ticket.ticket_id, false)}>Generar borrador de la propuesta</button>
          )}
          {ticket.stage === "generation_failed" && (
            <p className="error" role="alert">La generación falló en «{ticket.error}».{" "}
              <button type="button" onClick={() => onGenerate(ticket.ticket_id, true)}>Reintentar</button></p>
          )}
          <ProposalSections ticket={ticket} />
        </>
      )}
    </section>
  );
}

function ProposalSections({ ticket }: { ticket: Ticket }) {
  const started = ticket.sections.some((s) => s.generation_status !== "not_started");
  if (!started) return null;
  const h = ticket.response_handoff;
  return (
    <>
      <h3>Propuesta económica — borradores por departamento</h3>
      {RUNNING.has(ticket.status) && ticket.stage !== "generated" && (
        <p className="rfp-muted" role="status">{STAGE_LABEL[ticket.stage] ?? ticket.stage}…</p>
      )}
      {ticket.status === "needs_human_review" && (
        <p className="rfp-warning">Una o más secciones agotaron el límite de iteraciones sin pasar la evaluación. Se conserva su último
          borrador y su resultado de evaluación, y pasan igual a la Parte 3 para revisión humana.</p>
      )}
      <div className="rfp-workstreams">
        {ticket.sections.map((s) => <SectionCard key={s.department_id} section={s} max={h?.max_iterations} />)}
      </div>
      {h && (
        <p className="rfp-muted">Handoff <code>{h.contract}</code> → Parte 3 (aprobación por departamento; Compliance obligatorio)
          {h.needs_human_review.length > 0 ? ` · revisión humana: ${h.needs_human_review.join(", ")}` : " · todas las secciones pasaron"}
          {h.signals.length > 0 ? ` · señales: ${h.signals.map((x) => `${x.trigger} (${x.department_id})`).join(", ")}` : ""}</p>
      )}
    </>
  );
}

function Check({ ok, label, children }: { ok: boolean | undefined; label: string; children?: React.ReactNode }) {
  return (
    <li><span className={`rfp-badge ${ok ? "rfp-pass" : "rfp-fail"}`}>{ok ? "✓" : "✗"} {label}</span> {children}</li>
  );
}

function SectionCard({ section, max }: { section: Section; max?: number }) {
  const e = section.evaluation_results;
  const evaluated = e && "overall_pass" in e && (e.readability || e.generator_error);
  return (
    <article className="rfp-card rfp-section">
      <h4>{DEPARTMENT_NAME[section.department_id] ?? section.department_id}</h4>
      <p className="rfp-muted">
        <span className={`rfp-badge rfp-gen-${section.generation_status}`}>{SECTION_LABEL[section.generation_status] ?? section.generation_status}</span>{" "}
        iteración {section.iterations}{max ? `/${max}` : ""} · aprueba {section.owner}
      </p>
      {evaluated && e && (
        <ul className="rfp-eval">
          {e.generator_error ? <Check ok={false} label="Generador">{e.generator_error}</Check> : (
            <>
              <Check ok={e.readability?.pass} label="Legibilidad">
                FK {e.readability?.score ?? "—"} · {e.readability?.details.words ?? "—"} palabras
                {e.readability?.details.issues?.length ? ` · ${e.readability.details.issues.join("; ")}` : ""}
              </Check>
              <Check ok={e.relevance?.pass} label="Pertinencia">
                {Math.round((e.relevance?.score ?? 0) * 100)} % de aspectos clave
                {e.relevance?.missing_aspects.length ? ` · faltan: ${e.relevance.missing_aspects.join(", ")}` : ""}
              </Check>
              <Check ok={e.compliance?.pass} label="Compliance">
                {e.compliance?.violations.length ? e.compliance.violations.map((v) => v.rule_id).join(", ") : `${e.compliance?.rule_ids.length} reglas OK`}
                {e.contains_phi ? " · contains_phi" : ""}
              </Check>
            </>
          )}
        </ul>
      )}
      {e && !e.overall_pass && e.feedback_for_generator && (
        <details><summary>Feedback al generador</summary><pre className="rfp-pre">{e.feedback_for_generator}</pre></details>
      )}
      {section.draft_content && (
        <details open={section.generation_status === "passed"}><summary>Borrador</summary><pre className="rfp-pre">{section.draft_content}</pre></details>
      )}
      {(section.evaluation_history?.length ?? 0) > 1 && (
        <p className="rfp-muted">Historial: {section.evaluation_history!.map((x) => `it. ${x.iteration} ${x.evaluation.overall_pass ? "✓" : "✗"}`).join(" → ")}</p>
      )}
    </article>
  );
}
