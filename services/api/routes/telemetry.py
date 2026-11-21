import hashlib
import hmac
import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from ..core.dependencies import CurrentUser
from ..core.security import get_settings
from ..schemas.telemetry import TelemetryReceipt, TelemetrySession
from ..services.telemetry_store import get_telemetry_engine, parse_events, store_events

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
logger = logging.getLogger("api.telemetry")


def telemetry_endpoint() -> str:
    return os.environ.get("TELEMETRY_ENDPOINT", "http://localhost:8000/telemetry/events")


@router.get("/session", response_model=TelemetrySession)
def session(user: CurrentUser) -> TelemetrySession:
    digest = hmac.new(
        get_settings().secret.encode(),
        f"healthcore.telemetry.v1:{user.id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return TelemetrySession(userId=digest, role=user.role.value)


MAX_BATCH_EVENTS = 20


@router.post("/events", response_model=TelemetryReceipt)
def receive_events(payload: Annotated[Any, Body()]) -> TelemetryReceipt:
    # The envelope is parsed loosely: one invalid event must not reject the batch,
    # so each item is validated on its own instead of typing the body as
    # list[TelemetryEvent] (which would turn any bad event into a 422 for all).
    raw_events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= MAX_BATCH_EVENTS:
        raise HTTPException(status_code=422, detail="Invalid telemetry envelope")

    valid, rejected = parse_events(raw_events)
    try:
        inserted = store_events(get_telemetry_engine(), valid)
    except (RuntimeError, ValueError, SQLAlchemyError) as exc:
        logger.error("Telemetry storage failed: %s", type(exc).__name__)
        # 503 makes the client retry the whole batch; eventId keeps the retry idempotent.
        raise HTTPException(status_code=503, detail="Telemetry storage unavailable") from exc

    receipt = TelemetryReceipt(
        received=len(raw_events), stored=len(valid), rejected=sum(rejected.values())
    )
    logger.info(
        "Telemetry batch received=%d stored=%d (new=%d, duplicates=%d) rejected=%d %s types=%s",
        receipt.received,
        receipt.stored,
        inserted,
        len(valid) - inserted,
        receipt.rejected,
        dict(rejected),
        ",".join(event.event_type for event in valid),
    )
    return receipt
