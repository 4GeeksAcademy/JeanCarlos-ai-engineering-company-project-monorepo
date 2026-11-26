"""Validation shared by the incidents API and the CSV seed script.

The legacy CSV rules are the ones of the incidents-file-analyzer project
(HealthCore analyzer BRIEFING). Messages never include patient identifiers.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from .catalog import (
    BRANCHES, CATEGORIES, DESCRIPTION_MAX_LENGTH, DESCRIPTION_MIN_LENGTH,
    LEGACY_CATEGORY_MAP, LEGACY_CLINIC_COUNTRIES, LEGACY_STATUS_MAP, ORIGINS, STATUSES,
    TITLE_MAX_LENGTH, TRANSITIONS,
)


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message}


# --- Primitives (used by both the API and the CSV rules) --------------------

def clean_text(value: Any) -> str | None:
    """Return the stripped string, or None when the value is not a string."""
    return value.strip() if isinstance(value, str) else None


def description_error(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return "Description is required."
    if len(text) < DESCRIPTION_MIN_LENGTH:
        return f"Description must be at least {DESCRIPTION_MIN_LENGTH} characters long."
    if len(text) > DESCRIPTION_MAX_LENGTH:
        return f"Description must be at most {DESCRIPTION_MAX_LENGTH} characters long."
    return None


def choice_error(label: str, value: Any, allowed: tuple[str, ...]) -> str | None:
    if value is None or value == "":
        return f"{label} is required."
    if value not in allowed:
        return f"{label} must be one of: {', '.join(allowed)}."
    return None


# --- Incident payloads (API) ------------------------------------------------

INCIDENT_FIELDS = ("title", "description", "category", "status", "origin", "branch")
_CHOICES = {
    "category": ("Category", CATEGORIES),
    "status": ("Status", STATUSES),
    "origin": ("Origin", ORIGINS),
    "branch": ("Branch", BRANCHES),
}


def validate_incident(data: Any) -> tuple[dict[str, str], list[FieldError]]:
    """Validate a new incident. Returns (clean values, errors).

    ``status`` is optional and defaults to ``open``; every other field is required.
    """
    if not isinstance(data, Mapping):
        return {}, [FieldError("body", "The request must be a JSON object with the incident fields.")]
    errors: list[FieldError] = []
    for key in data:
        if key not in INCIDENT_FIELDS:
            errors.append(FieldError(str(key), "This field cannot be set when creating an incident."))

    clean: dict[str, str] = {}
    title = clean_text(data.get("title"))
    if not title:
        errors.append(FieldError("title", "Title is required."))
    elif len(title) > TITLE_MAX_LENGTH:
        errors.append(FieldError("title", f"Title must be at most {TITLE_MAX_LENGTH} characters long."))
    else:
        clean["title"] = title

    if message := description_error(data.get("description")):
        errors.append(FieldError("description", message))
    else:
        clean["description"] = clean_text(data["description"])

    values = dict(data)
    values.setdefault("status", "open")
    for field, (label, allowed) in _CHOICES.items():
        if message := choice_error(label, values.get(field), allowed):
            errors.append(FieldError(field, message))
        else:
            clean[field] = values[field]
    return clean, errors


def validate_filter(field: str, value: str | None) -> FieldError | None:
    if value is None:
        return None
    label, allowed = _CHOICES[field]
    message = choice_error(label, value, allowed)
    return FieldError(field, message) if message else None


def transition_error(current: str, new: Any) -> FieldError | None:
    """Check the lifecycle: open → in_progress|discarded, in_progress → resolved|discarded."""
    if message := choice_error("Status", new, STATUSES):
        return FieldError("status", message)
    allowed = TRANSITIONS.get(current, ())
    if new == current:
        return FieldError("status", f"The incident is already {current}.")
    if not allowed:
        return FieldError("status", f"The incident is {current}, which is a final status and cannot change.")
    if new not in allowed:
        return FieldError(
            "status",
            f"An incident that is {current} can only move to: {', '.join(allowed)}.",
        )
    return None


# --- Legacy CSV rows (incidents-file-analyzer rules) ------------------------

PATIENT_ID = re.compile(r"^PAT-\d{6}$")

# rule code -> human readable label (as reported by the analyzer)
CSV_RULES = {
    "invalid_clinic_id": "Invalid or missing clinic_id",
    "country_mismatch": "Country/clinic mismatch",
    "invalid_category": "Invalid or missing category",
    "empty_description": "Empty description",
    "missing_patient_id": "Missing patient_id",
    "closed_without_score": "Closed case, no score",
    "score_out_of_range": "Satisfaction score out of range",
    # Required by the CSV → model transformation of the incident manager.
    "invalid_status": "Invalid or missing status",
    "invalid_date": "Invalid or missing date",
}


def csv_row_errors(row: Mapping[str, Any]) -> list[str]:
    """Return the rule codes a CSV row violates (empty list when valid).

    The patient_id is checked for presence/format only; its value is never returned.
    """
    def text(key: str) -> str:
        return clean_text(row.get(key)) or ""

    errors: list[str] = []
    clinic = text("clinic_id")
    if clinic not in LEGACY_CLINIC_COUNTRIES:
        errors.append("invalid_clinic_id")
    elif text("country") != LEGACY_CLINIC_COUNTRIES[clinic]:
        errors.append("country_mismatch")
    if text("category") not in LEGACY_CATEGORY_MAP:
        errors.append("invalid_category")
    if description_error(row.get("description")) is not None:
        errors.append("empty_description")
    if not PATIENT_ID.fullmatch(text("patient_id")):
        errors.append("missing_patient_id")
    status = text("status")
    score = text("satisfaction_score")
    if status not in LEGACY_STATUS_MAP:
        errors.append("invalid_status")
    elif status == "CLOSED" and not score:
        errors.append("closed_without_score")
    if score and (not score.isdigit() or not 1 <= int(score) <= 5):
        errors.append("score_out_of_range")
    try:
        date.fromisoformat(text("date"))
    except ValueError:
        errors.append("invalid_date")
    return errors
