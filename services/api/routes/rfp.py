"""RFP intake (Hito 9 Parte 1) on the existing API: the router only triggers and reads; the
agentic flow lives in data/pipelines/rfp_intake/.

- POST /api/rfp/tickets        multipart PDF → 202 {ticket_id, status: analizando}; the pipeline
                               runs in the background (FastAPI BackgroundTasks, worker thread).
- GET  /api/rfp/tickets        ticket list (polled by the backoffice).
- GET  /api/rfp/tickets/{id}   ticket + metadata + readability + sections + synthesis + handoff.
- POST /api/rfp/tickets/{id}/generate
                               Part 2: takes the ticket from the Part 1 queue (analisis_completo +
                               ready_for_generation) → 202; generators + parallel evaluators run in
                               the background and the ticket moves generando_borrador → en_evaluación
                               (→ needs_human_review). `?retry=true` re-runs a failed generation.

After a successful intake the generation starts automatically (RFP_AUTO_GENERATE=0 disables it).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Path, UploadFile, status
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from data.pipelines.rfp_intake import response_runner, runner, store
from data.pipelines.rfp_intake.response_graph import NotReadyForGeneration

from ..core.dependencies import AuthenticatedUser
from ..database import get_inventory_engine

logger = logging.getLogger("api.rfp_intake")
router = APIRouter(prefix="/api/rfp", tags=["rfp-intake"])
TicketId = Annotated[str, Path(pattern=r"^[0-9a-f-]{36}$")]


@lru_cache(maxsize=1)
def _dedicated_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise RuntimeError("RFP_DATABASE_URL must use PostgreSQL")
    return create_engine(parsed.set(drivername="postgresql+psycopg"), pool_pre_ping=True)


def get_rfp_engine() -> Engine:
    """RFP_DATABASE_URL (e.g. the Supabase pooler with the rfp_pipeline role) or, if unset, the
    same PostgreSQL as incidents and inventory (DATABASE_URL)."""
    try:
        url = os.environ.get("RFP_DATABASE_URL", "").strip()
        return _dedicated_engine(url) if url else get_inventory_engine()
    except (RuntimeError, ValueError, SQLAlchemyError) as error:
        logger.error("RFP database configuration failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="RFP database unavailable") from error


Engine_ = Annotated[Engine, Depends(get_rfp_engine)]


def auto_generate() -> bool:
    return os.environ.get("RFP_AUTO_GENERATE", "1").strip().lower() not in {"0", "false", "no"}


def run_in_background(engine: Engine, ticket_id: str) -> None:
    """Part 1, then — if the ticket was queued for generation — Part 2 in the same worker thread."""
    try:
        status = runner.run_intake(engine, ticket_id)
        if status == "analisis_completo" and auto_generate():
            run_generation_in_background(engine, ticket_id)
    except Exception as error:  # run_intake records failures; this only guards the thread
        logger.error("rfp_intake crashed ticket_id=%s error=%s", ticket_id, type(error).__name__)


def run_generation_in_background(engine: Engine, ticket_id: str, retry: bool = False, claimed: bool = False) -> None:
    try:
        response_runner.run_generation(engine, ticket_id, retry=retry, claimed=claimed)
    except NotReadyForGeneration:
        logger.info("rfp_response skipped ticket_id=%s (not in the generation queue)", ticket_id)
    except Exception as error:  # run_generation records failures; this only guards the thread
        logger.error("rfp_response crashed ticket_id=%s error=%s", ticket_id, type(error).__name__)


@router.post("/tickets", status_code=status.HTTP_202_ACCEPTED)
async def upload_rfp(user: AuthenticatedUser, engine: Engine_, background: BackgroundTasks,
                     file: Annotated[UploadFile, File(description="RFP en PDF")]) -> dict[str, Any]:
    content = await file.read(store.MAX_PDF_BYTES + 1)
    try:
        ticket = store.receive(engine, content, created_by=str(user.id))
    except store.InvalidPdf as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except SQLAlchemyError as error:
        logger.error("RFP ticket creation failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="RFP database unavailable") from error
    background.add_task(run_in_background, engine, ticket.ticket_id)
    logger.info("rfp_intake accepted ticket_id=%s bytes=%s", ticket.ticket_id, ticket.pdf_bytes)
    return {"ticket_id": ticket.ticket_id, "rfp_id": ticket.rfp_id, "status": ticket.status, "stage": ticket.stage}


@router.get("/tickets")
def list_rfp_tickets(_user: AuthenticatedUser, engine: Engine_) -> list[dict[str, Any]]:
    try:
        return store.list_tickets(engine)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="RFP database unavailable") from error


@router.get("/tickets/{ticket_id}")
def get_rfp_ticket(ticket_id: TicketId, _user: AuthenticatedUser, engine: Engine_) -> dict[str, Any]:
    try:
        view = store.ticket_view(engine, ticket_id)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="RFP database unavailable") from error
    if view is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return view


@router.post("/tickets/{ticket_id}/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_proposal(ticket_id: TicketId, user: AuthenticatedUser, engine: Engine_, background: BackgroundTasks,
                      retry: bool = False) -> dict[str, Any]:
    """The ticket is claimed here, synchronously (one conditional UPDATE → generando_borrador), so
    the 202 already reflects the new status and two clicks can never generate it twice."""
    try:
        if store.ticket_view(engine, ticket_id) is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        response_runner.claim(engine, ticket_id, retry=retry)
    except NotReadyForGeneration as error:
        raise HTTPException(status_code=409, detail="El ticket no está listo para generar la propuesta "
                                                    "(requiere analisis_completo y ready_for_generation).") from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="RFP database unavailable") from error
    background.add_task(run_generation_in_background, engine, ticket_id, retry, True)
    logger.info("rfp_response queued ticket_id=%s by=%s", ticket_id, user.id)
    return {"ticket_id": ticket_id, "status": "generando_borrador", "queued": True}
