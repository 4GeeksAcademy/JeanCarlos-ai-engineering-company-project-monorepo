import hashlib
import hmac
import logging
import os
from typing import Annotated

from fastapi import APIRouter, Body

from ..core.dependencies import CurrentUser
from ..core.security import get_settings
from ..schemas.telemetry import TelemetryBatch, TelemetryReceipt, TelemetrySession

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


@router.post("/events", response_model=TelemetryReceipt)
def receive_events(
    batch: Annotated[TelemetryBatch, Body()],
) -> TelemetryReceipt:
    logger.info(
        "Received telemetry batch count=%d types=%s",
        len(batch.events),
        ",".join(event.event_type for event in batch.events),
    )
    return TelemetryReceipt(received=len(batch.events))
