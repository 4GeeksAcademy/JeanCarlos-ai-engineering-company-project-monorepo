-- Least-privilege role used by the FastAPI receiver: it can only INSERT telemetry.
-- Its password is set outside version control:
--   alter role telemetry_writer with login password '<scram verifier>';
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'telemetry_writer') then
    create role telemetry_writer nologin;
  end if;
end
$$;

grant usage on schema public to telemetry_writer;
grant insert on public.telemetry_events to telemetry_writer;
-- INSERT ... ON CONFLICT (id) DO NOTHING (idempotent retries) needs SELECT on the
-- conflict column; no other column is readable.
grant select (id) on public.telemetry_events to telemetry_writer;

-- RLS is enabled on telemetry_events: allow this role to insert, nothing else.
drop policy if exists telemetry_writer_insert on public.telemetry_events;
create policy telemetry_writer_insert on public.telemetry_events
  for insert to telemetry_writer with check (true);

-- ON CONFLICT also evaluates SELECT policies on the new row. Rows are visible to the
-- role, but the column grant above still restricts reads to `id`.
drop policy if exists telemetry_writer_conflict_check on public.telemetry_events;
create policy telemetry_writer_conflict_check on public.telemetry_events
  for select to telemetry_writer using (true);
