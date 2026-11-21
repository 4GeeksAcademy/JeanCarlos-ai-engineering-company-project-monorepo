"""Validation against the event contract and bulk persistence into telemetry_events."""

import json
import logging
import os
import re
from collections import Counter
from collections.abc import Iterable
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, Uuid
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url
from sqlmodel import create_engine

from ..schemas.telemetry import TelemetryEvent

logger = logging.getLogger("api.telemetry")

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "telemetry_contract.json"
SCHEMA_VERSION_TAG = "_schema_version"

metadata = MetaData()
# Mirrors supabase/migrations/*_telemetry_events.sql; the migration is the source of truth.
telemetry_events = Table(
    "telemetry_events",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("service", String, nullable=False),
    Column("event_type", String, nullable=False),
    Column("session_id", String),
    Column("user_id", String),
    Column("request_id", String),
    Column("tags", JSON().with_variant(JSONB, "postgresql"), nullable=False),
)


@lru_cache(maxsize=1)
def load_contract() -> dict[str, dict[str, Any]]:
    catalog = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return {event["event_type"]: event for event in catalog["events"]}


@lru_cache(maxsize=1)
def get_telemetry_engine() -> Engine:
    database_url = os.environ.get("TELEMETRY_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TELEMETRY_DATABASE_URL is required to store telemetry")
    url = make_url(database_url)
    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise RuntimeError("TELEMETRY_DATABASE_URL must use PostgreSQL")
    # prepare_threshold=None keeps psycopg compatible with Supabase's transaction pooler.
    return create_engine(
        url.set(drivername="postgresql+psycopg"),
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
    )


def service_name() -> str:
    return os.environ.get("TELEMETRY_SERVICE", "backoffice")


_JSON_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _valid(value: Any, descriptor: dict[str, Any]) -> bool:
    """Same rules the backoffice applies before queueing (telemetry.ts `valid`)."""
    types = descriptor["type"] if isinstance(descriptor["type"], list) else [descriptor["type"]]
    if not any(_JSON_TYPES[kind](value) for kind in types):
        return False
    if "enum" in descriptor and value not in descriptor["enum"]:
        return False
    if "const" in descriptor and value != descriptor["const"]:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value != value or value in (float("inf"), float("-inf")):
            return False
        if "minimum" in descriptor and value < descriptor["minimum"]:
            return False
        if "maximum" in descriptor and value > descriptor["maximum"]:
            return False
    if isinstance(value, str):
        if "pattern" in descriptor and not re.search(descriptor["pattern"], value):
            return False
        if descriptor.get("format") == "date":
            try:
                date.fromisoformat(value)
            except ValueError:
                return False
    if isinstance(value, list) and "items" in descriptor:
        return all(_valid(item, descriptor["items"]) for item in value)
    if isinstance(value, dict) and "properties" in descriptor:
        nested = descriptor["properties"]
        return set(value) <= set(nested) and all(_valid(value[key], nested[key]) for key in value)
    return True


def contract_violation(event: TelemetryEvent) -> str | None:
    """Reason code if the event does not match its event_type contract, else None."""
    schema = load_contract().get(event.event_type)
    if schema is None:
        return "unknown_event_type"
    properties = event.properties
    if not set(properties) <= set(schema["allowlist"]):
        return "property_not_allowlisted"
    for key, descriptor in schema["properties"].items():
        if key not in properties:
            if descriptor.get("required"):
                return "missing_property"
            continue
        if not _valid(properties[key], descriptor):
            return "invalid_property"
    return None


def parse_events(raw_events: Iterable[Any]) -> tuple[list[TelemetryEvent], Counter]:
    """Validate each raw event on its own; invalid ones are counted by reason, never raised."""
    valid: list[TelemetryEvent] = []
    rejected: Counter = Counter()
    for raw in raw_events:
        try:
            event = TelemetryEvent.model_validate(raw)
        except ValidationError:
            rejected["invalid_envelope"] += 1
            continue
        reason = contract_violation(event)
        if reason:
            rejected[reason] += 1
            continue
        valid.append(event)
    return valid, rejected


def to_row(event: TelemetryEvent, service: str) -> dict[str, Any]:
    return {
        "id": event.eventId,
        "timestamp": event.timestamp,
        "service": service,
        "event_type": event.event_type,
        "session_id": event.sessionId,
        "user_id": event.userId,
        "request_id": event.requestId,
        # Properties already match the allowlist; the version tells future readers how to read them.
        "tags": {**event.properties, SCHEMA_VERSION_TAG: event.schemaVersion},
    }


def store_events(engine: Engine, events: list[TelemetryEvent]) -> int:
    """Insert all events in one statement and one transaction; returns rows written now.

    Re-sent events (same eventId after a client retry) are already stored and are
    skipped by ON CONFLICT, never duplicated.
    """
    if not events:
        return 0
    rows = [to_row(event, service_name()) for event in events]
    dialect = postgresql if engine.dialect.name == "postgresql" else sqlite
    statement = dialect.insert(telemetry_events).values(rows).on_conflict_do_nothing(
        index_elements=["id"]
    )
    with engine.begin() as connection:
        result = connection.execute(statement)
    return result.rowcount if result.rowcount is not None and result.rowcount >= 0 else len(rows)
