-- Read-only role for GET /telemetry/report. It only sees the columns the technical
-- report aggregates; pseudonymous session/user/request ids stay unreadable.
-- Its password is set outside version control:
--   alter role telemetry_reader with login password '<scram verifier>';
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'telemetry_reader') then
    create role telemetry_reader nologin;
  end if;
end
$$;

grant usage on schema public to telemetry_reader;
grant select ("timestamp", event_type, tags) on public.telemetry_events to telemetry_reader;

drop policy if exists telemetry_reader_select on public.telemetry_events;
create policy telemetry_reader_select on public.telemetry_events
  for select to telemetry_reader using (true);
