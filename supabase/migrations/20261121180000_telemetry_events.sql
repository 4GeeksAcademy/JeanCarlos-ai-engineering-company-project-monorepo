-- Append-only store for backoffice telemetry (contract docs/telemetry/event-schemas.json).
-- One row per TelemetryEvent envelope; `tags` holds the allowlisted `properties`.
create table if not exists public.telemetry_events (
  id          uuid        primary key,          -- envelope eventId (idempotent on client retries)
  "timestamp" timestamptz not null,             -- capture time, not insert time
  service     text        not null,             -- producer, e.g. 'backoffice'
  event_type  text        not null,
  session_id  text,                             -- pseudonymous session id
  user_id     text,                             -- HMAC pseudonym, never a raw account id
  request_id  text,                             -- correlation id
  tags        jsonb       not null default '{}'::jsonb
);

comment on table public.telemetry_events is
  'Immutable telemetry events. Insert-only: UPDATE and DELETE are blocked by trigger.';
comment on column public.telemetry_events.tags is
  'Event properties restricted to the event_type allowlist, plus _schema_version. Never PHI.';

create index if not exists telemetry_events_timestamp_idx on public.telemetry_events ("timestamp");
create index if not exists telemetry_events_event_type_idx on public.telemetry_events (event_type);
create index if not exists telemetry_events_tags_gin_idx on public.telemetry_events using gin (tags);

-- Events are facts: reject any UPDATE or DELETE, whoever issues it.
create or replace function public.telemetry_events_immutable()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'telemetry_events is append-only: % is not allowed', tg_op
    using errcode = 'insufficient_privilege';
end;
$$;

drop trigger if exists telemetry_events_no_update_delete on public.telemetry_events;
create trigger telemetry_events_no_update_delete
  before update or delete on public.telemetry_events
  for each row execute function public.telemetry_events_immutable();

-- Only the backend writes, through a direct database connection. The Data API
-- roles get no access at all (RLS enabled, no policies, privileges revoked).
alter table public.telemetry_events enable row level security;
revoke all on public.telemetry_events from anon, authenticated;
revoke update, delete, truncate on public.telemetry_events from service_role;
