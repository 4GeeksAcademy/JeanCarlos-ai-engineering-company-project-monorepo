import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from ..core.dependencies import CurrentUser
from ..core.security import get_settings
from ..schemas.telemetry import TelemetryReceipt, TelemetrySession
from ..services.cache import response_cache
from ..services.telemetry_store import (
    get_report_engine,
    get_telemetry_engine,
    parse_events,
    store_events,
)
from services.telemetry.analysis import build_report

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
logger = logging.getLogger("api.telemetry")


def telemetry_endpoint() -> str:
    return os.environ.get("TELEMETRY_ENDPOINT", "http://localhost:8000/telemetry/events")


def user_pseudonym(user_id: str) -> str:
    """HMAC pseudonym of an account id: telemetry userId and pipeline_runs.triggered_by."""
    return hmac.new(
        get_settings().secret.encode(),
        f"healthcore.telemetry.v1:{user_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


@router.get("/session", response_model=TelemetrySession)
def session(user: CurrentUser) -> TelemetrySession:
    return TelemetrySession(userId=user_pseudonym(user.id), role=user.role.value)


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


REPORT_CACHE_NAMESPACE = "telemetry-report"
REPORT_CACHE_TTL_SECONDS = 60
REPORT_DEFAULT_WINDOW = timedelta(days=7)
REPORT_MAX_WINDOW = timedelta(days=93)


def _as_utc(value: datetime) -> datetime:
    # ISO 8601 without offset is read as UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def resolve_period(start_date: datetime | None, end_date: datetime | None) -> tuple[datetime, datetime]:
    """Resolve the [start, end) window once; metric functions never apply their own default."""
    end = _as_utc(end_date) if end_date else datetime.now(timezone.utc)
    start = _as_utc(start_date) if start_date else end - REPORT_DEFAULT_WINDOW
    if start >= end:
        raise HTTPException(status_code=422, detail="start_date must be before end_date")
    if end - start > REPORT_MAX_WINDOW:
        raise HTTPException(status_code=422, detail="The report window cannot exceed 93 days")
    return start, end


@router.get("/report")
def report(
    _user: CurrentUser,
    start_date: Annotated[datetime | None, Query(description="ISO 8601; default end_date - 7 days")] = None,
    end_date: Annotated[datetime | None, Query(description="ISO 8601; default now (UTC)")] = None,
) -> dict[str, Any]:
    # Cache by the requested parameters, so the default "last 7 days" request is
    # also reused for 60 s instead of recomputing with a slightly different now().
    key = (
        _as_utc(start_date).isoformat() if start_date else None,
        _as_utc(end_date).isoformat() if end_date else None,
    )
    hit, cached, version = response_cache.get(REPORT_CACHE_NAMESPACE, key)
    if hit:
        return cached
    start, end = resolve_period(start_date, end_date)
    try:
        result = build_report(get_report_engine(), start, end)
    except (RuntimeError, ValueError, SQLAlchemyError) as exc:
        logger.error("Telemetry report failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Telemetry report unavailable") from exc
    response_cache.set(
        REPORT_CACHE_NAMESPACE, key, result,
        ttl_seconds=REPORT_CACHE_TTL_SECONDS, expected_version=version,
    )
    return result
