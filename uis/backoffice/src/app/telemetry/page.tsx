"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

type Row = Record<string, string | number | null>;
type Report = { period: { from: string; to: string }; metrics: Record<string, Row[]> };

const percent = (value: unknown) => (typeof value === "number" ? `${(value * 100).toFixed(1)} %` : "—");
const ms = (value: unknown) => (typeof value === "number" ? `${value.toFixed(1)} ms` : "—");

function isReport(value: unknown): value is Report {
  if (typeof value !== "object" || value === null) return false;
  const report = value as Partial<Report>;
  return typeof report.period?.from === "string" && typeof report.period?.to === "string" &&
    typeof report.metrics === "object" && report.metrics !== null &&
    Object.values(report.metrics).every(Array.isArray);
}

/** Horizontal bar proportional to value / max, with the formatted value as text. */
function Bar({ value, max, label }: { value: number; max: number; label: string }) {
  const width = max > 0 ? Math.max(2, (value / max) * 100) : 0;
  return (
    <div className="metric-bar">
      <span className="metric-bar-fill" style={{ width: `${width}%` }} aria-hidden="true" />
      <span className="metric-bar-label">{label}</span>
    </div>
  );
}

function MetricTable({ title, question, rows, columns }: {
  title: string;
  question: string;
  rows: Row[];
  columns: { key: string; label: string; render?: (row: Row, rows: Row[]) => ReactNode }[];
}) {
  return (
    <section className="panel" aria-label={title}>
      <h2>{title}</h2>
      <p>{question}</p>
      {rows.length === 0 ? <p>Sin datos en este periodo.</p> : (
        <div className="table-scroll" role="region" aria-label={`Tabla: ${title}`} tabIndex={0}>
          <table>
            <thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index}>
                  {columns.map((column) => (
                    <td key={column.key}>{column.render ? column.render(row, rows) : String(row[column.key] ?? "—")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const maxOf = (rows: Row[], key: string) => Math.max(0, ...rows.map((row) => Number(row[key]) || 0));

export default function TelemetryReportPage() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [query, setQuery] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`/api/telemetry/report${query}`, { cache: "no-store", signal: controller.signal });
        const body: unknown = await response.json().catch(() => null);
        if (!response.ok || !isReport(body)) {
          const detail = typeof body === "object" && body !== null && "detail" in body ? String(body.detail) : "";
          throw new Error(detail || `No se pudo cargar el reporte (${response.status}).`);
        }
        setReport(body);
        setState("ready");
      } catch (loadError) {
        if (controller.signal.aborted) return;
        setError(loadError instanceof Error ? loadError.message : "No se pudo cargar el reporte.");
        setState("error");
      }
    }
    void load();
    return () => controller.abort();
  }, [query]);

  function changeQuery(next: string) {
    if (next === query) return;
    setState("loading");
    setQuery(next);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const params = new URLSearchParams();
    if (from) params.set("start_date", `${from}T00:00:00Z`);
    if (to) params.set("end_date", `${to}T00:00:00Z`);
    changeQuery(params.size ? `?${params}` : "");
  }

  const metrics = report?.metrics ?? {};
  return (
    <main>
      <h1>Reporte técnico de telemetría</h1>
      <p>Vista de ingeniería sobre la salud del sistema: volumen, errores y latencia. No incluye métricas de negocio.</p>
      <section className="panel" aria-label="Periodo">
        <form className="report-period" onSubmit={submit}>
          <label>Desde (UTC)<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
          <label>Hasta, exclusivo (UTC)<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label>
          <button>Aplicar</button>
          <button type="button" onClick={() => { setFrom(""); setTo(""); changeQuery(""); }}>Últimos 7 días</button>
        </form>
        {report && <p role="status"><strong>Periodo:</strong> {report.period.from} → {report.period.to}</p>}
        {state === "loading" && <p role="status">Cargando reporte...</p>}
        {state === "error" && <p className="error" role="alert">{error}</p>}
      </section>
      {state === "ready" && report && (
        <>
          <MetricTable
            title="Eventos por día" question="¿Cuánta telemetría produce el sistema cada día?"
            rows={metrics.events_per_day ?? []}
            columns={[
              { key: "date", label: "Día" },
              { key: "events", label: "Eventos", render: (row, rows) => <Bar value={Number(row.events)} max={maxOf(rows, "events")} label={String(row.events)} /> },
              { key: "distinct_event_types", label: "Tipos distintos" },
            ]}
          />
          <MetricTable
            title="Tasa de error por ruta" question="¿Qué porcentaje de peticiones a la API falla (4xx cliente, 5xx servidor)?"
            rows={metrics.error_rate_by_type ?? []}
            columns={[
              { key: "date", label: "Día" },
              { key: "route", label: "Ruta", render: (row) => `${row.method} ${row.route_template}` },
              { key: "requests", label: "Peticiones" },
              { key: "error_rate", label: "Error total", render: (row) => <Bar value={Number(row.error_rate)} max={1} label={percent(row.error_rate)} /> },
              { key: "server_error_rate", label: "Error 5xx", render: (row) => percent(row.server_error_rate) },
            ]}
          />
          <MetricTable
            title="Latencia por ruta" question="¿Qué tan rápido responde cada ruta (p50/p95 ponderados por peticiones)?"
            rows={metrics.api_latency_by_route ?? []}
            columns={[
              { key: "date", label: "Día" },
              { key: "route", label: "Ruta", render: (row) => `${row.method} ${row.route_template}` },
              { key: "requests", label: "Peticiones" },
              { key: "p50_ms", label: "p50", render: (row) => ms(row.p50_ms) },
              { key: "p95_ms", label: "p95", render: (row, rows) => <Bar value={Number(row.p95_ms)} max={maxOf(rows, "p95_ms")} label={ms(row.p95_ms)} /> },
              { key: "p95_max_ms", label: "p95 máx.", render: (row) => ms(row.p95_max_ms) },
            ]}
          />
          <MetricTable
            title="Errores de frontend" question="¿Qué errores no controlados ocurren en la interfaz cada día?"
            rows={metrics.frontend_errors_per_day ?? []}
            columns={[
              { key: "date", label: "Día" },
              { key: "error_code", label: "Código" },
              { key: "errors", label: "Errores", render: (row, rows) => <Bar value={Number(row.errors)} max={maxOf(rows, "errors")} label={String(row.errors)} /> },
              { key: "affected_routes", label: "Rutas afectadas" },
              { key: "releases", label: "Releases" },
            ]}
          />
          <MetricTable
            title="Tasa de fallos de login" question="¿Qué porcentaje de intentos de login falla cada día?"
            rows={metrics.auth_failure_rate ?? []}
            columns={[
              { key: "date", label: "Día" },
              { key: "attempts", label: "Intentos" },
              { key: "failures", label: "Fallos" },
              { key: "failure_rate", label: "Tasa", render: (row) => <Bar value={Number(row.failure_rate)} max={1} label={percent(row.failure_rate)} /> },
            ]}
          />
          <MetricTable
            title="Distribución por tipo de evento" question="¿Qué tipos de evento dominan cada día?"
            rows={metrics.event_type_distribution ?? []}
            columns={[
              { key: "date", label: "Día" },
              { key: "event_type", label: "Tipo" },
              { key: "events", label: "Eventos" },
              { key: "share_of_day", label: "Peso en el día", render: (row) => <Bar value={Number(row.share_of_day)} max={1} label={percent(row.share_of_day)} /> },
            ]}
          />
        </>
      )}
    </main>
  );
}
