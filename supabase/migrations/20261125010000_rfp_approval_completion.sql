-- RFP approval and closing (Hito 9 Parte 3): approvals per department, arbitration record, final
-- document, the trace of every node execution, and the LangGraph checkpointer tables (PostgresSaver)
-- that keep the paused approval threads. Mirrors data/pipelines/rfp_intake/models.py.
--
-- No PHI: approval comments are rejected by the API when they match the PHI rules; the trace stores
-- summaries (lengths + hashes for long text, PHI categories instead of matches), never documents.

-- ---------------------------------------------------------------- ticket
alter table public.rfp_ticket
  add column if not exists arbitration json,                          -- [{round, trigger, arbiter, decided_by, resolution, ...}]
  add column if not exists pending_arbitration json,                  -- decision asked to a named arbiter right now
  add column if not exists approval_round integer not null default 0; -- arbitration rounds used

alter table public.rfp_ticket drop constraint if exists rfp_ticket_stage_check;
alter table public.rfp_ticket add constraint rfp_ticket_stage_check check (stage is null or stage in (
  'received', 'converting', 'screening_phi', 'classifying', 'extracting', 'orchestrating', 'workers', 'synthesizing',
  'done', 'discarded', 'failed', 'generating', 'evaluating', 'generated', 'generation_failed',
  'awaiting_approval', 'arbitration', 'finalizing', 'completed', 'approval_rejected', 'discarded_by_compliance',
  'approval_failed'));

-- ---------------------------------------------------------------- department section
alter table public.rfp_department_section
  add column if not exists approval_packet json,        -- what the approver sees (evaluation summary, triggers, actions)
  add column if not exists approval_comment text,
  add column if not exists confirmed_capacity integer,  -- people the clinical staff can cover (Dr. Marcus Reid)
  add column if not exists revision_rounds integer not null default 0,
  add column if not exists decision_history json;

-- ---------------------------------------------------------------- final document (BRIEFING §2.3 FinalDocument)
create table if not exists public.rfp_final_document (
  ticket_id varchar(36) primary key references public.rfp_ticket (ticket_id) on delete cascade,
  rfp_id varchar not null,
  client_name varchar,
  client_country varchar,
  currency varchar,
  regulation varchar,
  agreement_type varchar,
  sections json not null,
  approvals json not null,
  arbitration json not null,
  markdown text not null,
  generated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- trace: one row per node execution
create table if not exists public.rfp_trace (
  id serial primary key,
  ticket_id varchar(36) not null references public.rfp_ticket (ticket_id) on delete cascade,
  thread_id varchar,
  node varchar not null,
  agent varchar not null,
  status varchar not null,  -- ok / interrupted / error
  input json,
  output json,
  started_at timestamptz not null,
  finished_at timestamptz not null
);
create index if not exists ix_rfp_trace_ticket_id on public.rfp_trace (ticket_id);

-- ---------------------------------------------------------------- LangGraph PostgresSaver (langgraph-checkpoint-postgres 3.x)
-- Same DDL as PostgresSaver.setup(); thread ids: rfp-{ticket_id} and rfp-{ticket_id}:{department}.
create table if not exists public.checkpoint_migrations (v integer primary key);
create table if not exists public.checkpoints (
  thread_id text not null,
  checkpoint_ns text not null default '',
  checkpoint_id text not null,
  parent_checkpoint_id text,
  type text,
  checkpoint jsonb not null,
  metadata jsonb not null default '{}',
  primary key (thread_id, checkpoint_ns, checkpoint_id)
);
create table if not exists public.checkpoint_blobs (
  thread_id text not null,
  checkpoint_ns text not null default '',
  channel text not null,
  version text not null,
  type text not null,
  blob bytea,
  primary key (thread_id, checkpoint_ns, channel, version)
);
create table if not exists public.checkpoint_writes (
  thread_id text not null,
  checkpoint_ns text not null default '',
  checkpoint_id text not null,
  task_id text not null,
  idx integer not null,
  channel text not null,
  type text,
  blob bytea not null,
  task_path text not null default '',
  primary key (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
create index if not exists checkpoints_thread_id_idx on public.checkpoints (thread_id);
create index if not exists checkpoint_blobs_thread_id_idx on public.checkpoint_blobs (thread_id);
create index if not exists checkpoint_writes_thread_id_idx on public.checkpoint_writes (thread_id);
insert into public.checkpoint_migrations (v) select generate_series(0, 9) on conflict do nothing;

-- ---------------------------------------------------------------- access: backend only
alter table public.rfp_final_document enable row level security;
alter table public.rfp_trace enable row level security;
alter table public.checkpoint_migrations enable row level security;
alter table public.checkpoints enable row level security;
alter table public.checkpoint_blobs enable row level security;
alter table public.checkpoint_writes enable row level security;

-- the dedicated role of scripts/supabase_rfp_role.sql, when it exists
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'rfp_pipeline') then
    grant select, insert, update, delete on public.rfp_final_document, public.rfp_trace, public.checkpoint_migrations,
      public.checkpoints, public.checkpoint_blobs, public.checkpoint_writes to rfp_pipeline;
    grant usage, select on sequence public.rfp_trace_id_seq to rfp_pipeline;
    drop policy if exists rfp_final_document_backend on public.rfp_final_document;
    create policy rfp_final_document_backend on public.rfp_final_document for all to rfp_pipeline using (true) with check (true);
    drop policy if exists rfp_trace_backend on public.rfp_trace;
    create policy rfp_trace_backend on public.rfp_trace for all to rfp_pipeline using (true) with check (true);
    drop policy if exists checkpoint_migrations_backend on public.checkpoint_migrations;
    create policy checkpoint_migrations_backend on public.checkpoint_migrations for all to rfp_pipeline using (true) with check (true);
    drop policy if exists checkpoints_backend on public.checkpoints;
    create policy checkpoints_backend on public.checkpoints for all to rfp_pipeline using (true) with check (true);
    drop policy if exists checkpoint_blobs_backend on public.checkpoint_blobs;
    create policy checkpoint_blobs_backend on public.checkpoint_blobs for all to rfp_pipeline using (true) with check (true);
    drop policy if exists checkpoint_writes_backend on public.checkpoint_writes;
    create policy checkpoint_writes_backend on public.checkpoint_writes for all to rfp_pipeline using (true) with check (true);
  end if;
end $$;
