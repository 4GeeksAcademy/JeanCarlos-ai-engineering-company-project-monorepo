-- RFP intake (Hito 9 Parte 1): ticket, RFP metadata and per-department key aspects.
-- Mirrors data/pipelines/rfp_intake/models.py (SQLModel); the API also creates them with
-- create_all on first use, so this migration is the reviewed DDL for Supabase.
--
-- HIPAA / UK GDPR: no column stores patient data. The document is stored only after PHI
-- redaction (rfp_ticket.document_markdown), PHI is recorded as categories, and the PDF path is
-- named by ticket id. RLS is enabled with no policies: only the backend role can read/write.

create table if not exists public.rfp_ticket (
  ticket_id varchar(36) primary key,
  rfp_id varchar(32) not null unique,
  status varchar not null default 'analizando'
    constraint rfp_ticket_status_check check (status is null or status in (
      'analizando', 'descartado', 'analisis_completo', 'generando_borrador', 'en_evaluación',
      'needs_human_review', 'esperando_aprobación', 'terminado')),
  stage varchar not null default 'received'
    constraint rfp_ticket_stage_check check (stage is null or stage in (
      'received', 'converting', 'screening_phi', 'classifying', 'extracting', 'orchestrating',
      'workers', 'synthesizing', 'done', 'discarded', 'failed')),
  raw_pdf_path varchar not null,
  pdf_sha256 varchar(64) not null,
  pdf_bytes integer not null,
  created_by varchar,
  error text,
  classification json,
  phi_detected boolean not null default false,
  phi_categories json,
  compliance_review_required boolean not null default false,
  document_markdown text,
  synthesis json,
  handoff json,                                    -- contract rfp_intake.handoff/v1 (Part 2 input)
  ready_for_generation boolean not null default false,  -- queue flag read by Part 2
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);
create index if not exists ix_rfp_ticket_status on public.rfp_ticket (status);
create index if not exists ix_rfp_ticket_ready_for_generation on public.rfp_ticket (ready_for_generation);

create table if not exists public.rfp_metadata (
  ticket_id varchar(36) primary key references public.rfp_ticket (ticket_id) on delete cascade,
  rfp_reference varchar,
  client_name varchar,
  client_type varchar,
  client_country varchar constraint rfp_metadata_client_country_check
    check (client_country is null or client_country in ('US', 'UK')),
  program_type varchar constraint rfp_metadata_program_type_check
    check (program_type is null or program_type in ('occupational_health', 'corporate_wellness', 'referral_network', 'other')),
  program_types json,
  covered_population integer constraint rfp_metadata_population_check
    check (covered_population is null or covered_population > 0),
  population_unit varchar,
  deadline date,
  deadline_text varchar,
  budget_range varchar,
  contract_term varchar,
  currency varchar,
  regulation varchar,
  agreement_type varchar,
  departments_needed json,
  open_questions json,
  word_count integer,
  estimated_tokens integer,
  pdf_pages integer,
  flesch_kincaid_grade double precision,
  flesch_reading_ease double precision,
  gunning_fog double precision,
  cost_band varchar,
  readability json,
  extracted_at timestamptz not null default now()
);

create table if not exists public.rfp_department_section (
  id serial primary key,
  ticket_id varchar(36) not null references public.rfp_ticket (ticket_id) on delete cascade,
  department_id varchar not null constraint rfp_department_section_department_id_check
    check (department_id is null or department_id in ('revenue', 'clinical', 'compliance')),
  owner varchar not null,
  key_aspects json not null,
  requests json not null,
  open_questions json not null,
  excerpt_ids json not null,
  worker varchar not null,
  draft_content text,
  evaluation_results json,                         -- includes contains_phi: bool (Part 2)
  approval_status varchar not null default 'pending' constraint rfp_department_section_approval_status_check
    check (approval_status is null or approval_status in ('pending', 'approved', 'request_changes', 'rejected')),
  approver varchar,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  constraint rfp_department_section_unique unique (ticket_id, department_id)
);
create index if not exists ix_rfp_department_section_ticket_id on public.rfp_department_section (ticket_id);

alter table public.rfp_ticket enable row level security;
alter table public.rfp_metadata enable row level security;
alter table public.rfp_department_section enable row level security;
