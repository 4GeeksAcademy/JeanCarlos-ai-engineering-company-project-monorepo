"""RFP intake tables in PostgreSQL (Supabase) via SQLModel — the same DB layer as incidents and
inventory. BRIEFING Hito 9 §2.3: Ticket, RFP metadata and DepartmentSection. No column holds a
patient identifier: documents are stored only after PHI redaction, PHI is recorded as categories,
and the PDF path is named by the ticket id (never by the uploaded file name).

The same rows continue in Parts 2 and 3 (draft_content, evaluation_results, approvals), so the
full status vocabulary of the BRIEFING is allowed; Part 1 only writes analizando, descartado and
analisis_completo.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from .departments import DEPARTMENT_IDS

STATUSES = ("analizando", "descartado", "analisis_completo", "generando_borrador", "en_evaluación",
            "needs_human_review", "esperando_aprobación", "terminado")
STAGES = ("received", "converting", "screening_phi", "classifying", "extracting", "orchestrating", "workers",
          "synthesizing", "done", "discarded", "failed",
          # Part 2: generation and evaluation
          "generating", "evaluating", "generated", "generation_failed",
          # Part 3: approval, arbitration and closing
          "awaiting_approval", "arbitration", "finalizing", "completed", "approval_rejected",
          "discarded_by_compliance", "approval_failed")
SECTION_STATUSES = ("not_started", "generating", "evaluating", "revising", "passed", "needs_human_review")
COUNTRIES = ("US", "UK")
PROGRAM_TYPES = ("occupational_health", "corporate_wellness", "referral_network", "other")
APPROVAL_STATUSES = ("pending", "approved", "request_changes", "rejected")


def _one_of(table: str, column: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IS NULL OR {column} IN ({allowed})", name=f"{table}_{column}_check")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(**kwargs) -> Any:
    return Field(default_factory=utcnow, sa_type=DateTime(timezone=True), **kwargs)


class RfpTicket(SQLModel, table=True):
    __tablename__ = "rfp_ticket"
    __table_args__ = (_one_of("rfp_ticket", "status", STATUSES), _one_of("rfp_ticket", "stage", STAGES))

    ticket_id: str = Field(primary_key=True, max_length=36)
    rfp_id: str = Field(sa_column=Column(String(32), unique=True, nullable=False))
    status: str = Field(default="analizando", index=True)
    stage: str = Field(default="received")
    raw_pdf_path: str
    pdf_sha256: str = Field(max_length=64)
    pdf_bytes: int
    created_by: str | None = None
    error: str | None = Field(default=None, sa_type=Text)  # last failure (step + error type), never document text
    classification: dict | None = Field(default=None, sa_type=JSON)
    phi_detected: bool = False
    phi_categories: list | None = Field(default=None, sa_type=JSON)
    compliance_review_required: bool = False
    document_markdown: str | None = Field(default=None, sa_type=Text)  # PHI-redacted Markdown (Part 2 input)
    synthesis: dict | None = Field(default=None, sa_type=JSON)
    handoff: dict | None = Field(default=None, sa_type=JSON)  # contract rfp_intake.handoff/v1 for Part 2
    ready_for_generation: bool = Field(default=False, index=True)  # queue flag read by Part 2
    response_handoff: dict | None = Field(default=None, sa_type=JSON)  # contract rfp_response.handoff/v1 for Part 3
    ready_for_approval: bool = Field(default=False, index=True)  # queue flag read by Part 3
    # Part 3: arbitration record (trigger, fixed arbiter, rule, resolution, round) and the decision
    # a named arbiter is being asked for right now (None when nobody is asked)
    arbitration: list | None = Field(default=None, sa_type=JSON)
    pending_arbitration: dict | None = Field(default=None, sa_type=JSON)
    approval_round: int = 0  # arbitration rounds used (limit: approval.MAX_ARBITRATION_ROUNDS)
    created_at: datetime = _ts()
    updated_at: datetime = _ts()
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class RfpMetadata(SQLModel, table=True):
    __tablename__ = "rfp_metadata"
    __table_args__ = (_one_of("rfp_metadata", "client_country", COUNTRIES),
                      _one_of("rfp_metadata", "program_type", PROGRAM_TYPES),
                      CheckConstraint("covered_population IS NULL OR covered_population > 0",
                                      name="rfp_metadata_population_check"))

    ticket_id: str = Field(sa_column=Column(String(36), ForeignKey("rfp_ticket.ticket_id", ondelete="CASCADE"),
                                            primary_key=True))
    rfp_reference: str | None = None
    client_name: str | None = None
    client_type: str | None = None  # employer / university / other institution
    client_country: str | None = None
    program_type: str | None = None
    program_types: list | None = Field(default=None, sa_type=JSON)
    covered_population: int | None = None
    population_unit: str | None = None  # employees / students
    deadline: date | None = None
    deadline_text: str | None = None
    budget_range: str | None = None
    contract_term: str | None = None
    currency: str | None = None  # USD for US, GBP for UK (BRIEFING §5)
    regulation: str | None = None  # HIPAA / UK GDPR
    agreement_type: str | None = None  # BAA / DPA
    departments_needed: list | None = Field(default=None, sa_type=JSON)
    open_questions: list | None = Field(default=None, sa_type=JSON)
    # readability / processing-cost metrics of the converted Markdown
    word_count: int | None = None
    estimated_tokens: int | None = None
    pdf_pages: int | None = None
    flesch_kincaid_grade: float | None = None
    flesch_reading_ease: float | None = None
    gunning_fog: float | None = None
    cost_band: str | None = None
    readability: dict | None = Field(default=None, sa_type=JSON)
    extracted_at: datetime = _ts()


class RfpDepartmentSection(SQLModel, table=True):
    __tablename__ = "rfp_department_section"
    __table_args__ = (UniqueConstraint("ticket_id", "department_id", name="rfp_department_section_unique"),
                      _one_of("rfp_department_section", "department_id", DEPARTMENT_IDS),
                      _one_of("rfp_department_section", "approval_status", APPROVAL_STATUSES),
                      _one_of("rfp_department_section", "generation_status", SECTION_STATUSES))

    id: int | None = Field(default=None, primary_key=True)
    ticket_id: str = Field(sa_column=Column(String(36), ForeignKey("rfp_ticket.ticket_id", ondelete="CASCADE"),
                                            nullable=False, index=True))
    department_id: str
    owner: str
    key_aspects: list = Field(default_factory=list, sa_type=JSON)
    requests: list = Field(default_factory=list, sa_type=JSON)  # what Revenue must ask this department
    open_questions: list = Field(default_factory=list, sa_type=JSON)
    excerpt_ids: list = Field(default_factory=list, sa_type=JSON)
    worker: str = "model"  # model / fallback
    # Parts 2 and 3
    draft_content: str | None = Field(default=None, sa_type=Text)  # last draft, PHI-redacted
    evaluation_results: dict | None = Field(default=None, sa_type=JSON)  # last EvaluationResult (incl. contains_phi)
    generation_status: str = "not_started"
    iterations: int = 0  # generator → evaluators cycles used (limit: guidelines.MAX_ITERATIONS)
    evaluation_history: list | None = Field(default=None, sa_type=JSON)  # every iteration: draft + EvaluationResult
    updated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    approval_status: str = "pending"
    approver: str | None = None
    approved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    # Part 3: what the approver sees, what they decided, and the revision rounds they used
    approval_packet: dict | None = Field(default=None, sa_type=JSON)
    approval_comment: str | None = Field(default=None, sa_type=Text)
    confirmed_capacity: int | None = None  # people the clinical staff can cover (Dr. Marcus Reid)
    revision_rounds: int = 0  # request_changes used (limit: approval.MAX_REVISION_ROUNDS)
    decision_history: list | None = Field(default=None, sa_type=JSON)
    created_at: datetime = _ts()


class RfpFinalDocument(SQLModel, table=True):
    """BRIEFING §2.3 FinalDocument: ticket_id, sections, currency, generated_at (+ the approvals and
    the arbitration record it was built from, and the Markdown delivered to Revenue Cycle)."""
    __tablename__ = "rfp_final_document"

    ticket_id: str = Field(sa_column=Column(String(36), ForeignKey("rfp_ticket.ticket_id", ondelete="CASCADE"),
                                            primary_key=True))
    rfp_id: str
    client_name: str | None = None
    client_country: str | None = None
    currency: str | None = None
    regulation: str | None = None
    agreement_type: str | None = None
    sections: list = Field(default_factory=list, sa_type=JSON)  # approved sections, in the BRIEFING order
    approvals: list = Field(default_factory=list, sa_type=JSON)
    arbitration: list = Field(default_factory=list, sa_type=JSON)
    markdown: str = Field(sa_type=Text)
    generated_at: datetime = _ts()


class RfpTrace(SQLModel, table=True):
    """One row per node execution of the three graphs: agent, input, output, timestamps."""
    __tablename__ = "rfp_trace"

    id: int | None = Field(default=None, primary_key=True)
    ticket_id: str = Field(sa_column=Column(String(36), ForeignKey("rfp_ticket.ticket_id", ondelete="CASCADE"),
                                            nullable=False, index=True))
    thread_id: str | None = None
    node: str
    agent: str
    status: str  # ok / interrupted / error
    input: dict | list | None = Field(default=None, sa_type=JSON)
    output: dict | list | None = Field(default=None, sa_type=JSON)
    started_at: datetime = Field(sa_type=DateTime(timezone=True))
    finished_at: datetime = Field(sa_type=DateTime(timezone=True))


TABLES = [RfpTicket.__table__, RfpMetadata.__table__, RfpDepartmentSection.__table__, RfpFinalDocument.__table__,
          RfpTrace.__table__]


def create_tables(engine) -> None:
    SQLModel.metadata.create_all(engine, tables=TABLES)
