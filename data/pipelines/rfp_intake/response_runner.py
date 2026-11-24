"""Run Part 2 (generation + evaluation) for one ticket — called in the background by the API
(right after the intake, or from POST /api/rfp/tickets/{id}/generate) and by scripts/rfp_intake.py.

The ticket is claimed from the Part 1 queue with one conditional UPDATE (analisis_completo +
ready_for_generation → generando_borrador), so it is generated once. Every node persists its
progress (section status, iteration, draft, EvaluationResult). An unexpected failure is recorded on
the ticket (stage `generation_failed` + error type) and the run can be retried; one department
that cannot pass never discards the ticket.
"""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine
from sqlmodel import Session

from . import store
from .models import RfpTicket
from .response_graph import NotReadyForGeneration, build_graph

logger = logging.getLogger("api.rfp_intake")

_graph = None


def graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def claim(engine: Engine, ticket_id: str, *, retry: bool = False) -> None:
    """Take the ticket from the Part 1 queue (→ generando_borrador) or raise NotReadyForGeneration."""
    if not store.claim_for_generation(engine, ticket_id, retry=retry):
        raise NotReadyForGeneration("the ticket is not analisis_completo + ready_for_generation")


def run_generation(engine: Engine, ticket_id: str, *, retry: bool = False, claimed: bool = False) -> str:
    """Returns the final ticket status, or "failed". Raises NotReadyForGeneration when the ticket
    is not in the Part 1 queue. `claimed`: the API already took it from the queue synchronously."""
    if not claimed:
        claim(engine, ticket_id, retry=retry)
    store.reset_generation(engine, ticket_id)
    logger.info("rfp_response start ticket_id=%s", ticket_id)
    try:
        config = {"configurable": {"engine": engine, "thread_id": f"rfp-{ticket_id}:generation"}}
        result = graph().invoke({"ticket_id": ticket_id, "sections": []}, config=config)
    except Exception as error:  # recorded on the ticket, never swallowed
        with Session(engine) as session:
            stage = session.get(RfpTicket, ticket_id).stage
        store.update_ticket(engine, ticket_id, stage="generation_failed",
                            error=f"{stage}: {type(error).__name__}: {str(error)[:200]}")
        logger.error("rfp_response failed ticket_id=%s stage=%s error=%s", ticket_id, stage, type(error).__name__)
        return "failed"
    logger.info("rfp_response done ticket_id=%s status=%s", ticket_id, result.get("status"))
    return result["status"]
