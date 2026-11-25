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

- GET  /api/rfp/tickets/{id}/approval
                               Part 3: where each checkpointed thread is paused (ticket coordinator +
                               one per department), approval packets, limits, pending arbitration.
- POST /api/rfp/tickets/{id}/approval
                               opens Part 3 for a ticket in the Part 2 queue (en_evaluación /
                               needs_human_review + ready_for_approval) → esperando_aprobación.
- POST /api/rfp/tickets/{id}/sections/{department_id}/decision
                               {action: approve|request_changes|reject, approver, comment,
                               confirmed_capacity} — validated here (409 nobody is asked, 422 invalid)
                               and resumed in the background on rfp-{id}:{department_id}.
- POST /api/rfp/tickets/{id}/arbitration
                               ruling of the fixed arbiter of a BRIEFING §7 trigger.
- GET  /api/rfp/tickets/{id}/document   final document (JSON, or ?format=markdown).
- GET  /api/rfp/tickets/{id}/trace      every node execution: agent, input, output, timestamps.
Who may decide (OWASP A01): the BRIEFING names one approver per department (Tom Callahan, Dr.
Marcus Reid, Claire Whitfield); RFP_APPROVERS maps each department to the backoffice accounts of
that person, e.g. {"revenue": ["tom.callahan"], "clinical": ["marcus.reid"], "compliance":
["claire.whitfield"]}. A decision or an arbitration from any other account is a 403 — typing the
approver's name is not enough — and the account is recorded as `submitted_by`. Unset: nobody can
sign off (fail closed).

- GET  /api/rfp/events         Server-Sent Events (text/event-stream), same JWT as the rest:
                               `rfp_ticket_created` when a new RFP ticket enters the flow, with
                               Last-Event-ID replay (see services/api/realtime.py).

After a successful intake the generation starts automatically (RFP_AUTO_GENERATE=0 disables it),
and after the generation the approval flow opens automatically (RFP_AUTO_APPROVAL=0 disables it).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Annotated, Any

import threading
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Header, HTTPException, Path, UploadFile, status
from fastapi.responses import PlainTextResponse, StreamingResponse
import jwt
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from data.pipelines.connections import require_tls
from data.pipelines.rfp_intake import approval, approval_runner, response_runner, runner, store
from data.pipelines.rfp_intake.approval_graph import NotReadyForApproval
from data.pipelines.rfp_intake.departments import DEPARTMENT_IDS
from data.pipelines.rfp_intake.response_graph import NotReadyForGeneration

from .. import realtime
from ..core.dependencies import AuthenticatedUser, oauth2_scheme
from ..database import get_inventory_engine

logger = logging.getLogger("api.rfp_intake")
router = APIRouter(prefix="/api/rfp", tags=["rfp-intake"])
TicketId = Annotated[str, Path(pattern=r"^[0-9a-f-]{36}$")]
DepartmentId = Annotated[str, Path(pattern="^(" + "|".join(DEPARTMENT_IDS) + ")$")]


@lru_cache(maxsize=1)
def _dedicated_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise RuntimeError("RFP_DATABASE_URL must use PostgreSQL")
    return create_engine(require_tls(parsed.set(drivername="postgresql+psycopg")), pool_pre_ping=True)


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


def auto_approval() -> bool:
    return os.environ.get("RFP_AUTO_APPROVAL", "1").strip().lower() not in {"0", "false", "no"}


def run_generation_in_background(engine: Engine, ticket_id: str, retry: bool = False, claimed: bool = False) -> None:
    """Part 2, then — if it produced the Part 3 handoff — open the approval flow (Part 3)."""
    try:
        status = response_runner.run_generation(engine, ticket_id, retry=retry, claimed=claimed)
        if status in store.APPROVAL_INPUT and auto_approval():
            run_approval_in_background(engine, ticket_id)
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


# ================================================================ Part 3: approval, arbitration and closing


def _view_or_404(engine: Engine, ticket_id: str) -> dict:
    try:
        view = store.ticket_view(engine, ticket_id)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="RFP database unavailable") from error
    if view is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return view


def run_approval_in_background(engine: Engine, ticket_id: str, claimed: bool = False) -> None:
    try:
        approval_runner.open_approval(engine, ticket_id, claimed=claimed)
    except NotReadyForApproval:
        logger.info("rfp_approval skipped ticket_id=%s (not in the approval queue)", ticket_id)
    except Exception as error:  # open_approval records failures; this only guards the thread
        logger.error("rfp_approval crashed ticket_id=%s error=%s", ticket_id, type(error).__name__)


def rfp_approvers() -> dict[str, set[str]]:
    """RFP_APPROVERS (JSON): department → backoffice usernames allowed to decide for it."""
    import json

    try:
        raw = json.loads(os.environ.get("RFP_APPROVERS", "") or "{}")
    except ValueError:
        logger.error("RFP_APPROVERS is not valid JSON: nobody can sign off")
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(d): {str(u).lower() for u in users} for d, users in raw.items() if isinstance(users, list)}


def require_approver(user, department_id: str) -> None:
    from data.pipelines.rfp_intake.departments import DEPARTMENTS

    if user.username.lower() not in rfp_approvers().get(department_id, set()):
        department = DEPARTMENTS[department_id]
        logger.warning("rfp_approval refused department=%s by=%s (not a registered approver)", department_id, user.id)
        raise HTTPException(status_code=403, detail=f"Tu cuenta no está registrada como aprobadora de {department.name} "
                                                    f"(BRIEFING: decide {department.owner}).")


def _decision_in_background(engine: Engine, ticket_id: str, department_id: str, decision: dict) -> None:
    try:
        approval_runner.submit_decision(engine, ticket_id, department_id, decision)
    except (approval.InvalidDecision, approval_runner.NotAwaitingDecision) as error:
        logger.info("rfp_approval decision dropped ticket_id=%s department=%s reason=%s", ticket_id, department_id,
                    type(error).__name__)
    except Exception as error:  # recorded on the ticket by the runner
        logger.error("rfp_approval decision crashed ticket_id=%s error=%s", ticket_id, type(error).__name__)


def _arbitration_in_background(engine: Engine, ticket_id: str, decision: dict) -> None:
    try:
        approval_runner.submit_arbitration(engine, ticket_id, decision)
    except (approval.InvalidDecision, approval_runner.NotAwaitingDecision) as error:
        logger.info("rfp_approval arbitration dropped ticket_id=%s reason=%s", ticket_id, type(error).__name__)
    except Exception as error:
        logger.error("rfp_approval arbitration crashed ticket_id=%s error=%s", ticket_id, type(error).__name__)


@router.get("/tickets/{ticket_id}/approval")
def get_approval(ticket_id: TicketId, _user: AuthenticatedUser, engine: Engine_) -> dict[str, Any]:
    view = _view_or_404(engine, ticket_id)
    result = approval_runner.status(engine, ticket_id)
    result["packets"] = {s["department_id"]: s["approval_packet"] for s in view["sections"]}
    result["final_document_available"] = store.final_document(engine, ticket_id) is not None
    return result


@router.post("/tickets/{ticket_id}/approval", status_code=status.HTTP_202_ACCEPTED)
def open_approval(ticket_id: TicketId, user: AuthenticatedUser, engine: Engine_,
                  background: BackgroundTasks) -> dict[str, Any]:
    _view_or_404(engine, ticket_id)
    try:
        approval_runner.claim(engine, ticket_id)
    except NotReadyForApproval as error:
        raise HTTPException(status_code=409, detail="El ticket no está listo para aprobación (requiere en_evaluación "
                                                    "o needs_human_review y ready_for_approval).") from error
    background.add_task(run_approval_in_background, engine, ticket_id, True)
    logger.info("rfp_approval queued ticket_id=%s by=%s", ticket_id, user.id)
    return {"ticket_id": ticket_id, "status": "esperando_aprobación", "queued": True}


@router.post("/tickets/{ticket_id}/sections/{department_id}/decision", status_code=status.HTTP_202_ACCEPTED)
def submit_decision(ticket_id: TicketId, department_id: DepartmentId, user: AuthenticatedUser, engine: Engine_,
                    background: BackgroundTasks, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """The answer is validated BEFORE it can enter the graph; the resume (and a revision, which
    calls the model) runs in the background on the department's own thread."""
    _view_or_404(engine, ticket_id)
    require_approver(user, department_id)  # the account, not the name typed in the body
    body = {**body, "submitted_by": user.username}
    try:
        decision = approval_runner.check_decision(engine, ticket_id, department_id, body)
    except approval_runner.NotAwaitingDecision as error:
        raise HTTPException(status_code=409, detail=f"La sección {department_id} no está esperando aprobación.") from error
    except approval.InvalidDecision as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    background.add_task(_decision_in_background, engine, ticket_id, department_id, decision.model_dump())
    logger.info("rfp_approval decision queued ticket_id=%s department=%s action=%s by=%s", ticket_id, department_id,
                decision.action, user.id)
    return {"ticket_id": ticket_id, "department_id": department_id, "action": decision.action, "queued": True}


@router.post("/tickets/{ticket_id}/arbitration", status_code=status.HTTP_202_ACCEPTED)
def submit_arbitration(ticket_id: TicketId, user: AuthenticatedUser, engine: Engine_, background: BackgroundTasks,
                       body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    _view_or_404(engine, ticket_id)
    body = {**body, "submitted_by": user.username}
    try:
        decision = approval_runner.check_arbitration(engine, ticket_id, body)
    except approval_runner.NotAwaitingDecision as error:
        raise HTTPException(status_code=409, detail="No hay un arbitraje esperando decisión.") from error
    except approval.InvalidDecision as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    require_approver(user, approval.TRIGGERS[decision.trigger].arbiter_department)  # the fixed arbiter's account
    background.add_task(_arbitration_in_background, engine, ticket_id, decision.model_dump())
    logger.info("rfp_approval arbitration queued ticket_id=%s trigger=%s by=%s", ticket_id, decision.trigger, user.id)
    return {"ticket_id": ticket_id, "trigger": decision.trigger, "resolution": decision.resolution, "queued": True}


@router.get("/tickets/{ticket_id}/document", response_model=None)
def get_final_document(ticket_id: TicketId, _user: AuthenticatedUser, engine: Engine_,
                       format: str = "json") -> dict[str, Any] | PlainTextResponse:
    _view_or_404(engine, ticket_id)
    document = store.final_document(engine, ticket_id)
    if document is None:
        raise HTTPException(status_code=404, detail="El documento final todavía no existe.")
    if format == "markdown":
        return PlainTextResponse(document["markdown"], media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{document["rfp_id"]}.md"'})
    return document


@router.get("/tickets/{ticket_id}/trace")
def get_trace(ticket_id: TicketId, _user: AuthenticatedUser, engine: Engine_) -> list[dict[str, Any]]:
    _view_or_404(engine, ticket_id)
    return store.trace(engine, ticket_id)


# ================================================================ real time: Server-Sent Events

MAX_STREAMS_PER_USER = 5
_streams: dict[str, int] = {}
_streams_lock = threading.Lock()


def _token_expiry(token: str) -> datetime | None:
    """The JWT was already verified by AuthenticatedUser; the stream ends when it expires."""
    try:
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
    except Exception:  # noqa: BLE001 — verified above; a malformed claim only disables the cut-off
        return None
    return datetime.fromtimestamp(exp, tz=timezone.utc) if isinstance(exp, (int, float)) else None


@router.get("/events", response_class=StreamingResponse)
def rfp_events(user: AuthenticatedUser, token: Annotated[str, Depends(oauth2_scheme)], engine: Engine_,
               last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None) -> StreamingResponse:
    """Server-Sent Events for Revenue Cycle's dashboard. Named events, JSON `data:`, `id:` =
    rfp_event.id (resume cursor), `: keep-alive` comments. Requires the backoffice JWT
    (Authorization: Bearer); without it: 401 and no event."""
    if not realtime.accepting():  # shutting down: the client retries against the next process
        raise HTTPException(status_code=503, detail="El servicio se está reiniciando.")
    with _streams_lock:
        if _streams.get(user.id, 0) >= MAX_STREAMS_PER_USER:
            raise HTTPException(status_code=429, detail="Demasiadas conexiones en tiempo real abiertas.")

    async def body():
        with _streams_lock:  # counted while the body runs, so a stream that never starts cannot leak a slot
            _streams[user.id] = _streams.get(user.id, 0) + 1
        try:
            async for chunk in realtime.stream(engine, realtime.parse_last_event_id(last_event_id),
                                               _token_expiry(token), str(user.id)):
                yield chunk
        finally:
            with _streams_lock:
                _streams[user.id] -= 1
                if not _streams[user.id]:
                    del _streams[user.id]

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})
