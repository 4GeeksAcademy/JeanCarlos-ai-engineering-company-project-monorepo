"""Incident table (PostgreSQL via SQLModel). Allowed values come from the shared catalog."""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Index, Text
from sqlmodel import Field, SQLModel

from packages.shared.incidents import (
    BRANCHES, CATEGORIES, DESCRIPTION_MAX_LENGTH, ORIGINS, STATUSES, TITLE_MAX_LENGTH,
)


def _one_of(column: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"incident_{column}_check")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Incident(SQLModel, table=True):
    __tablename__ = "incident"
    __table_args__ = (
        _one_of("status", STATUSES),
        _one_of("origin", ORIGINS),
        _one_of("category", CATEGORIES),
        _one_of("branch", BRANCHES),
        CheckConstraint(
            f"length(trim(title)) BETWEEN 1 AND {TITLE_MAX_LENGTH}", name="incident_title_check"
        ),
        CheckConstraint(
            f"length(trim(description)) BETWEEN 1 AND {DESCRIPTION_MAX_LENGTH}",
            name="incident_description_check",
        ),
        # Compliance (compliance_breach) and executive views filter by category/status.
        Index("ix_incident_category_status", "category", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=TITLE_MAX_LENGTH)
    description: str = Field(sa_type=Text)
    category: str = Field(index=True)
    status: str = Field(default="open", index=True)
    origin: str = Field(index=True)
    branch: str = Field(index=True)
    created_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))
    # TinyDB id of the reporting user (null for the historical seed).
    user_uuid: str | None = Field(default=None)
    # incident_id of the legacy CSV, only for seed idempotency (never exposed by the API).
    legacy_ref: str | None = Field(default=None, unique=True)
