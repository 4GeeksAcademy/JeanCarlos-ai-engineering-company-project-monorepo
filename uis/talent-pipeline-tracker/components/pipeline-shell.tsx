import Link from "next/link";
import type { ReactNode } from "react";

/*
 * Layout shared by the prerendered Suspense fallback (app/page.tsx) and the
 * client list (CandidateList). Performance audit (AUDIT.md):
 * - The fallback used to be a lone spinner, so the <h1> (LCP element) only
 *   existed after the JavaScript ran. Now the topbar and heading are rendered by
 *   the server outside the Suspense boundary (app/page.tsx) and never replaced.
 * - Swapping the fallback for the list, and the loading state for the table,
 *   keeps every element in place (no layout shift).
 */

export function PipelineTopbar() {
  return (
    <header className="topbar">
      <Link className="brand" href="/" aria-label="People and Talent, inicio">
        <span className="brand-mark" aria-hidden="true">HC</span>
        <span><strong>HealthCore</strong><small>People & Talent</small></span>
      </Link>
      <div className="topbar-context"><span className="online-dot" /> Talent pipeline</div>
      <div className="team-avatar" aria-label="Equipo de People & Talent">PT</div>
    </header>
  );
}

export function PipelineHeading({ action }: { action: ReactNode }) {
  return (
    <section className="page-heading">
      <div>
        <p className="eyebrow">HEALTHCORE <span>/</span> PEOPLE & TALENT</p>
        <h1>Candidaturas<span className="heading-mark">.</span></h1>
        <p className="page-subtitle">Una vista clara de cada persona y su avance en el proceso.</p>
      </div>
      {action}
    </section>
  );
}

export type SummaryValues = { total?: number; inProgress?: number; selected?: number; received?: number };

export function PipelineSummary({ values }: { values: SummaryValues | null }) {
  const show = (value?: number) => value ?? "—";
  return (
    <section className="summary-grid" aria-label="Resumen del pipeline">
      <div className="summary-item summary-total"><span>Candidaturas</span><strong>{show(values?.total)}</strong><small>en el pipeline</small></div>
      <div className="summary-item summary-active"><span>En proceso</span><strong>{show(values?.inProgress)}</strong><small>evaluación activa</small></div>
      <div className="summary-item summary-selected"><span>Seleccionadas</span><strong>{show(values?.selected)}</strong><small>decisión positiva</small></div>
      <div className="summary-item summary-received"><span>Recibidas</span><strong>{show(values?.received)}</strong><small>pendientes de revisión</small></div>
    </section>
  );
}

export function PanelHeading({ subtitle, filters }: { subtitle: string; filters: ReactNode }) {
  return (
    <div className="panel-heading">
      <div><h2 id="candidate-list-heading">Todas las candidaturas</h2><p>{subtitle}</p></div>
      {filters}
    </div>
  );
}

/** Loading state with reserved height: the table arriving later does not push the footer. */
export function ListLoading() {
  return <div className="feedback-state list-loading" role="status"><span className="spinner" />Cargando candidaturas…</div>;
}

export function PipelineFooter() {
  return <footer className="page-footer"><span>People & Talent <i>·</i> Talent pipeline</span><span>Datos conectados al sistema de candidaturas</span></footer>;
}

/** Inert copy of the filters, same size as the real ones, for the static fallback. */
function FiltersPlaceholder() {
  return (
    <div className="filters" aria-hidden="true">
      <label className="search-control"><svg viewBox="0 0 24 24"><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></svg><input type="search" placeholder="Nombre o email" disabled tabIndex={-1} /></label>
      <label><select disabled tabIndex={-1}><option>Todos los estados</option></select></label>
      <label><select disabled tabIndex={-1}><option>Todas las etapas</option></select></label>
    </div>
  );
}

/** Fallback for the Suspense boundary (summary + list), same layout as the list while it loads. */
export function PipelineListFallback() {
  return (
    <>
      <PipelineSummary values={null} />
      <section className="candidate-panel" aria-labelledby="candidate-list-heading">
        <PanelHeading subtitle="Consulta y gestiona el pipeline" filters={<FiltersPlaceholder />} />
        <ListLoading />
      </section>
    </>
  );
}
