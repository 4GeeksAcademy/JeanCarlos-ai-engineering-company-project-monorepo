from datetime import datetime

from pydantic import BaseModel


class IncidentResponse(BaseModel):
    id: int
    title: str
    description: str
    category: str
    status: str
    origin: str
    branch: str
    created_at: datetime
    updated_at: datetime


class IncidentSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_origin: dict[str, int]
    by_branch: dict[str, int]


class FieldErrorItem(BaseModel):
    field: str
    message: str


class IncidentValidationError(BaseModel):
    detail: str
    errors: list[FieldErrorItem]
