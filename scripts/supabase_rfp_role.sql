-- Login role for the RFP intake on Supabase (Hito 9). Run it once in the Supabase SQL editor,
-- replacing <SCRAM_VERIFIER> with the output of:
--   python scripts/rfp_intake.py scram-verifier   (prompts for a password, prints only the verifier)
-- Then set RFP_DATABASE_URL=postgresql://rfp_pipeline.<project-ref>:<password>@<pooler-host>:6543/postgres
-- in services/api/.env (never commit it). The tables come from
-- supabase/migrations/20261123230000_rfp_intake.sql (RLS on, no policies).

create role rfp_pipeline login password '<SCRAM_VERIFIER>';
grant usage on schema public to rfp_pipeline;
grant select, insert, update, delete on public.rfp_ticket, public.rfp_metadata, public.rfp_department_section to rfp_pipeline;
grant usage, select on sequence public.rfp_department_section_id_seq to rfp_pipeline;
-- Part 3 (after supabase/migrations/20261125010000_rfp_approval_completion.sql, which also grants these when the
-- role already exists): final document, trace and the LangGraph checkpointer tables
grant select, insert, update, delete on public.rfp_final_document, public.rfp_trace, public.checkpoint_migrations,
  public.checkpoints, public.checkpoint_blobs, public.checkpoint_writes to rfp_pipeline;
grant usage, select on sequence public.rfp_trace_id_seq to rfp_pipeline;
-- real-time notifications (supabase/migrations/20261125020000_rfp_realtime_events.sql)
grant select, insert on public.rfp_event to rfp_pipeline;
grant usage, select on sequence public.rfp_event_id_seq to rfp_pipeline;
-- RLS is enabled on the three tables: this role is the backend, so it gets full access to them only.
create policy rfp_ticket_backend on public.rfp_ticket for all to rfp_pipeline using (true) with check (true);
create policy rfp_metadata_backend on public.rfp_metadata for all to rfp_pipeline using (true) with check (true);
create policy rfp_department_section_backend on public.rfp_department_section for all to rfp_pipeline using (true) with check (true);
create policy rfp_final_document_backend on public.rfp_final_document for all to rfp_pipeline using (true) with check (true);
create policy rfp_trace_backend on public.rfp_trace for all to rfp_pipeline using (true) with check (true);
create policy checkpoint_migrations_backend on public.checkpoint_migrations for all to rfp_pipeline using (true) with check (true);
create policy checkpoints_backend on public.checkpoints for all to rfp_pipeline using (true) with check (true);
create policy checkpoint_blobs_backend on public.checkpoint_blobs for all to rfp_pipeline using (true) with check (true);
create policy checkpoint_writes_backend on public.checkpoint_writes for all to rfp_pipeline using (true) with check (true);
create policy rfp_event_backend on public.rfp_event for all to rfp_pipeline using (true) with check (true);
