"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { openEventStream, parseTicketCreated, TicketDeduper, type RfpTicketCreated, type StreamStatus } from "@/lib/sse";

// RFP intake in ticket mode (HealthCore BRIEFING, Hito 9 Parte 1). Revenue Cycle uploads the PDF
// (POST /api/rfp/tickets → 202 + ticket_id); the agentic flow runs in the background and this page
// polls the ticket until it is `analisis_completo` or `descartado`. Part 2: the per-department
// generators and the parallel evaluators move it to `generando_borrador` → `en_evaluación` (→
// `needs_human_review`); each section shows its draft and its EvaluationResult. Part 3: the ticket waits
// in `esperando_aprobación` while each department's named approver approves, requests changes or
// rejects its section (each on its own paused thread); the fixed arbiter of a BRIEFING §7 trigger
// rules here too; with every approval the final document is generated (`terminado`).
//
// Real time (Sistemas en Tiempo Real, Parte 1): the page keeps one Server-Sent Events stream open
// (fetch + ReadableStream, src/lib/sse.ts). A new RFP ticket arrives as `rfp_ticket_created`: it is
// announced in its own "Nueva RFP" panel and inserted into the list from the event payload — no
// reload, no refetch of the list per event. If the connection drops it reconnects with backoff and
// Last-Event-ID (the API replays what was missed); tickets are deduplicated by ticket_id.

type Status = "analizando" | "descartado" | "analisis_completo" | string;

type TicketRow = {
  ticket_id: string; rfp_id: string; status: Status; stage: string; error: string | null; phi_detected: boolean;
  ready_for_generation: boolean; updated_at: string; client_name: string | null; client_country: string | null; created_at: string;
  pending_arbitration?: Arbitration | null;
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
type Trigger = { trigger: string; arbiter: string; rule: string; evidence: Record<string, unknown> };
type Packet = {
  approver: string; approver_role: string; department: string; needs_human_review: boolean; iterations: number;
  words: number; open_questions: string[]; triggers: Trigger[]; revision_rounds: number; max_revision_rounds: number;
  allowed_actions: string[]; blocked_reasons: Record<string, string>; requires_capacity: boolean;
  client: { country: string | null; currency: string | null; instrument: string | null; covered_population: number | null; population_unit: string | null };
  evaluation: { overall_pass: boolean | null; relevance: { missing: string[] }; compliance: { violations: string[] }; contains_phi: boolean };
};
type Decision = { action: string; by: string; comment: string | null; confirmed_capacity?: number | null; at: string; trigger?: string };
type Arbitration = {
  trigger: string; arbiter: string; rule: string; departments: string[]; evidence: Record<string, unknown>; options: string[];
  round: number; max_rounds: number; binding: boolean;
};
type ArbitrationEntry = {
  round: number; trigger: string; arbiter: string; decided_by: string; resolution: string; departments: string[];
  agreed_population?: number; comment?: string | null; at: string;
};
type Section = {
  department_id: string; owner: string; generation_status: string; iterations: number; draft_content: string | null;
  evaluation_results: Evaluation | null; evaluation_history: { iteration: number; evaluation: Evaluation }[] | null;
  approval_status: string; approver: string | null; approved_at: string | null; approval_packet: Packet | null;
  approval_comment: string | null; confirmed_capacity: number | null; revision_rounds: number; decision_history: Decision[] | null;
};
type ApprovalState = {
  threads: Record<string, { thread_id: string; paused_at: string }>;
  limits: { max_revision_rounds: number; max_arbitration_rounds: number }; approval_round: number;
  final_document_available: boolean;
};
type FinalDocument = { rfp_id: string; currency: string; generated_at: string; markdown: string };
type TraceRow = {
  id: number; thread_id: string | null; node: string; agent: string; status: string; started_at: string; finished_at: string;
  output: unknown;
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
  arbitration: ArbitrationEntry[] | null; pending_arbitration: Arbitration | null; approval_round: number;
};

const STATUS_LABEL: Record<string, string> = {
  analizando: "Analizando", descartado: "Descartado", analisis_completo: "Análisis completo",
  generando_borrador: "Generando borrador", "en_evaluación": "En evaluación", needs_human_review: "Revisión humana",
  "esperando_aprobación": "Esperando aprobación", terminado: "Terminado",
};
const SECTION_LABEL: Record<string, string> = {
  not_started: "Pendiente", generating: "Generando", evaluating: "Evaluando", revising: "Corrigiendo con feedback",
  passed: "Pasó la evaluación", needs_human_review: "Revisión humana",
};
const DEPARTMENT_NAME: Record<string, string> = {
  revenue: "Revenue Cycle", clinical: "Clinical Operations", compliance: "Compliance and Data Governance",
};
const APPROVAL_LABEL: Record<string, string> = {
  pending: "Pendiente de aprobación", approved: "Aprobada", request_changes: "Cambios solicitados", rejected: "Rechazada",
};
const ACTION_LABEL: Record<string, string> = {
  approve: "Aprobar", request_changes: "Solicitar cambios", reject: "Rechazar", reopen: "Reabierta por arbitraje", cancel: "Cancelada",
};
const PAUSED_LABEL: Record<string, string> = {
  await_approval: "⏸ esperando a su aprobador", await_closure: "⏸ aprobada, esperando el cierre", ended: "hilo terminado",
  not_started: "sin iniciar", revise: "revisando", await_departments: "⏸ esperando a los departamentos",
  arbiter_decision: "⏸ esperando al árbitro",
};
const RESOLUTION_LABEL: Record<string, string> = {
  reduce_population: "Reducir la población cubierta", add_sites: "Añadir sedes", discard: "Descartar el ticket",
  request_new_run: "Pedir una nueva ejecución", request_changes: "Devolver la sección (regla)",
};
// esperando_aprobación: approvals and revisions happen in the background, so the page keeps polling
const RUNNING = new Set(["analizando", "generando_borrador", "en_evaluación", "esperando_aprobación"]);
const STOPPED_STAGES = new Set(["failed", "generation_failed", "generated", "done", "discarded", "approval_failed"]);
const STAGE_LABEL: Record<string, string> = {
  received: "Recibido", converting: "Convirtiendo PDF → Markdown", screening_phi: "Revisando PHI",
  classifying: "Clasificando", extracting: "Extrayendo metadatos", orchestrating: "Orquestando departamentos",
  workers: "Workers por departamento", synthesizing: "Sintetizando", done: "Listo", discarded: "Descartado",
  failed: "Error", generating: "Generadores por departamento", evaluating: "Evaluadores en paralelo",
  generated: "Borradores evaluados", generation_failed: "Error en la generación",
  awaiting_approval: "Aprobación por departamento", arbitration: "Arbitraje (BRIEFING §7)",
  finalizing: "Generando el documento final", completed: "Documento final listo",
  approval_rejected: "Rechazada: requiere una nueva ejecución", discarded_by_compliance: "Descartada por Compliance",
  approval_failed: "Error en la aprobación",
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
  const [live, setLive] = useState<StreamStatus>({ state: "connecting", attempt: 0 });
  const [arrivals, setArrivals] = useState<Arrival[]>([]);
  const [newIds, setNewIds] = useState<Set<string>>(() => new Set());
  const [listError, setListError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const rows = (await readJson(await fetch("/api/rfp/tickets", { cache: "no-store" }))) as TicketRow[];
      // a ticket that arrived through the stream after this list was read is kept
      setTickets((current) => [...current.filter((t) => !rows.some((r) => r.ticket_id === t.ticket_id)), ...rows]
        .sort((a, b) => b.created_at.localeCompare(a.created_at)));
      // a ticket that just finished Part 1 is picked up by Part 2 automatically: keep polling briefly
      setJustQueued(rows.some((t) => t.status === "analisis_completo" && t.ready_for_generation
        && Date.now() - new Date(t.updated_at).getTime() < 60_000));
      if (selected) setDetail((await readJson(await fetch(`/api/rfp/tickets/${selected}`, { cache: "no-store" }))) as Ticket);
      setListError(null); // the service is back: the warning goes away on its own
    } catch (error) {
      setListError(error instanceof Error ? error.message : "Error al consultar los tickets.");
    }
  }, [selected]);

  const refreshRef = useRef(refresh);
  useEffect(() => { refreshRef.current = refresh; }, [refresh]);

  // One SSE stream for the whole page (src/lib/sse.ts: fetch + ReadableStream, backoff, Last-Event-ID).
  useEffect(() => {
    const deduper = new TicketDeduper();
    let firstReady = true;
    let replayLeft = 0;
    const stream = openEventStream({
      url: "/api/rfp/events",
      onStatus: setLive,
      onEvent: (frame) => {
        if (frame.event === "stream_ready") {
          replayLeft = (JSON.parse(frame.data) as { replayed: number }).replayed;
          // the only connection without a cursor is the first one: read the list once more so nothing
          // registered between the page load and the stream start is lost. From this frame on the
          // client holds the cursor (stream_ready carries `id:`), so every reconnection — even a cut
          // before the first ticket — sends Last-Event-ID and gets the replay of what it missed.
          if (firstReady) void refreshRef.current();
          firstReady = false;
          return;
        }
        // the gap is too large to replay: read the list once; the cursor moved to the newest event
        // (resync_required carries `id:`), so the next reconnection does not ask to resync again
        if (frame.event === "resync_required") return void refreshRef.current();
        const ticket = parseTicketCreated(frame);
        if (!ticket) return;
        const recovered = replayLeft > 0;
        if (replayLeft > 0) replayLeft -= 1;
        if (!deduper.accept(ticket.ticket_id)) return; // the same ticket is never announced twice
        setArrivals((current) => [{ ...ticket, received_at: new Date().toISOString(), recovered }, ...current].slice(0, 8));
        setNewIds((current) => new Set(current).add(ticket.ticket_id));
        setTickets((current) => current.some((t) => t.ticket_id === ticket.ticket_id) ? current : [rowFromEvent(ticket), ...current]);
      },
    });
    const online = () => stream.reconnectNow();
    window.addEventListener("online", online);
    return () => {
      window.removeEventListener("online", online);
      stream.close();
    };
  }, []);

  function open(ticketId: string) {
    setSelected(ticketId);
    setArrivals((current) => current.filter((a) => a.ticket_id !== ticketId));
    setNewIds((current) => {
      const next = new Set(current);
      next.delete(ticketId);
      return next;
    });
  }

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

      {arrivals.length > 0 && (
        <section className="panel rfp-live" aria-label="Nuevas RFP" aria-live="polite">
          <h2>Nuevas RFP en tiempo real</h2>
          <ul className="rfp-arrivals">
            {arrivals.map((a) => (
              <li key={a.ticket_id} className="rfp-arrival">
                <div>
                  <strong>Nueva RFP · requiere procesamiento</strong>{a.recovered ? <span className="rfp-badge rfp-recovered">recuperada tras reconexión</span> : null}
                  <p>{a.rfp_id} · {a.client_name ?? "cliente por confirmar"} ({a.client_country ?? "—"}) · {a.program_type ?? "programa por confirmar"} ·{" "}
                    <span className="rfp-badge rfp-analizando">{STATUS_LABEL[a.status] ?? a.status}</span></p>
                  <p className="rfp-muted">Registrada {new Date(a.created_at).toLocaleTimeString("es")} · recibida {new Date(a.received_at).toLocaleTimeString("es")}</p>
                </div>
                <div className="rfp-actions">
                  <button type="button" onClick={() => open(a.ticket_id)}>Ver ticket</button>
                  <button type="button" className="rfp-secondary" onClick={() => setArrivals((c) => c.filter((x) => x.ticket_id !== a.ticket_id))}>Descartar aviso</button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel" aria-label="Tickets" aria-busy={pending}>
        <h2>Tickets <LiveBadge status={live} /> {pending && <small className="rfp-muted">· progreso cada {POLL_MS / 1000} s</small>}</h2>
        {listError && <p className="error" role="alert">{listError} Se reintenta automáticamente.</p>}
        {tickets.length === 0 ? <p className="rfp-muted">Aún no hay RFPs.</p> : (
          <div className="rfp-table-wrap">
            <table className="rfp-table">
              <thead><tr><th>Ticket</th><th>Cliente</th><th>País</th><th>Estado</th><th>Etapa</th><th /></tr></thead>
              <tbody>
                {tickets.map((t) => (
                  <tr key={t.ticket_id} className={[t.ticket_id === selected ? "rfp-selected" : "", newIds.has(t.ticket_id) ? "rfp-new" : ""].join(" ").trim() || undefined}>
                    <td>{t.rfp_id} {newIds.has(t.ticket_id) && <span className="rfp-badge rfp-new-badge">Nueva</span>}</td>
                    <td>{t.client_name ?? "—"}</td>
                    <td>{t.client_country ?? "—"}</td>
                    <td><StatusBadge status={t.status} failed={["failed", "generation_failed", "approval_failed"].includes(t.stage)} phi={t.phi_detected} /></td>
                    <td>{STAGE_LABEL[t.stage] ?? t.stage}</td>
                    <td><button type="button" className="rfp-link" onClick={() => open(t.ticket_id)}>Ver</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && detail && detail.ticket_id === selected && (
        <TicketDetail ticket={detail} onGenerate={generate} onChanged={refresh} />
      )}
    </main>
  );
}

type Arrival = RfpTicketCreated & { received_at: string; recovered: boolean };

function rowFromEvent(t: RfpTicketCreated): TicketRow {
  // the event is emitted right after the metadata extraction: the ticket is analizando / extracting
  return { ticket_id: t.ticket_id, rfp_id: t.rfp_id, status: t.status, stage: "extracting", error: null, phi_detected: false,
    ready_for_generation: false, updated_at: t.created_at, client_name: t.client_name, client_country: t.client_country,
    created_at: t.created_at };
}

function LiveBadge({ status }: { status: StreamStatus }) {
  if (status.state === "open") return <span className="rfp-live-badge rfp-live-on" role="status">● En vivo</span>;
  if (status.state === "expired") return <span className="rfp-live-badge rfp-live-off" role="alert">Sesión expirada: vuelve a iniciar sesión</span>;
  if (status.state === "reconnecting") {
    const wait = status.retryInMs !== undefined ? ` en ${Math.ceil(status.retryInMs / 1000)} s` : "…";
    return <span className="rfp-live-badge rfp-live-retry" role="status">Sin conexión en tiempo real · reintento {Math.max(1, status.attempt)}{wait}</span>;
  }
  return <span className="rfp-live-badge" role="status">Conectando…</span>;
}

function StatusBadge({ status, failed, phi }: { status: Status; failed: boolean; phi: boolean }) {
  return (
    <span className="rfp-badges">
      <span className={`rfp-badge rfp-${failed ? "failed" : status.replace("ó", "o")}`}>{failed ? "Error (reintentar)" : STATUS_LABEL[status] ?? status}</span>
      {phi && <span className="rfp-badge rfp-phi" title="Se detectó y redactó posible PHI">PHI redactada</span>}
    </span>
  );
}

function TicketDetail({ ticket, onGenerate, onChanged }: {
  ticket: Ticket; onGenerate: (id: string, retry: boolean) => void; onChanged: () => Promise<void>;
}) {
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
          {ticket.stage === "approval_rejected" && (
            <p className="rfp-warning" role="status">Un departamento rechazó su sección: el documento final no se generó. Revenue Cycle
              puede iniciar una nueva ejecución de la Parte 2.{" "}
              <button type="button" onClick={() => onGenerate(ticket.ticket_id, true)}>Nueva ejecución</button></p>
          )}
          <ProposalSections ticket={ticket} onChanged={onChanged} />
          <Closing ticket={ticket} />
        </>
      )}
    </section>
  );
}

const PART3 = new Set(["esperando_aprobación", "terminado"]);

function useApproval(ticket: Ticket): ApprovalState | null {
  const [state, setState] = useState<ApprovalState | null>(null);
  const inPart3 = PART3.has(ticket.status) || ["approval_rejected", "discarded_by_compliance", "approval_failed"].includes(ticket.stage);
  useEffect(() => {
    if (!inPart3) return;
    let alive = true;
    fetch(`/api/rfp/tickets/${ticket.ticket_id}/approval`, { cache: "no-store" })
      .then(readJson).then((body) => { if (alive) setState(body as ApprovalState); }).catch(() => undefined);
    return () => { alive = false; };
  }, [ticket.ticket_id, ticket.updated_at, inPart3, ticket.sections]);
  return inPart3 ? state : null;
}

function ProposalSections({ ticket, onChanged }: { ticket: Ticket; onChanged: () => Promise<void> }) {
  const approval = useApproval(ticket);
  const started = ticket.sections.some((s) => s.generation_status !== "not_started");
  if (!started) return null;
  const h = ticket.response_handoff;
  return (
    <>
      <h3>Propuesta económica — borradores por departamento</h3>
      {(ticket.status === "generando_borrador" || ticket.status === "en_evaluación") && ticket.stage !== "generated" && (
        <p className="rfp-muted" role="status">{STAGE_LABEL[ticket.stage] ?? ticket.stage}…</p>
      )}
      {ticket.status === "needs_human_review" && ticket.stage !== "approval_rejected" && (
        <p className="rfp-warning">Una o más secciones agotaron el límite de iteraciones sin pasar la evaluación. Se conserva su último
          borrador y su resultado de evaluación, y pasan igual a la Parte 3 para revisión humana.</p>
      )}
      <div className="rfp-workstreams">
        {ticket.sections.map((s) => (
          <SectionCard key={s.department_id} section={s} max={h?.max_iterations}>
            {approval && (
              <ApprovalBox ticketId={ticket.ticket_id} section={s} paused={approval.threads[s.department_id]}
                onChanged={onChanged} />
            )}
          </SectionCard>
        ))}
      </div>
      {approval && (
        <p className="rfp-muted">Hilos con checkpoint: {Object.entries(approval.threads).map(([k, t]) => `${k}: ${PAUSED_LABEL[t.paused_at] ?? t.paused_at}`).join(" · ")}
          {" "}· límite de revisiones por departamento {approval.limits.max_revision_rounds} · rondas de arbitraje {ticket.approval_round}/{approval.limits.max_arbitration_rounds}</p>
      )}
      {ticket.pending_arbitration && <ArbitrationBox ticketId={ticket.ticket_id} pending={ticket.pending_arbitration} onChanged={onChanged} />}
      {(ticket.arbitration?.length ?? 0) > 0 && (
        <>
          <h3>Registro de arbitraje</h3>
          <ul>{ticket.arbitration!.map((a, i) => (
            <li key={i}>Ronda {a.round} · <code>{a.trigger}</code> · árbitro fijo <strong>{a.arbiter}</strong> ({a.decided_by === "rule" ? "regla determinista" : "decisión humana"}) →{" "}
              {RESOLUTION_LABEL[a.resolution] ?? a.resolution}{a.agreed_population ? ` (${a.agreed_population})` : ""} · {a.departments.join(", ")}</li>
          ))}</ul>
        </>
      )}
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

function SectionCard({ section, max, children }: { section: Section; max?: number; children?: ReactNode }) {
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
      {children}
    </article>
  );
}

async function post(url: string, body: unknown): Promise<void> {
  await readJson(await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
}

function ApprovalBox({ ticketId, section, paused, onChanged }: {
  ticketId: string; section: Section; paused?: { thread_id: string; paused_at: string }; onChanged: () => Promise<void>;
}) {
  const [comment, setComment] = useState("");
  const [capacity, setCapacity] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const p = section.approval_packet;
  const waiting = paused?.paused_at === "await_approval";
  async function decide(action: string) {
    if (!p) return;
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { action, approver: p.approver, comment: comment.trim() || null };
      if (action === "approve" && p.requires_capacity) body.confirmed_capacity = Number(capacity) || null;
      await post(`/api/rfp/tickets/${ticketId}/sections/${section.department_id}/decision`, body);
      setComment("");
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo registrar la decisión.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="rfp-approval">
      <h5>Aprobación — {p?.approver ?? section.owner}</h5>
      <p className="rfp-muted">
        <span className={`rfp-badge rfp-ap-${section.approval_status}`}>{APPROVAL_LABEL[section.approval_status] ?? section.approval_status}</span>{" "}
        {paused ? PAUSED_LABEL[paused.paused_at] ?? paused.paused_at : ""}
        {section.approved_at ? ` · ${new Date(section.approved_at).toLocaleString("es")}` : ""}
        {section.confirmed_capacity ? ` · capacidad confirmada ${section.confirmed_capacity}` : ""}
      </p>
      {p && (
        <ul className="rfp-eval">
          <Check ok={p.evaluation.overall_pass ?? false} label={p.needs_human_review ? "Agotó el límite de la Parte 2" : "Evaluación automática"}>
            iteraciones {p.iterations}
            {p.evaluation.relevance.missing.length ? ` · faltan: ${p.evaluation.relevance.missing.join(", ")}` : ""}
            {p.evaluation.compliance.violations.length ? ` · reglas: ${p.evaluation.compliance.violations.join(", ")}` : ""}
          </Check>
          <li className="rfp-muted">Cliente {p.client.country ?? "—"} · {p.client.currency ?? "—"} · {p.client.instrument ?? "—"} · población {p.client.covered_population ?? "—"} {p.client.population_unit ?? ""}</li>
          {p.triggers.map((t) => (
            <Check key={t.trigger} ok={false} label={t.trigger}>árbitro {t.arbiter}</Check>
          ))}
          {p.open_questions.length > 0 && <li className="rfp-muted">Preguntas abiertas: {p.open_questions.join(" · ")}</li>}
          <li className="rfp-muted">Revisiones solicitadas {p.revision_rounds}/{p.max_revision_rounds}</li>
        </ul>
      )}
      {waiting && p && (
        <div className="rfp-decide">
          {p.requires_capacity && (
            <label>Capacidad confirmada ({p.client.population_unit ?? "personas"})
              <input type="number" min={1} value={capacity} onChange={(e) => setCapacity(e.target.value)} disabled={busy} />
            </label>
          )}
          <label>Comentario
            <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} disabled={busy}
              placeholder="Obligatorio para solicitar cambios o rechazar. Nunca datos de pacientes." />
          </label>
          <div className="rfp-actions">
            {(["approve", "request_changes", "reject"] as const).map((action) => (
              <button key={action} type="button" disabled={busy || !p.allowed_actions.includes(action)}
                title={p.blocked_reasons[action] ?? undefined} className={action === "approve" ? undefined : "rfp-secondary"}
                onClick={() => void decide(action)}>
                {action === "approve" ? `Aprobar como ${p.approver}` : ACTION_LABEL[action]}
              </button>
            ))}
          </div>
          {Object.values(p.blocked_reasons).map((r) => <p key={r} className="rfp-muted">{r}</p>)}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {section.approval_comment && <p className="rfp-muted">Comentario: «{section.approval_comment}»</p>}
      {(section.decision_history?.length ?? 0) > 0 && (
        <p className="rfp-muted">Decisiones: {section.decision_history!.map((d) => `${ACTION_LABEL[d.action] ?? d.action} (${d.by})`).join(" → ")}</p>
      )}
    </div>
  );
}

function ArbitrationBox({ ticketId, pending, onChanged }: { ticketId: string; pending: Arbitration; onChanged: () => Promise<void> }) {
  const [population, setPopulation] = useState(String(pending.evidence.confirmed_capacity ?? ""));
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function rule(resolution: string) {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/rfp/tickets/${ticketId}/arbitration`, {
        trigger: pending.trigger, arbiter: pending.arbiter, resolution, comment: comment.trim() || null,
        covered_population: resolution === "reduce_population" ? Number(population) || null : null,
      });
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo registrar el arbitraje.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="rfp-warning rfp-arbitration" role="region" aria-label="Arbitraje">
      <h3>Arbitraje: <code>{pending.trigger}</code> — decide {pending.arbiter}</h3>
      <p>{pending.rule}</p>
      <p className="rfp-muted">Evidencia: {Object.entries(pending.evidence).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}
        {" "}· ronda {pending.round}/{pending.max_rounds}{pending.binding ? " · límite alcanzado: decisión final sin otra ronda de revisión" : ""}</p>
      {pending.options.includes("reduce_population") && (
        <label>Población cubierta acordada
          <input type="number" min={1} value={population} onChange={(e) => setPopulation(e.target.value)} disabled={busy} />
        </label>
      )}
      <label>Comentario
        <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} disabled={busy} />
      </label>
      <div className="rfp-actions">
        {pending.options.map((o) => (
          <button key={o} type="button" disabled={busy} className={o === "request_new_run" || o === "discard" ? "rfp-secondary" : undefined}
            onClick={() => void rule(o)}>{RESOLUTION_LABEL[o] ?? o}</button>
        ))}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

function Closing({ ticket }: { ticket: Ticket }) {
  const [document, setDocument] = useState<FinalDocument | null>(null);
  const [trace, setTrace] = useState<TraceRow[] | null>(null);
  const done = ticket.status === "terminado";
  useEffect(() => {
    if (!done) return;
    let alive = true;
    fetch(`/api/rfp/tickets/${ticket.ticket_id}/document`, { cache: "no-store" })
      .then(readJson).then((body) => { if (alive) setDocument(body as FinalDocument); }).catch(() => undefined);
    return () => { alive = false; };
  }, [done, ticket.ticket_id]);
  async function loadTrace() {
    setTrace((await readJson(await fetch(`/api/rfp/tickets/${ticket.ticket_id}/trace`, { cache: "no-store" }))) as TraceRow[]);
  }
  function download() {
    if (!document) return;
    const url = URL.createObjectURL(new Blob([document.markdown], { type: "text/markdown;charset=utf-8" }));
    const link = Object.assign(window.document.createElement("a"), { href: url, download: `${document.rfp_id}.md` });
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <>
      {done && document && (
        <>
          <h3>Documento final para Revenue Cycle</h3>
          <p className="rfp-muted">Generado automáticamente al completarse todas las aprobaciones · {document.currency} ·{" "}
            {new Date(document.generated_at).toLocaleString("es")}{" "}
            <button type="button" onClick={download}>Descargar (.md)</button></p>
          <pre className="rfp-pre rfp-document">{document.markdown}</pre>
        </>
      )}
      <details className="rfp-trace" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open && !trace) void loadTrace(); }}>
        <summary>Trazabilidad: qué agente hizo qué y en qué orden</summary>
        {trace && (
          <div className="rfp-table-wrap">
            <table className="rfp-table">
              <thead><tr><th>#</th><th>Hora</th><th>Hilo</th><th>Nodo</th><th>Agente</th><th>Estado</th></tr></thead>
              <tbody>
                {trace.map((r) => (
                  <tr key={r.id}>
                    <td>{r.id}</td>
                    <td>{new Date(r.started_at).toLocaleTimeString("es")}</td>
                    <td>{(r.thread_id ?? "").replace(`rfp-${ticket.ticket_id}`, "") || "ticket"}</td>
                    <td>{r.node}</td>
                    <td>{r.agent}</td>
                    <td>{r.status === "interrupted" ? "⏸ interrumpido" : r.status === "error" ? "✗ error" : "✓"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </>
  );
}
