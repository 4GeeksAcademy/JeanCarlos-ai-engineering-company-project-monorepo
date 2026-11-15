from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class TelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: UUID
    timestamp: datetime
    sessionId: Annotated[str, Field(max_length=64)] | None
    userId: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None
    event_type: str = Field(pattern=r"^[a-z]+_[a-z_]+$", max_length=80)
    schemaVersion: Literal["1.0.0"]
    requestId: Annotated[str, Field(max_length=64)] | None
    properties: dict[str, JsonValue] = Field(max_length=20)

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("A timezone is required")
        return value


class TelemetryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[TelemetryEvent] = Field(min_length=1, max_length=20)


class TelemetryReceipt(BaseModel):
    received: int


class TelemetrySession(BaseModel):
    userId: str
    role: Literal["admin", "manager", "user"]
