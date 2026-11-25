-- KPI «Volumen de consumo de insumos» is defined "por departamento" (pipeline BRIEFING §2).
-- The BRIEFING destination table keeps its clinic × month grain; the per-department
-- breakdown is stored with the month checkpoint of the run that loaded it (additive).
alter table reporting.pipeline_run_months
  add column if not exists consumption_by_department jsonb not null default '[]'::jsonb;
