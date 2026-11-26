"""Run Part 3 (approval, arbitration and closing) for one ticket — called by the API (right after
Part 2, and from the approval / arbitration endpoints), by scripts/rfp_intake.py and by the E2E
script with simulated approvals.

- open_approval: takes the ticket from the Part 2 queue (→ esperando_aprobación) and starts the
  coordinator thread, which arbitrates and starts every department branch until each pauses on its
  approval.
- submit_decision: resumes ONE department thread with a validated human decision (explicit entry
  point: `Command(resume=...)` on the paused thread, never a new run), then tells the coordinator.
- submit_arbitration: resumes the coordinator with the ruling of the named arbiter.

Validation happens twice: `check_decision` / `check_arbitration` before anything is resumed (the
API answers 409/422 synchronously), and again inside the graph.
"""

from __future__ import annotations

import logging

from langgraph.types import Command
from sqlalchemy.engine import Engine
from sqlmodel import Session

from . import approval, checkpoint, store
from .approval_graph import (NotReadyForApproval, branch_config, branch_graph, paused_at, ticket_config,
                             ticket_graph)
from .departments import DEPARTMENT_IDS
from .models import RfpTicket

logger = logging.getLogger("api.rfp_intake")


class NotAwaitingDecision(RuntimeError):
    """Nobody is being asked for this decision right now (wrong department, or already decided)."""


def claim(engine: Engine, ticket_id: str) -> None:
    if not store.claim_for_approval(engine, ticket_id):
        raise NotReadyForApproval("the ticket is not en_evaluación / needs_human_review + ready_for_approval")


def _fail(engine: Engine, ticket_id: str, error: Exception) -> None:
    with Session(engine) as session:
        stage = session.get(RfpTicket, ticket_id).stage
    store.update_ticket(engine, ticket_id, stage="approval_failed",
                        error=f"{stage}: {type(error).__name__}: {str(error)[:200]}")
    logger.error("rfp_approval failed ticket_id=%s stage=%s error=%s", ticket_id, stage, type(error).__name__)


def open_approval(engine: Engine, ticket_id: str, *, claimed: bool = False) -> dict:
    if not claimed:
        claim(engine, ticket_id)
    store.reset_approval(engine, ticket_id)
    # a new approval run of the same ticket never inherits the checkpoints of a previous one
    checkpoint.delete_threads(engine, [checkpoint.ticket_thread(ticket_id)]
                              + [checkpoint.department_thread(ticket_id, d) for d in DEPARTMENT_IDS])
    config = ticket_config(engine, ticket_id)
    logger.info("rfp_approval open ticket_id=%s", ticket_id)
    try:
        with checkpoint.thread_lock(engine, config["configurable"]["thread_id"]):
            ticket_graph(engine).invoke({"ticket_id": ticket_id}, config=config)
    except Exception as error:  # recorded on the ticket, never swallowed
        _fail(engine, ticket_id, error)
    return status(engine, ticket_id)


def _branch_snapshot(engine: Engine, ticket_id: str, department_id: str):
    if department_id not in DEPARTMENT_IDS:
        raise NotAwaitingDecision(f"unknown department {department_id}")
    return branch_graph(engine).get_state(branch_config(engine, ticket_id, department_id))


def check_decision(engine: Engine, ticket_id: str, department_id: str, raw) -> approval.ApprovalDecision:
    snapshot = _branch_snapshot(engine, ticket_id, department_id)
    if paused_at(snapshot) != "await_approval":
        raise NotAwaitingDecision(f"the {department_id} section is not waiting for approval")
    return approval.validate_decision(snapshot.values["packet"], raw)


def submit_decision(engine: Engine, ticket_id: str, department_id: str, raw) -> dict:
    """Resume the department thread exactly at its interrupt, then notify the coordinator."""
    config = branch_config(engine, ticket_id, department_id)
    graph = branch_graph(engine)
    with checkpoint.thread_lock(engine, config["configurable"]["thread_id"]):  # re-checked under the lock
        decision = check_decision(engine, ticket_id, department_id, raw)
        logger.info("rfp_approval decision ticket_id=%s department=%s action=%s", ticket_id, department_id,
                    decision.action)
        try:
            graph.invoke(Command(resume=decision.model_dump()), config=config)
        except Exception as error:
            _fail(engine, ticket_id, error)
            raise
        where = paused_at(graph.get_state(config))
    notify(engine, ticket_id, {"department_id": department_id, "event": decision.action})
    return {"department_id": department_id, "action": decision.action, "paused_at": where, **status(engine, ticket_id)}


def notify(engine: Engine, ticket_id: str, event: dict) -> str:
    """Wake the coordinator if it is waiting for the departments (it re-reads the approvals)."""
    config = ticket_config(engine, ticket_id)
    graph = ticket_graph(engine)
    with checkpoint.thread_lock(engine, config["configurable"]["thread_id"]):
        if paused_at(graph.get_state(config)) != "await_departments":
            return paused_at(graph.get_state(config))
        try:
            graph.invoke(Command(resume=event), config=config)
        except Exception as error:
            _fail(engine, ticket_id, error)
        return paused_at(graph.get_state(config))


def check_arbitration(engine: Engine, ticket_id: str, raw) -> approval.ArbiterDecision:
    snapshot = ticket_graph(engine).get_state(ticket_config(engine, ticket_id))
    if paused_at(snapshot) != "arbiter_decision":
        raise NotAwaitingDecision("no arbitration is waiting for a decision")
    return approval.validate_arbiter_decision(snapshot.values.get("pending_arbitration"), raw)


def submit_arbitration(engine: Engine, ticket_id: str, raw) -> dict:
    config = ticket_config(engine, ticket_id)
    graph = ticket_graph(engine)
    with checkpoint.thread_lock(engine, config["configurable"]["thread_id"]):
        decision = check_arbitration(engine, ticket_id, raw)
        logger.info("rfp_approval arbitration ticket_id=%s trigger=%s resolution=%s", ticket_id, decision.trigger,
                    decision.resolution)
        try:
            graph.invoke(Command(resume=decision.model_dump()), config=config)
        except Exception as error:
            _fail(engine, ticket_id, error)
            raise
    return {"trigger": decision.trigger, "resolution": decision.resolution, **status(engine, ticket_id)}


def status(engine: Engine, ticket_id: str) -> dict:
    """Where every thread of the ticket is paused + the approval state of each department."""
    ticket, _metadata, sections = store.approval_input(engine, ticket_id)
    threads = {"ticket": {"thread_id": checkpoint.ticket_thread(ticket_id),
                          "paused_at": paused_at(ticket_graph(engine).get_state(ticket_config(engine, ticket_id)))}}
    for s in sections:
        threads[s.department_id] = {"thread_id": checkpoint.department_thread(ticket_id, s.department_id),
                                    "paused_at": paused_at(_branch_snapshot(engine, ticket_id, s.department_id))}
    return {"ticket_id": ticket_id, "status": ticket.status, "stage": ticket.stage, "error": ticket.error,
            "approval_round": ticket.approval_round, "pending_arbitration": ticket.pending_arbitration,
            "limits": {"max_revision_rounds": approval.max_revision_rounds(),
                       "max_arbitration_rounds": approval.max_arbitration_rounds()},
            "threads": threads,
            "departments": {s.department_id: {"approval_status": s.approval_status, "approver": s.approver,
                                              "revision_rounds": s.revision_rounds,
                                              "confirmed_capacity": s.confirmed_capacity} for s in sections}}
