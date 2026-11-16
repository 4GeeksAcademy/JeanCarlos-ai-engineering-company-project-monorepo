"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  BRANCHES, CATEGORIES, ORIGINS, STATUSES, errorMessage, getSummary, type IncidentSummary,
  type Option,
} from "@/lib/incidents";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; summary: IncidentSummary };

function Breakdown({ title, options, counts }: { title: string; options: Option[]; counts: Record<string, number> }) {
  return (
    <section className="summary-card" aria-label={title}>
      <h3>{title}</h3>
      <dl>
        {options.map((option) => (
          <div key={option.value} className={counts[option.value] ? undefined : "zero"}>
            <dt>{option.label}</dt>
            <dd>{counts[option.value] ?? 0}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** Aggregated metrics; its own loading/error state never breaks the host page. */
export default function IncidentSummaryPanel({ refreshKey = 0, compact = false }: {
  refreshKey?: number;
  /** Compact: totals by status only, with a link to the full summary page. */
  compact?: boolean;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getSummary(controller.signal)
      .then((summary) => setState({ kind: "ready", summary }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setState({ kind: "error", message: errorMessage(error) });
      });
    return () => controller.abort();
  }, [refreshKey, attempt]);

  return (
    <section className="panel" aria-labelledby="summary-title" aria-busy={state.kind === "loading"}>
      <h2 id="summary-title">Summary</h2>
      {state.kind === "loading" && <p role="status"><span className="spinner dark" aria-hidden="true" /> Loading metrics…</p>}
      {state.kind === "error" && (
        <div className="notice error-notice" role="alert">
          <p>Metrics are not available right now. {state.message}</p>
          <button type="button" onClick={() => { setState({ kind: "loading" }); setAttempt((n) => n + 1); }}>
            Retry
          </button>
        </div>
      )}
      {state.kind === "ready" && compact && (
        <div className="summary-compact">
          <p className="summary-total"><strong>{state.summary.total}</strong> incidents</p>
          {STATUSES.map((option) => (
            <p key={option.value} className={`status-badge status-${option.value}`}>
              {option.label}: {state.summary.by_status[option.value] ?? 0}
            </p>
          ))}
          <Link href="/incidents/summary">View full summary</Link>
        </div>
      )}
      {state.kind === "ready" && !compact && (
        <>
          <p className="summary-total"><strong>{state.summary.total}</strong> incidents registered</p>
          <div className="summary-grid">
            <Breakdown title="By status" options={STATUSES} counts={state.summary.by_status} />
            <Breakdown title="By category" options={CATEGORIES} counts={state.summary.by_category} />
            <Breakdown title="By origin" options={ORIGINS} counts={state.summary.by_origin} />
            <Breakdown title="By clinic" options={BRANCHES} counts={state.summary.by_branch} />
          </div>
        </>
      )}
    </section>
  );
}
