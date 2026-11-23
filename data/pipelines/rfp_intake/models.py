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
          "synthesizing", "done", "discarded", "failed")
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
                      _one_of("rfp_department_section", "approval_status", APPROVAL_STATUSES))

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
    draft_content: str | None = Field(default=None, sa_type=Text)
    evaluation_results: dict | None = Field(default=None, sa_type=JSON)  # includes contains_phi: bool
    approval_status: str = "pending"
    approver: str | None = None
    approved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = _ts()


TABLES = [RfpTicket.__table__, RfpMetadata.__table__, RfpDepartmentSection.__table__]


def create_tables(engine) -> None:
    SQLModel.metadata.create_all(engine, tables=TABLES)
