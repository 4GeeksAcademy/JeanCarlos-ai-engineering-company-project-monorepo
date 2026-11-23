"""Persistence of the intake (PostgreSQL / Supabase through SQLModel). Every graph node writes its
result as soon as it finishes, so the ticket always reflects how far the pipeline got."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from data.process.rag import REPO_ROOT

from .models import RfpDepartmentSection, RfpMetadata, RfpTicket, create_tables, utcnow

MAX_PDF_BYTES = 10 * 1024 * 1024


def raw_dir() -> Path:
    """data/raw/rfp/ — runtime artifact of the intake (git-ignored). Files are named by ticket id."""
    return Path(os.environ.get("RFP_RAW_DIR", REPO_ROOT / "data" / "raw" / "rfp"))


class InvalidPdf(ValueError):
    pass


def validate_pdf(content: bytes) -> None:
    if not content:
        raise InvalidPdf("El archivo está vacío.")
    if len(content) > MAX_PDF_BYTES:
        raise InvalidPdf("El PDF supera el límite de 10 MB.")
    if not content.startswith(b"%PDF-"):
        raise InvalidPdf("El archivo no es un PDF.")


def receive(engine: Engine, content: bytes, *, created_by: str | None) -> RfpTicket:
    """Save the PDF under data/raw/rfp/<ticket_id>.pdf and create the ticket in `analizando`."""
    validate_pdf(content)
    create_tables(engine)
    ticket_id = str(uuid.uuid4())
    folder = raw_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ticket_id}.pdf"
    path.write_bytes(content)
    try:
        relative = str(path.relative_to(REPO_ROOT))
    except ValueError:
        relative = str(path)
    ticket = RfpTicket(ticket_id=ticket_id, rfp_id=f"RFP-{utcnow():%Y%m%d}-{ticket_id[:6].upper()}",
                       raw_pdf_path=relative, pdf_sha256=hashlib.sha256(content).hexdigest(), pdf_bytes=len(content),
                       created_by=created_by)
    with Session(engine) as session:
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
    return ticket


def pdf_path(ticket: RfpTicket) -> Path:
    path = Path(ticket.raw_pdf_path)
    return path if path.is_absolute() else REPO_ROOT / path


def update_ticket(engine: Engine, ticket_id: str, **values) -> None:
    with Session(engine) as session:
        ticket = session.get(RfpTicket, ticket_id)
        for key, value in values.items():
            setattr(ticket, key, value)
        ticket.updated_at = utcnow()
        session.add(ticket)
        session.commit()


def save_metadata(engine: Engine, ticket_id: str, metadata: dict, metrics: dict) -> None:
    deadline = metadata.get("deadline")
    values = {
        **{k: metadata.get(k) for k in ("rfp_reference", "client_name", "client_type", "client_country", "program_type",
                                        "program_types", "covered_population", "population_unit", "deadline_text",
                                        "budget_range", "contract_term", "currency", "regulation", "agreement_type",
                                        "departments_needed", "open_questions")},
        "deadline": date.fromisoformat(deadline) if isinstance(deadline, str) else deadline,
        "word_count": metrics.get("words"), "estimated_tokens": metrics.get("estimated_tokens"),
        "pdf_pages": metrics.get("pages"), "flesch_kincaid_grade": metrics.get("flesch_kincaid_grade"),
        "flesch_reading_ease": metrics.get("flesch_reading_ease"), "gunning_fog": metrics.get("gunning_fog"),
        "cost_band": metrics.get("cost_band"), "readability": metrics,
    }
    with Session(engine) as session:
        row = session.get(RfpMetadata, ticket_id) or RfpMetadata(ticket_id=ticket_id)
        for key, value in values.items():
            setattr(row, key, value)
        row.extracted_at = utcnow()
        session.add(row)
        session.commit()


def save_section(engine: Engine, ticket_id: str, section: dict) -> None:
    with Session(engine) as session:
        row = session.exec(select(RfpDepartmentSection).where(RfpDepartmentSection.ticket_id == ticket_id,
                                                              RfpDepartmentSection.department_id == section["department_id"])
                           ).first() or RfpDepartmentSection(ticket_id=ticket_id, department_id=section["department_id"],
                                                              owner=section["owner"])
        for key in ("key_aspects", "requests", "open_questions", "excerpt_ids", "worker"):
            setattr(row, key, section[key])
        row.evaluation_results = {"contains_phi": False}  # Part 2's compliance evaluator can set it to true
        session.add(row)
        session.commit()


def reset_results(engine: Engine, ticket_id: str) -> None:
    """Before a reprocess: remove the results of a previous run (the ticket and PDF stay)."""
    with Session(engine) as session:
        for row in session.exec(select(RfpDepartmentSection).where(RfpDepartmentSection.ticket_id == ticket_id)):
            session.delete(row)
        metadata = session.get(RfpMetadata, ticket_id)
        if metadata:
            session.delete(metadata)
        session.commit()


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def ticket_view(engine: Engine, ticket_id: str) -> dict | None:
    """What the UI shows: ticket + metadata + sections (never the PDF nor the document text)."""
    with Session(engine) as session:
        ticket = session.get(RfpTicket, ticket_id)
        if ticket is None:
            return None
        metadata = session.get(RfpMetadata, ticket_id)
        sections = session.exec(select(RfpDepartmentSection).where(RfpDepartmentSection.ticket_id == ticket_id)
                                .order_by(RfpDepartmentSection.id)).all()
        data = {k: _jsonable(v) for k, v in ticket.model_dump().items() if k not in ("document_markdown", "raw_pdf_path")}
        data["metadata"] = {k: _jsonable(v) for k, v in metadata.model_dump().items()} if metadata else None
        data["sections"] = [{k: _jsonable(v) for k, v in s.model_dump().items()} for s in sections]
        return data


def list_tickets(engine: Engine, limit: int = 50) -> list[dict]:
    create_tables(engine)
    with Session(engine) as session:
        rows = session.exec(select(RfpTicket, RfpMetadata).join(RfpMetadata, isouter=True)
                            .order_by(RfpTicket.created_at.desc()).limit(limit)).all()
        return [{"ticket_id": t.ticket_id, "rfp_id": t.rfp_id, "status": t.status, "stage": t.stage, "error": t.error,
                 "phi_detected": t.phi_detected, "client_name": m.client_name if m else None,
                 "client_country": m.client_country if m else None, "created_at": _jsonable(t.created_at),
                 "updated_at": _jsonable(t.updated_at)} for t, m in rows]
