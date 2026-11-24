-- Dead Letter Queue of the Celery task queue (ticket DEV-55).
-- One row per task that exhausted its retries (max_retries = 3 → 4 attempts). The worker
-- writes it before giving up; the API reads it to explain a `failure` in GET /tasks/{id}.
-- Only identifiers and the error are stored: task messages never carry data payloads.
create table if not exists reporting.task_dead_letters (
  id bigint generated always as identity primary key,
  task_id text not null unique,
  task_name text not null,
  attempt integer not null check (attempt >= 1),
  max_retries integer not null,
  error_type text not null,
  error_message text not null,
  traceback text,
  task_kwargs jsonb not null default '{}'::jsonb,  -- identifiers only (run_id, months)
  failed_at timestamptz not null default now()
);
create index if not exists task_dead_letters_failed_at_idx on reporting.task_dead_letters (failed_at desc);

alter table reporting.task_dead_letters enable row level security;

-- The Celery worker runs the pipeline as reporting_pipeline; the API reads with reporting_reader.
grant select, insert on reporting.task_dead_letters to reporting_pipeline;
grant select on reporting.task_dead_letters to reporting_reader;
drop policy if exists reporting_pipeline_all on reporting.task_dead_letters;
create policy reporting_pipeline_all on reporting.task_dead_letters
  for all to reporting_pipeline using (true) with check (true);
drop policy if exists reporting_reader_select on reporting.task_dead_letters;
create policy reporting_reader_select on reporting.task_dead_letters
  for select to reporting_reader using (true);
