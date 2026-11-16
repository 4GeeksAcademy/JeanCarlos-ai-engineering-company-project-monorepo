"""Centralized incident manager: /api/incidents (authenticated)."""
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from packages.shared.incidents import (
    BRANCHES, CATEGORIES, ORIGINS, STATUSES, FieldError, transition_error, validate_filter,
    validate_incident,
)

from ..core.dependencies import CurrentUser
from ..database import get_db
from ..models.incident import Incident
from ..schemas.incidents import IncidentResponse, IncidentSummary, IncidentValidationError

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
IncidentId = Annotated[int, Path(gt=0)]
VALIDATION_RESPONSE = {400: {"model": IncidentValidationError, "description": "Invalid field"}}

_ready_engines: set[int] = set()


class IncidentInputError(Exception):
    """Rendered by main.py as 400 {detail, errors: [{field, message}]}."""

    def __init__(self, errors: list[FieldError], detail: str = "Some fields are missing or invalid."):
        super().__init__(detail)
        self.detail = detail
        self.errors = errors


def get_incident_db(session: Annotated[Session, Depends(get_db)]) -> Iterator[Session]:
    # Create the table on first use so read endpoints work on an empty database.
    engine: Engine = session.get_bind()
    if id(engine) not in _ready_engines:
        Incident.__table__.create(engine, checkfirst=True)
        _ready_engines.add(id(engine))
    yield session


Database = Annotated[Session, Depends(get_incident_db)]


def to_response(incident: Incident) -> IncidentResponse:
    return IncidentResponse.model_validate(incident, from_attributes=True)


def incident_or_404(session: Session, incident_id: int, *, lock: bool = False) -> Incident:
    statement = select(Incident).where(Incident.id == incident_id)
    if lock:
        statement = statement.with_for_update()
    incident = session.exec(statement).first()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.post(
    "", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED,
    responses=VALIDATION_RESPONSE,
)
def create_incident(payload: Annotated[Any, Body()], user: CurrentUser, session: Database):
    values, errors = validate_incident(payload)
    if errors:
        raise IncidentInputError(errors)
    now = datetime.now(timezone.utc)
    incident = Incident(**values, created_at=now, updated_at=now, user_uuid=user.id)
    session.add(incident)
    session.commit()
    session.refresh(incident)
    return to_response(incident)


@router.get("", response_model=list[IncidentResponse], responses=VALIDATION_RESPONSE)
def list_incidents(
    _user: CurrentUser,
    session: Database,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    origin: str | None = None,
    branch: str | None = None,
    category: str | None = None,
):
    filters = {"status": status_filter, "origin": origin, "branch": branch, "category": category}
    errors = [error for field, value in filters.items() if (error := validate_filter(field, value))]
    if errors:
        raise IncidentInputError(errors, "Some filters are invalid.")
    statement = select(Incident)
    for field, value in filters.items():
        if value is not None:
            statement = statement.where(getattr(Incident, field) == value)
    statement = statement.order_by(Incident.created_at.desc(), Incident.id.desc())
    return [to_response(incident) for incident in session.exec(statement).all()]


def _counts(session: Session, column, keys: tuple[str, ...]) -> dict[str, int]:
    counts = dict.fromkeys(keys, 0)
    for value, total in session.exec(select(column, func.count()).group_by(column)).all():
        counts[value] = total
    return counts


@router.get("/summary", response_model=IncidentSummary)
def summary(_user: CurrentUser, session: Database):
    by_status = _counts(session, Incident.status, STATUSES)
    return IncidentSummary(
        total=sum(by_status.values()),
        by_status=by_status,
        by_category=_counts(session, Incident.category, CATEGORIES),
        by_origin=_counts(session, Incident.origin, ORIGINS),
        by_branch=_counts(session, Incident.branch, BRANCHES),
    )


@router.get("/{incident_id}", response_model=IncidentResponse)
def get_incident(incident_id: IncidentId, _user: CurrentUser, session: Database):
    return to_response(incident_or_404(session, incident_id))


@router.patch(
    "/{incident_id}/status", response_model=IncidentResponse, responses=VALIDATION_RESPONSE,
)
def update_status(
    incident_id: IncidentId,
    payload: Annotated[Any, Body()],
    _user: CurrentUser,
    session: Database,
):
    if not isinstance(payload, dict) or set(payload) != {"status"}:
        raise IncidentInputError(
            [FieldError("status", "Send only the new status, for example {\"status\": \"in_progress\"}.")]
        )
    incident = incident_or_404(session, incident_id, lock=True)
    if error := transition_error(incident.status, payload["status"]):
        raise IncidentInputError([error], "This status change is not allowed.")
    incident.status = payload["status"]
    incident.updated_at = datetime.now(timezone.utc)
    session.add(incident)
    session.commit()
    session.refresh(incident)
    return to_response(incident)
