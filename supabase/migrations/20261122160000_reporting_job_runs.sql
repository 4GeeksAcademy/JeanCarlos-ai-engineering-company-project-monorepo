-- Nightly orchestration log (ticket DEV-53): one row per execution of a scheduled job.
--
-- job_runs is NOT pipeline_runs:
-- - job_runs (this table): the nightly script. It records the CSV backup, the launch of
--   the pipeline subprocess, the lock and per-day idempotency.
-- - pipeline_runs: written by the pipeline itself during its run (extract/transform/load
--   phases, rows processed).
--
-- Lifecycle: pending → processing → completed | failed.
-- `processing` IS the distributed lock: the partial unique index below allows at most one
-- processing row per job, so two instances cannot both move to processing. There is no
-- separate lock table, column or flag.
-- `skipped` is a terminal record outside the lifecycle. It never takes the lock and only
-- logs an execution that aborted silently: lock held, or the day already completed.

create table if not exists reporting.job_runs (
  id bigint generated always as identity primary key,
  job_name text not null,
  target_date date not null,
  status text not null default 'pending'
    check (status in ('pending', 'processing', 'completed', 'failed', 'skipped')),
  skip_reason text check (skip_reason in ('locked', 'already_completed')),
  started_at timestamptz,
  finished_at timestamptz,
  error_message text,
  details jsonb not null default '{}'::jsonb,   -- csv_path, rows_exported, pipeline exit code...
  host text,
  pid integer,
  created_at timestamptz not null default now(),
  check ((status = 'skipped') = (skip_reason is not null)),
  check (status not in ('completed', 'failed', 'skipped') or finished_at is not null)
);

-- Idempotency queries: has (job_name, target_date) already completed?
create index if not exists job_runs_job_target_idx on reporting.job_runs (job_name, target_date);

-- The lock: at most one `processing` row per job_name.
create unique index if not exists job_runs_one_processing_idx
  on reporting.job_runs (job_name) where status = 'processing';

-- At most one `completed` row per job and day, so even a race cannot complete a day twice.
create unique index if not exists job_runs_one_completed_per_day_idx
  on reporting.job_runs (job_name, target_date) where status = 'completed';

alter table reporting.job_runs enable row level security;

-- The nightly script connects as reporting_pipeline (it also launches the pipeline).
grant select, insert, update on reporting.job_runs to reporting_pipeline;
grant select on reporting.job_runs to reporting_reader;
drop policy if exists reporting_pipeline_all on reporting.job_runs;
create policy reporting_pipeline_all on reporting.job_runs
  for all to reporting_pipeline using (true) with check (true);
drop policy if exists reporting_reader_select on reporting.job_runs;
create policy reporting_reader_select on reporting.job_runs
  for select to reporting_reader using (true);
