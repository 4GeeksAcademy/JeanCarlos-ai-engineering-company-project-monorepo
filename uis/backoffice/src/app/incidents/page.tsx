"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import IncidentSummaryPanel from "@/components/IncidentSummaryPanel";
import {
  BRANCHES, CATEGORIES, ORIGINS, STATUSES, TRANSITIONS, branchLabel, categoryLabel,
  errorMessage, listIncidents, originLabel, statusLabel, updateIncidentStatus,
  type Incident, type IncidentFilters, type Option,
} from "@/lib/incidents";

const NO_FILTERS: IncidentFilters = { status: "", origin: "", branch: "", category: "" };
const dateFormat = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" });

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; incidents: Incident[] };

function FilterSelect({ label, name, options, value, onChange }: {
  label: string; name: keyof IncidentFilters; options: Option[]; value: string;
  onChange: (name: keyof IncidentFilters, value: string) => void;
}) {
  return (
    <label>
      {label}
      <select name={name} value={value} onChange={(event) => onChange(name, event.target.value)}>
        <option value="">All</option>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}

function StatusCell({ incident, pending, onChange }: {
  incident: Incident; pending: boolean; onChange: (incident: Incident, status: string) => void;
}) {
  const next = TRANSITIONS[incident.status] ?? [];
  if (next.length === 0) {
    return <span className={`status-badge status-${incident.status}`}>{statusLabel(incident.status)}</span>;
  }
  return (
    <label className="status-control">
      <span className="sr-only">Status of incident #{incident.id}</span>
      <select
        value={incident.status} disabled={pending} aria-busy={pending}
        className={`status-${incident.status}`}
        onChange={(event) => onChange(incident, event.target.value)}
      >
        <option value={incident.status}>{statusLabel(incident.status)}</option>
        {next.map((status) => <option key={status} value={status}>→ {statusLabel(status)}</option>)}
      </select>
      {pending && <small role="status">Saving…</small>}
    </label>
  );
}

export default function IncidentsPage() {
  const [filters, setFilters] = useState<IncidentFilters>(NO_FILTERS);
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [reload, setReload] = useState(0);
  const [pending, setPending] = useState<Set<number>>(new Set());
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [summaryKey, setSummaryKey] = useState(0);
  const filtered = Object.values(filters).some(Boolean);

  useEffect(() => {
    const controller = new AbortController();
    listIncidents(filters, controller.signal)
      .then((incidents) => setState({ kind: "ready", incidents }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setState({ kind: "error", message: errorMessage(error) });
      });
    return () => controller.abort();
  }, [filters, reload]);

  function changeFilter(name: keyof IncidentFilters, value: string) {
    setState({ kind: "loading" });
    setFilters((current) => ({ ...current, [name]: value }));
  }

  function retry() {
    setState({ kind: "loading" });
    setReload((n) => n + 1);
  }

  function replace(id: number, status: string) {
    setState((current) => current.kind !== "ready" ? current : {
      kind: "ready",
      incidents: current.incidents.map((item) => item.id === id ? { ...item, status } : item),
    });
  }

  async function changeStatus(incident: Incident, status: string) {
    if (status === incident.status) return;
    const previous = incident.status;
    setNotice(null);
    replace(incident.id, status); // optimistic
    setPending((current) => new Set(current).add(incident.id));
    try {
      const updated = await updateIncidentStatus(incident.id, status);
      replace(incident.id, updated.status);
      setNotice({ kind: "success", text: `Incident #${incident.id} is now ${statusLabel(updated.status).toLowerCase()}.` });
      setSummaryKey((n) => n + 1);
    } catch (error) {
      replace(incident.id, previous); // revert the visual state
      setNotice({
        kind: "error",
        text: `The status of incident #${incident.id} could not be changed and is still ${statusLabel(previous).toLowerCase()}. ${errorMessage(error)}`,
      });
    } finally {
      setPending((current) => {
        const next = new Set(current);
        next.delete(incident.id);
        return next;
      });
    }
  }

  return (
    <main>
      <header className="page-header">
        <div>
          <h1>Incident dashboard</h1>
          <p>All incidents reported across HealthCore clinics.</p>
        </div>
        <Link className="button-link" href="/incidents/new">Report an incident</Link>
      </header>

      <IncidentSummaryPanel refreshKey={summaryKey} compact />

      <section className="panel" aria-labelledby="list-title">
        <h2 id="list-title">Incidents</h2>
        <form className="filters" onSubmit={(event) => event.preventDefault()} aria-label="Filter incidents">
          <FilterSelect label="Status" name="status" options={STATUSES} value={filters.status} onChange={changeFilter} />
          <FilterSelect label="Category" name="category" options={CATEGORIES} value={filters.category} onChange={changeFilter} />
          <FilterSelect label="Origin" name="origin" options={ORIGINS} value={filters.origin} onChange={changeFilter} />
          <FilterSelect label="Clinic" name="branch" options={BRANCHES} value={filters.branch} onChange={changeFilter} />
          {filtered && (
            <button type="button" className="secondary" onClick={() => { setState({ kind: "loading" }); setFilters(NO_FILTERS); }}>
              Clear filters
            </button>
          )}
        </form>

        {notice && (
          <div className={`notice ${notice.kind}-notice`} role={notice.kind === "error" ? "alert" : "status"}>
            {notice.text}
          </div>
        )}

        {state.kind === "loading" && (
          <p role="status" className="loading"><span className="spinner dark" aria-hidden="true" /> Loading incidents…</p>
        )}
        {state.kind === "error" && (
          <div className="notice error-notice" role="alert">
            <p>We couldn&apos;t load the incidents. {state.message}</p>
            <button type="button" onClick={retry}>Retry</button>
          </div>
        )}
        {state.kind === "ready" && state.incidents.length === 0 && (
          <div className="empty-state" role="status">
            {filtered ? (
              <>
                <p><strong>No incidents match these filters.</strong></p>
                <p>Try another combination or clear the filters.</p>
              </>
            ) : (
              <>
                <p><strong>No incidents have been reported yet.</strong></p>
                <p><Link href="/incidents/new">Report the first incident</Link>.</p>
              </>
            )}
          </div>
        )}
        {state.kind === "ready" && state.incidents.length > 0 && (
          <>
            <p className="result-count">{state.incidents.length} incident{state.incidents.length === 1 ? "" : "s"}</p>
            <div className="table-scroll" role="region" aria-label="Incident list" tabIndex={0}>
              <table className="incident-table">
                <thead>
                  <tr><th>#</th><th>Incident</th><th>Category</th><th>Origin</th><th>Clinic</th><th>Created</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {state.incidents.map((incident) => (
                    <tr key={incident.id}>
                      <td className="nowrap">{incident.id}</td>
                      <th scope="row">
                        {incident.title}
                        {incident.description !== incident.title && <small>{incident.description}</small>}
                      </th>
                      <td>{categoryLabel(incident.category)}</td>
                      <td className="nowrap">{originLabel(incident.origin).split(" (")[0]}</td>
                      <td>{branchLabel(incident.branch)}</td>
                      <td className="nowrap"><time dateTime={incident.created_at}>{dateFormat.format(new Date(incident.created_at))}</time></td>
                      <td>
                        <StatusCell incident={incident} pending={pending.has(incident.id)} onChange={changeStatus} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
