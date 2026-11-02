"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState, useTransition } from "react";
import { CandidateForm } from "@/components/candidate-form";
import { getCandidates } from "@/lib/api";
import type { Candidate } from "@/types/tracker";

const statusLabels = {
  received: "Recibida",
  in_progress: "En proceso",
  selected: "Seleccionada",
  discarded: "Descartada",
};

const stageLabels = {
  pending: "Pendiente",
  review: "Revisión",
  personal_interview: "Entrevista personal",
  technical_interview: "Entrevista técnica",
  offer_presented: "Oferta presentada",
};

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("es", { dateStyle: "medium" }).format(new Date(value));
}

function initials(name: string): string {
  return name.split(/\s+/).slice(0, 2).map((part) => part[0] ?? "").join("").toLocaleUpperCase("es");
}

export function CandidateList() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryString = searchParams.toString();
  const searchFromUrl = new URLSearchParams(queryString).get("search") ?? "";
  const [records, setRecords] = useState<Candidate[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState(searchParams.get("search") ?? "");
  const [loadState, setLoadState] = useState<"loading" | "success" | "error">("loading");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isPending, startTransition] = useTransition();

  const loadRecords = useCallback(async () => {
    setLoadState("loading");
    setError("");
    try {
      const result = await getCandidates(new URLSearchParams(queryString));
      setRecords(result.data);
      setTotal(result.total);
      setLoadState("success");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "No se pudo cargar el listado.");
      setLoadState("error");
    }
  }, [queryString]);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchFromUrl), 0);
    return () => window.clearTimeout(timer);
  }, [searchFromUrl]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadRecords(), 0);
    return () => window.clearTimeout(timer);
  }, [loadRecords]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams(queryString);
      if (search.trim()) params.set("search", search.trim());
      else params.delete("search");
      const query = params.toString();
      startTransition(() => router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false }));
    }, 250);

    return () => window.clearTimeout(timer);
  }, [search, pathname, queryString, router]);

  function updateFilter(key: "status" | "stage", value: string) {
    const params = new URLSearchParams(queryString);
    if (value) params.set(key, value);
    else params.delete(key);
    const query = params.toString();
    startTransition(() => router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false }));
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="People and Talent, inicio">
          <span className="brand-mark" aria-hidden="true">HC</span>
          <span><strong>HealthCore</strong><small>People & Talent</small></span>
        </Link>
        <div className="topbar-context"><span className="online-dot" /> Talent pipeline</div>
        <div className="team-avatar" aria-label="Equipo de People & Talent">PT</div>
      </header>

      <div className="page-content">
        <section className="page-heading">
          <div>
            <p className="eyebrow">HEALTHCORE <span>/</span> PEOPLE & TALENT</p>
            <h1>Candidaturas<span className="heading-mark">.</span></h1>
            <p className="page-subtitle">Una vista clara de cada persona y su avance en el proceso.</p>
          </div>
          <CandidateForm onSaved={async () => { setNotice("Candidatura registrada correctamente."); await loadRecords(); }} />
        </section>

        <section className="summary-grid" aria-label="Resumen del pipeline">
          <div className="summary-item summary-total"><span>Candidaturas</span><strong>{loadState === "success" ? total : "—"}</strong><small>en el pipeline</small></div>
          <div className="summary-item summary-active"><span>En proceso</span><strong>{loadState === "success" ? records.filter((record) => record.status === "in_progress").length : "—"}</strong><small>evaluación activa</small></div>
          <div className="summary-item summary-selected"><span>Seleccionadas</span><strong>{loadState === "success" ? records.filter((record) => record.status === "selected").length : "—"}</strong><small>decisión positiva</small></div>
          <div className="summary-item summary-received"><span>Recibidas</span><strong>{loadState === "success" ? records.filter((record) => record.status === "received").length : "—"}</strong><small>pendientes de revisión</small></div>
        </section>

        <section className="candidate-panel" aria-labelledby="candidate-list-heading">
          {notice && <p className="operation-feedback operation-success" role="status">{notice}</p>}
          <div className="panel-heading">
            <div><h2 id="candidate-list-heading">Todas las candidaturas</h2><p>{loadState === "success" ? `${records.length} de ${total} candidaturas` : "Consulta y gestiona el pipeline"}</p></div>
            <div className="filters">
              <label className="search-control"><span className="sr-only">Buscar por nombre o email</span><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></svg><input value={search} onChange={(event) => setSearch(event.target.value)} type="search" placeholder="Nombre o email" /></label>
              <label><span className="sr-only">Filtrar por estado</span><select value={searchParams.get("status") ?? ""} onChange={(event) => updateFilter("status", event.target.value)}><option value="">Todos los estados</option><option value="received">Recibida</option><option value="in_progress">En proceso</option><option value="selected">Seleccionada</option><option value="discarded">Descartada</option></select></label>
              <label><span className="sr-only">Filtrar por etapa</span><select value={searchParams.get("stage") ?? ""} onChange={(event) => updateFilter("stage", event.target.value)}><option value="">Todas las etapas</option><option value="pending">Pendiente</option><option value="review">Revisión</option><option value="personal_interview">Entrevista personal</option><option value="technical_interview">Entrevista técnica</option><option value="offer_presented">Oferta presentada</option></select></label>
            </div>
          </div>

          {loadState === "loading" && <div className="feedback-state" role="status"><span className="spinner" />Cargando candidaturas…</div>}
          {loadState === "error" && <div className="feedback-state error-state" role="alert"><div><strong>No se pudo cargar el pipeline</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => void loadRecords()}>Reintentar</button></div>}
          {loadState === "success" && records.length === 0 && <div className="feedback-state empty-state"><strong>No hay resultados</strong><p>Prueba con otros filtros o registra una candidatura.</p></div>}
          {loadState === "success" && records.length > 0 && <div className="table-wrap"><table className="candidate-table"><thead><tr><th scope="col">Candidata/o</th><th scope="col">Puesto</th><th scope="col">Estado</th><th scope="col">Etapa</th><th scope="col">Fecha de ingreso</th><th scope="col"><span className="sr-only">Abrir detalle</span></th></tr></thead><tbody>
            {records.map((record) => <tr key={record.id}>
              <td data-label="Candidata/o"><Link className="candidate-name" href={`/candidatures/${record.id}`}><span className="candidate-avatar" aria-hidden="true">{initials(record.full_name)}</span><span><strong>{record.full_name}</strong><small>{record.email}</small></span></Link></td>
              <td data-label="Puesto"><span className="position-title">{record.position}</span><small className="secondary-text">{record.experience_years} años de experiencia</small></td>
              <td data-label="Estado"><span className={`status-pill status-${record.status}`}>{statusLabels[record.status]}</span></td>
              <td data-label="Etapa"><span className="stage-text">{stageLabels[record.stage]}</span></td>
              <td data-label="Fecha de ingreso"><span className="date-text">{formatDate(record.applied_at)}</span></td>
              <td data-label="Detalle"><Link className="open-detail" href={`/candidatures/${record.id}`} aria-label={`Ver candidatura de ${record.full_name}`}>↗</Link></td>
            </tr>)}
          </tbody></table></div>}
        </section>
        <footer className="page-footer"><span>People & Talent <i>·</i> Talent pipeline</span><span>Datos conectados al sistema de candidaturas</span></footer>
      </div>
      {isPending && <span className="sr-only" role="status">Actualizando filtros…</span>}
    </main>
  );
}