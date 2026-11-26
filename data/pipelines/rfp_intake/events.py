"""Real-time notifications of the RFP flow (Sistemas en Tiempo Real, Parte 1).

`rfp_ticket_created` is published once per ticket, at the moment a new ticket enters the flow as
a valid RFP: the classifier accepted the document and the extractor has the client fields, with
the ticket still in `analizando` (the first status of the RFP system). A document the classifier
discards never notifies. The payload carries only ticket fields with the names of the RFP system
(BRIEFING §2/§3):

    {"ticket_id", "rfp_id", "client_name", "client_country", "program_type", "status", "created_at"}

Every event is a row of `rfp_event`; its `id` is the SSE `id:`. That gives:
- replay after a disconnection (Last-Event-ID → the rows after it), across API restarts;
- delivery across processes (the intake can run in an API worker or in the CLI);
- one notification per ticket: (event, ticket_id) is unique, so a reprocess does not notify again.

No model or agent is called here; this is a communication layer.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from . import phi
from .models import RfpEvent, RfpTicket

logger = logging.getLogger("api.rfp_intake")

RFP_TICKET_CREATED = "rfp_ticket_created"
PAYLOAD_FIELDS = ("ticket_id", "rfp_id", "client_name", "client_country", "program_type", "status", "created_at")

_listeners: list[Callable[[], None]] = []
_listeners_lock = threading.Lock()


def add_listener(callback: Callable[[], None]) -> Callable[[], None]:
    """`callback` runs (in the publishing thread) after every new event; returns the remover."""
    with _listeners_lock:
        _listeners.append(callback)

    def remove() -> None:
        with _listeners_lock:
            if callback in _listeners:
                _listeners.remove(callback)

    return remove


def _wake_listeners() -> None:
    with _listeners_lock:
        callbacks = list(_listeners)
    for callback in callbacks:
        try:
            callback()
        except Exception as error:  # a closed event loop never breaks the pipeline
            logger.debug("rfp event listener failed: %s", type(error).__name__)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ticket_created_payload(ticket: RfpTicket, metadata: dict) -> dict:
    """Only ticket fields; any value that matches the PHI rules is withheld (fail closed)."""
    payload = {
        "ticket_id": ticket.ticket_id,
        "rfp_id": ticket.rfp_id,
        "client_name": metadata.get("client_name"),
        "client_country": metadata.get("client_country"),
        "program_type": metadata.get("program_type") or (metadata.get("program_types") or [None])[0],
        "status": ticket.status,
        "created_at": _iso(ticket.created_at),
    }
    for key, value in payload.items():
        if isinstance(value, str) and phi.find_phi(value):
            payload[key] = None
    return payload


def publish_ticket_created(engine: Engine, ticket_id: str, metadata: dict) -> int | None:
    """Store the event (once per ticket) and wake the SSE hubs. Returns the event id, or None if
    the ticket had already been notified."""
    with Session(engine) as session:
        ticket = session.get(RfpTicket, ticket_id)
        if ticket is None:
            return None
        row = RfpEvent(event=RFP_TICKET_CREATED, ticket_id=ticket_id, payload=ticket_created_payload(ticket, metadata))
        session.add(row)
        try:
            session.commit()
        except IntegrityError:  # already notified (a reprocess of the same ticket)
            session.rollback()
            return None
        session.refresh(row)
        event_id = row.id
    logger.info("rfp_event %s id=%s ticket_id=%s", RFP_TICKET_CREATED, event_id, ticket_id)
    _wake_listeners()
    return event_id


def latest_id(engine: Engine) -> int:
    with Session(engine) as session:
        return session.exec(select(func.max(RfpEvent.id))).one() or 0


def events_after(engine: Engine, last_id: int, limit: int = 200) -> list[dict]:
    with Session(engine) as session:
        rows = session.exec(select(RfpEvent).where(RfpEvent.id > last_id).order_by(RfpEvent.id).limit(limit)).all()
        return [{"id": r.id, "event": r.event, "data": r.payload, "created_at": r.created_at} for r in rows]


def oldest_id(engine: Engine) -> int:
    with Session(engine) as session:
        return session.exec(select(func.min(RfpEvent.id))).one() or 0
