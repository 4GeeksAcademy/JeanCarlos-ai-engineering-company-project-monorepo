-- Real-time notifications of the RFP flow (Sistemas en Tiempo Real, Parte 1).
-- One row per published event; `id` is the SSE `id:` (Last-Event-ID replay after a reconnection).
-- (event, ticket_id) is unique: a ticket is announced once, even if it is re-processed.
-- No PHI: the payload holds only ticket fields (ticket_id, rfp_id, client_name, client_country,
-- program_type, status, created_at). Mirrors data/pipelines/rfp_intake/models.py (RfpEvent).

create table if not exists public.rfp_event (
  id serial primary key,
  event varchar not null,                    -- rfp_ticket_created
  ticket_id varchar(36) not null references public.rfp_ticket (ticket_id) on delete cascade,
  payload json not null,
  created_at timestamptz not null default now(),
  constraint rfp_event_unique unique (event, ticket_id)
);
create index if not exists ix_rfp_event_event on public.rfp_event (event);
create index if not exists ix_rfp_event_ticket_id on public.rfp_event (ticket_id);

alter table public.rfp_event enable row level security;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'rfp_pipeline') then
    grant select, insert on public.rfp_event to rfp_pipeline;
    grant usage, select on sequence public.rfp_event_id_seq to rfp_pipeline;
    drop policy if exists rfp_event_backend on public.rfp_event;
    create policy rfp_event_backend on public.rfp_event for all to rfp_pipeline using (true) with check (true);
  end if;
end $$;
