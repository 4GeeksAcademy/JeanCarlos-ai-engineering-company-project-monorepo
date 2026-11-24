-- RFP response generation (Hito 9 Parte 2): drafts, EvaluationResult and progress per section,
-- plus the Part 3 queue on the ticket. Mirrors data/pipelines/rfp_intake/models.py.
--
-- No PHI: drafts are stored only after PHI redaction (a draft that contained patient data is
-- stored redacted and its EvaluationResult carries contains_phi = true, never the matched text).

alter table public.rfp_ticket
  add column if not exists response_handoff json,                               -- contract rfp_response.handoff/v1 (Part 3 input)
  add column if not exists ready_for_approval boolean not null default false;  -- queue flag read by Part 3
create index if not exists ix_rfp_ticket_ready_for_approval on public.rfp_ticket (ready_for_approval);

alter table public.rfp_ticket drop constraint if exists rfp_ticket_stage_check;
alter table public.rfp_ticket add constraint rfp_ticket_stage_check check (stage is null or stage in (
  'received', 'converting', 'screening_phi', 'classifying', 'extracting', 'orchestrating', 'workers', 'synthesizing',
  'done', 'discarded', 'failed', 'generating', 'evaluating', 'generated', 'generation_failed'));

alter table public.rfp_department_section
  add column if not exists generation_status varchar not null default 'not_started',
  add column if not exists iterations integer not null default 0,     -- generator → evaluators cycles used
  add column if not exists evaluation_history json,                    -- every iteration: redacted draft + EvaluationResult
  add column if not exists updated_at timestamptz;

alter table public.rfp_department_section drop constraint if exists rfp_department_section_generation_status_check;
alter table public.rfp_department_section add constraint rfp_department_section_generation_status_check
  check (generation_status is null or generation_status in (
    'not_started', 'generating', 'evaluating', 'revising', 'passed', 'needs_human_review'));
