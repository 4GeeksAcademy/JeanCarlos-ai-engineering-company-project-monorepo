"use client";

import { useEffect, useState, type FormEvent } from "react";
import { departments } from "@/types/inventory";

// Business dashboard for the board pack: reads GET /reporting/monthly-clinic-supply-performance
// (reporting.monthly_clinic_supply_performance) and GET /reporting/pipeline-runs/latest.
// Labels use the KPI names of the HealthCore pipeline BRIEFING §2, in plain language.

type Country = "US" | "UK";
type Clinic = {
  clinic_id: string;
  country: Country;
  total_supply_cost: number;
  supply_consumption_count: number;
  critical_stockout_count: number;
  expiry_risk_count: number;
  currency: "USD" | "GBP";
};
type DepartmentConsumption = { clinic_id: string; department: string; supply_consumption_count: number };
type MonthlyReport = {
  month_start: string;
  clinics: Clinic[];
  consumption_by_department: DepartmentConsumption[];
  computed_at: string;
  revised: boolean;
  coverage_checked: boolean;
  coverage_warnings: string[];
};
type PipelineRun = {
  status: "running" | "completed" | "failed" | "skipped_locked";
  started_at: string;
  finished_at: string | null;
  months: string[];
  events_skipped: Record<string, number>;
};
type KpiKey = "total_supply_cost" | "supply_consumption_count" | "critical_stockout_count" | "expiry_risk_count";

const KPIS: { key: KpiKey; name: string; measures: string; why: string; unit: (value: number, currency: Clinic["currency"]) => string }[] = [
  {
    key: "total_supply_cost",
    name: "Costo de insumos por clínica",
    measures: "Cuánto gastó cada clínica comprando insumos médicos durante el mes.",
    why: "Muestra dónde se concentra el gasto y orienta las decisiones de compra entre clínicas.",
    unit: (value, currency) => money(value, currency),
  },
  {
    key: "supply_consumption_count",
    name: "Volumen de consumo de insumos",
    measures: "Cuántos consumos de insumos registró cada clínica durante el mes, y en qué departamento.",
    why: "Señal de actividad: distingue una clínica con mucho trabajo de una que no está registrando datos.",
    unit: (value) => `${value} ${value === 1 ? "consumo" : "consumos"}`,
  },
  {
    key: "critical_stockout_count",
    name: "Frecuencia de quiebre crítico",
    measures: "Cuántas veces durante el mes cada clínica cayó por debajo del mínimo de un insumo.",
    why: "Riesgo para la seguridad del paciente y de cumplimiento: requiere acción rápida.",
    unit: (value) => `${value} ${value === 1 ? "vez" : "veces"}`,
  },
  {
    key: "expiry_risk_count",
    name: "Conteo de riesgo de vencimiento",
    measures: "Cuántos lotes de insumos de cada clínica se marcaron por estar cerca de su fecha de vencimiento.",
    why: "Riesgo de merma y de cumplimiento: indica qué insumos hay que usar o dar de baja antes de que venzan.",
    unit: (value) => `${value} ${value === 1 ? "lote" : "lotes"}`,
  },
];

const COUNTRIES: { code: Country; label: string; currency: Clinic["currency"] }[] = [
  { code: "US", label: "Estados Unidos", currency: "USD" },
  { code: "UK", label: "Reino Unido", currency: "GBP" },
];

const REGIONS: Record<string, string> = {
  "us-tx": "Texas", "us-fl": "Florida", "us-ga": "Georgia", "uk-lon": "Londres", "uk-man": "Mánchester",
};

const STALE_AFTER_HOURS = 26; // daily run at 05:00 UTC + margin

function money(value: number, currency: Clinic["currency"]) {
  return new Intl.NumberFormat(currency === "USD" ? "en-US" : "en-GB", { style: "currency", currency }).format(value);
}

/** us-tx-001 → "Texas 1"; unknown ids are shown as they are. */
function clinicName(id: string) {
  const match = /^([a-z]{2}-[a-z]+)-(\d+)$/.exec(id);
  return match && REGIONS[match[1]] ? `${REGIONS[match[1]]} ${Number(match[2])}` : id;
}

function monthName(monthStart: string) {
  const [year, month] = monthStart.split("-").map(Number);
  return new Intl.DateTimeFormat("es", { month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, 1)));
}

function periodLabel(monthStart: string) {
  const [year, month] = monthStart.split("-").map(Number);
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const monthOnly = new Intl.DateTimeFormat("es", { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, 1)));
  return `del 1 al ${lastDay} de ${monthOnly} de ${year}`;
}

function dateTime(iso: string) {
  return new Intl.DateTimeFormat("es", { dateStyle: "long", timeStyle: "short", timeZone: "UTC" }).format(new Date(iso)) + " (UTC)";
}

function isCurrentMonth(monthStart: string, at: number) {
  const now = new Date(at);
  return monthStart === `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}-01`;
}

function isReport(value: unknown): value is MonthlyReport {
  if (typeof value !== "object" || value === null) return false;
  const report = value as Partial<MonthlyReport>;
  return typeof report.month_start === "string" && typeof report.computed_at === "string" &&
    Array.isArray(report.consumption_by_department) &&
    Array.isArray(report.clinics) && Array.isArray(report.coverage_warnings) &&
    report.clinics.every((clinic) => typeof clinic?.clinic_id === "string" &&
      (clinic.country === "US" || clinic.country === "UK") &&
      KPIS.every(({ key }) => typeof clinic[key] === "number"));
}

function isRun(value: unknown): value is PipelineRun {
  return typeof value === "object" && value !== null && typeof (value as PipelineRun).status === "string" &&
    typeof (value as PipelineRun).started_at === "string";
}

async function getJson(url: string, signal: AbortSignal): Promise<{ status: number; body: unknown }> {
  const response = await fetch(url, { cache: "no-store", signal });
  return { status: response.status, body: await response.json().catch(() => null) };
}

function Bar({ value, max, label }: { value: number; max: number; label: string }) {
  const width = max > 0 ? Math.max(value > 0 ? 2 : 0, (value / max) * 100) : 0;
  return (
    <div className="metric-bar">
      <span className="metric-bar-fill" style={{ width: `${width}%` }} aria-hidden="true" />
      <span className="metric-bar-label">{label}</span>
    </div>
  );
}

function departmentName(value: string) {
  return departments.find((item) => item.value === value)?.label ?? (value === "unknown" ? "Sin departamento" : value);
}

/** KPI «Volumen de consumo de insumos» por departamento (BRIEFING §2). */
function DepartmentBreakdown({ rows }: { rows: DepartmentConsumption[] }) {
  if (rows.length === 0) return <p>No hubo consumos registrados por departamento en este mes.</p>;
  const sorted = [...rows].sort((a, b) =>
    clinicName(a.clinic_id).localeCompare(clinicName(b.clinic_id), "es") || b.supply_consumption_count - a.supply_consumption_count);
  const max = Math.max(...rows.map((row) => row.supply_consumption_count));
  return (
    <>
      <h3>Por departamento</h3>
      <div className="table-scroll" role="region" aria-label="Volumen de consumo de insumos por departamento" tabIndex={0}>
        <table>
          <thead><tr><th>Clínica</th><th>Departamento</th><th>Consumos</th></tr></thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={`${row.clinic_id}-${row.department}`}>
                <td>{clinicName(row.clinic_id)}</td>
                <td>{departmentName(row.department)}</td>
                <td><Bar value={row.supply_consumption_count} max={max} label={String(row.supply_consumption_count)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function KpiSection({ kpi, clinics, warnings, children }: {
  kpi: (typeof KPIS)[number]; clinics: Clinic[]; warnings: Set<string>; children?: React.ReactNode;
}) {
  return (
    <section className="panel" aria-labelledby={`kpi-${kpi.key}`}>
      <h2 id={`kpi-${kpi.key}`}>{kpi.name}</h2>
      <p>{kpi.measures} <em>{kpi.why}</em></p>
      <div className="kpi-countries">
        {COUNTRIES.map((country) => {
          const rows = clinics.filter((clinic) => clinic.country === country.code);
          // Each country on its own scale and total: amounts in USD and GBP are never added together.
          const max = Math.max(0, ...rows.map((clinic) => clinic[kpi.key]));
          const total = rows.reduce((sum, clinic) => sum + clinic[kpi.key], 0);
          return (
            <div key={country.code}>
              <h3>{country.label} <span className="kpi-total">Total: {kpi.unit(total, country.currency)}</span></h3>
              <div className="table-scroll" role="region" aria-label={`${kpi.name} — ${country.label}`} tabIndex={0}>
                <table>
                  <thead><tr><th>Clínica</th><th>{kpi.name}</th></tr></thead>
                  <tbody>
                    {rows.map((clinic) => (
                      <tr key={clinic.clinic_id}>
                        <td>
                          {clinicName(clinic.clinic_id)}
                          {warnings.has(clinic.clinic_id) && <span className="kpi-flag" title="Datos posiblemente incompletos"> *</span>}
                        </td>
                        <td><Bar value={clinic[kpi.key]} max={max} label={kpi.unit(clinic[kpi.key], clinic.currency)} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
      </div>
      {children}
    </section>
  );
}

export default function MonthlyReportPage() {
  const [month, setMonth] = useState("");
  const [query, setQuery] = useState("");
  const [report, setReport] = useState<MonthlyReport | null>(null);
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [error, setError] = useState("");
  const [checkedAt, setCheckedAt] = useState(0); // when the data was loaded (staleness reference)

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [kpis, latest] = await Promise.all([
          getJson(`/api/reporting/monthly-clinic-supply-performance${query}`, controller.signal),
          getJson("/api/reporting/pipeline-runs/latest", controller.signal),
        ]);
        setRun(latest.status === 200 && isRun(latest.body) ? latest.body : null);
        setCheckedAt(Date.now());
        if (kpis.status === 404) {
          setReport(null);
          setState("missing");
          return;
        }
        if (kpis.status !== 200 || !isReport(kpis.body)) throw new Error(`No se pudo cargar el reporte (${kpis.status}).`);
        setReport(kpis.body);
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

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = month ? `?month_start=${month}-01` : "";
    if (next === query) return;
    setState("loading");
    setQuery(next);
  }

  const warnings = new Set(report?.coverage_warnings ?? []);
  const lastSuccess = run && run.status === "completed" && run.finished_at ? new Date(run.finished_at) : null;
  const stale = run && (run.status === "failed" ||
    (lastSuccess !== null && checkedAt - lastSuccess.getTime() > STALE_AFTER_HOURS * 3_600_000));
  const withoutCost = report && run?.months.includes(report.month_start) ? run.events_skipped.inbound_without_cost ?? 0 : 0;

  return (
    <main>
      <h1>Reporte Mensual de Desempeño de Insumos por Clínica</h1>
      <p>Costo de insumos, consumo, quiebres de stock y riesgo de vencimiento de las 12 clínicas de la red, en Estados Unidos y Reino Unido.</p>

      <section className="panel" aria-label="Periodo">
        <form className="report-period" onSubmit={submit}>
          <label>Mes<input type="month" value={month} onChange={(event) => setMonth(event.target.value)} /></label>
          <button>Ver mes</button>
          <button type="button" onClick={() => { setMonth(""); if (query) { setState("loading"); setQuery(""); } }}>Último mes disponible</button>
        </form>
        {report && (
          <div role="status">
            <p className="report-month"><strong>Periodo: {monthName(report.month_start)}</strong> ({periodLabel(report.month_start)}).</p>
            {isCurrentMonth(report.month_start, checkedAt) && <p><strong>Mes en curso:</strong> las cifras son parciales y se actualizan cada día.</p>}
            <p>Cifras actualizadas el {dateTime(report.computed_at)}. Cada país se muestra en su moneda (USD y GBP); no se suman entre sí.</p>
          </div>
        )}
        {stale && (
          <p className="error" role="alert">
            Atención: la actualización diaria del reporte {run?.status === "failed" ? "falló en su último intento" : "lleva más de un día sin completarse"}.
            Las cifras pueden no estar al día; avise al equipo de datos.
          </p>
        )}
        {state === "loading" && <p role="status">Cargando reporte...</p>}
        {state === "missing" && <p role="status">Ese mes todavía no tiene un reporte calculado.</p>}
        {state === "error" && <p className="error" role="alert">{error}</p>}
      </section>

      {state === "ready" && report && (
        <>
          {(warnings.size > 0 || withoutCost > 0 || report.revised) && (
            <section className="panel" aria-label="Calidad de los datos">
              <h2>Notas sobre los datos</h2>
              <ul>
                {warnings.size > 0 && (
                  <li>
                    Datos posiblemente incompletos (*) en: {[...warnings].map(clinicName).join(", ")}. Los movimientos
                    registrados en el inventario no coinciden con los recibidos por el sistema de reporte.
                  </li>
                )}
                {withoutCost > 0 && (
                  <li>{withoutCost} {withoutCost === 1 ? "entrada se registró" : "entradas se registraron"} sin costo unitario y no {withoutCost === 1 ? "suma" : "suman"} al costo de insumos.</li>
                )}
                {report.revised && <li>Las cifras de este mes se corrigieron después de su primera publicación por datos que llegaron tarde.</li>}
              </ul>
            </section>
          )}
          {KPIS.map((kpi) => (
            <KpiSection key={kpi.key} kpi={kpi} clinics={report.clinics} warnings={warnings}>
              {kpi.key === "supply_consumption_count" && <DepartmentBreakdown rows={report.consumption_by_department} />}
            </KpiSection>
          ))}
        </>
      )}
    </main>
  );
}
