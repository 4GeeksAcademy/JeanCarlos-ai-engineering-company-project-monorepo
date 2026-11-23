-- Business reporting pipeline (data/pipelines/PIPELINE_DESIGN.md §4).
-- Everything lives in the dedicated `reporting` schema; nothing here writes to
-- public.telemetry_events, which the pipeline only reads.

create schema if not exists reporting;

-- Destination table, exactly as in the HealthCore pipeline BRIEFING §5.
-- unique (clinic_id, month_start) is the key the idempotent upsert relies on.
create table if not exists reporting.monthly_clinic_supply_performance (
  id uuid primary key default gen_random_uuid(),
  clinic_id text not null,
  country text not null,
  month_start date not null,
  total_supply_cost numeric not null default 0,
  supply_consumption_count integer not null default 0,
  critical_stockout_count integer not null default 0,
  expiry_risk_count integer not null default 0,
  currency text not null,
  computed_at timestamptz not null default now(),
  unique (clinic_id, month_start)
);

-- Execution log: one row per run (PIPELINE_DESIGN.md §6, "Log de ejecución").
create table if not exists reporting.pipeline_runs (
  run_id uuid primary key,
  pipeline_name text not null,
  flow_run_id text,
  trigger text not null check (trigger in ('scheduled', 'manual', 'backfill')),
  triggered_by text,
  months date[] not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null check (status in ('running', 'completed', 'failed', 'skipped_locked')),
  source_events_read integer not null default 0,
  events_skipped jsonb not null default '{}'::jsonb,
  rows_upserted integer not null default 0,
  rows_changed integer not null default 0,
  warnings jsonb not null default '[]'::jsonb,
  error_stage text check (error_stage in ('lock', 'extract', 'transform', 'validate', 'load')),
  error_message text,
  code_version text
);
create index if not exists pipeline_runs_latest_idx
  on reporting.pipeline_runs (pipeline_name, started_at desc);

-- Per-month checkpoint, written in the same transaction as the month's upsert.
create table if not exists reporting.pipeline_run_months (
  run_id uuid not null references reporting.pipeline_runs (run_id),
  month_start date not null,
  status text not null check (status in ('completed', 'failed')),
  source_event_counts jsonb not null default '{}'::jsonb,
  inventory_counts jsonb,
  clinics_without_events text[] not null default '{}',
  coverage jsonb,
  rows_changed integer not null default 0,
  completed_at timestamptz not null default now(),
  primary key (run_id, month_start)
);
create index if not exists pipeline_run_months_month_idx
  on reporting.pipeline_run_months (month_start, completed_at desc);

-- Audit trail of published figures: previous values of every row a reload changed.
create table if not exists reporting.monthly_clinic_supply_performance_revisions (
  revision_id bigint generated always as identity primary key,
  run_id uuid not null references reporting.pipeline_runs (run_id),
  clinic_id text not null,
  month_start date not null,
  reason text not null check (reason in ('late_events', 'backfill', 'recompute')),
  previous jsonb not null,
  current jsonb not null,
  revised_at timestamptz not null default now()
);

create or replace function reporting.block_revision_changes() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'monthly_clinic_supply_performance_revisions is append-only';
end;
$$;

drop trigger if exists revisions_append_only on reporting.monthly_clinic_supply_performance_revisions;
create trigger revisions_append_only
  before update or delete on reporting.monthly_clinic_supply_performance_revisions
  for each row execute function reporting.block_revision_changes();

alter table reporting.monthly_clinic_supply_performance enable row level security;
alter table reporting.pipeline_runs enable row level security;
alter table reporting.pipeline_run_months enable row level security;
alter table reporting.monthly_clinic_supply_performance_revisions enable row level security;

-- Roles. Passwords are set outside version control:
--   alter role reporting_pipeline with login password '<scram verifier>';
--   alter role reporting_reader with login password '<scram verifier>';
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'reporting_pipeline') then
    create role reporting_pipeline nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'reporting_reader') then
    create role reporting_reader nologin;
  end if;
end
$$;

-- reporting_pipeline: reads only the 4 columns it needs from telemetry_events
-- (never session_id, user_id or request_id) and writes only in `reporting`.
grant usage on schema public to reporting_pipeline;
grant select (id, "timestamp", event_type, tags) on public.telemetry_events to reporting_pipeline;
drop policy if exists reporting_pipeline_select on public.telemetry_events;
create policy reporting_pipeline_select on public.telemetry_events
  for select to reporting_pipeline using (true);

grant usage on schema reporting to reporting_pipeline;
grant select, insert, update on reporting.monthly_clinic_supply_performance to reporting_pipeline;
grant select, insert, update on reporting.pipeline_runs to reporting_pipeline;
grant select, insert on reporting.pipeline_run_months to reporting_pipeline;
grant select, insert on reporting.monthly_clinic_supply_performance_revisions to reporting_pipeline;

-- reporting_reader: what services/reporting reads; no write grants anywhere.
grant usage on schema reporting to reporting_reader;
grant select on reporting.monthly_clinic_supply_performance,
  reporting.pipeline_runs, reporting.pipeline_run_months,
  reporting.monthly_clinic_supply_performance_revisions to reporting_reader;

do $$
declare
  t text;
begin
  foreach t in array array['monthly_clinic_supply_performance', 'pipeline_runs',
                           'pipeline_run_months', 'monthly_clinic_supply_performance_revisions']
  loop
    execute format('drop policy if exists reporting_pipeline_all on reporting.%I', t);
    execute format('create policy reporting_pipeline_all on reporting.%I for all to reporting_pipeline using (true) with check (true)', t);
    execute format('drop policy if exists reporting_reader_select on reporting.%I', t);
    execute format('create policy reporting_reader_select on reporting.%I for select to reporting_reader using (true)', t);
  end loop;
end
$$;
