"""Run the intake for one ticket (called in the background by the API and by scripts/rfp_intake.py).

The ticket is the source of truth: each node persists its stage and results; if anything fails,
the ticket records the failed stage and the error type (status stays `analizando`, stage
`failed`, never silently) and can be re-run with `python scripts/rfp_intake.py reprocess <id>`.
A failure in one ticket never affects another: each run is independent.
"""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine
from sqlmodel import Session

from . import store
from .graph import build_graph
from .models import RfpTicket

logger = logging.getLogger("api.rfp_intake")

_graph = None


def graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_intake(engine: Engine, ticket_id: str) -> str:
    with Session(engine) as session:
        ticket = session.get(RfpTicket, ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        path = str(store.pdf_path(ticket))
        stage_before = ticket.stage
    store.reset_results(engine, ticket_id)
    store.update_ticket(engine, ticket_id, status="analizando", stage="received", error=None, synthesis=None,
                        handoff=None, ready_for_generation=False, completed_at=None)
    logger.info("rfp_intake start ticket_id=%s previous_stage=%s", ticket_id, stage_before)
    try:
        result = graph().invoke({"ticket_id": ticket_id, "pdf_path": path, "sections": []},
                                config={"configurable": {"engine": engine}})
    except Exception as error:  # recorded on the ticket, never swallowed
        with Session(engine) as session:
            failed_at = session.get(RfpTicket, ticket_id).stage
        store.update_ticket(engine, ticket_id, stage="failed", error=f"{failed_at}: {type(error).__name__}: {str(error)[:200]}")
        logger.error("rfp_intake failed ticket_id=%s stage=%s error=%s", ticket_id, failed_at, type(error).__name__)
        return "failed"
    logger.info("rfp_intake done ticket_id=%s status=%s", ticket_id, result.get("status"))
    return result.get("status", "analizando")
