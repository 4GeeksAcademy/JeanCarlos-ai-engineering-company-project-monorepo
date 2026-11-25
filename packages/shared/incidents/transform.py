"""Legacy CSV row → Incident model values (HealthCore incident manager BRIEFING)."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from typing import Any

from .catalog import (
    LEGACY_BRANCH_MAP, LEGACY_CATEGORY_MAP, LEGACY_DEFAULT_BRANCH, LEGACY_STATUS_MAP,
    TITLE_MAX_LENGTH,
)


def legacy_ref(row: Mapping[str, Any]) -> str | None:
    """CSV identifier used only for idempotency (incident_id)."""
    value = row.get("incident_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def csv_row_to_incident(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map a *validated* CSV row to Incident fields.

    - title: first 120 characters of description, stripped
    - description: copied literally
    - created_at / updated_at: date parsed as midnight UTC
    - status, category: BRIEFING maps; branch: clinic_id map, ``central`` fallback
    - origin: always ``customer``; patient_id and satisfaction_score are not stored
    """
    description = row["description"]
    created_at = datetime.combine(date.fromisoformat(row["date"].strip()), time.min, tzinfo=timezone.utc)
    clinic_id = (row.get("clinic_id") or "").strip()
    return {
        "title": description.strip()[:TITLE_MAX_LENGTH].strip(),
        "description": description,
        "category": LEGACY_CATEGORY_MAP[row["category"].strip()],
        "status": LEGACY_STATUS_MAP[row["status"].strip()],
        "origin": "customer",
        "branch": LEGACY_BRANCH_MAP.get(clinic_id, LEGACY_DEFAULT_BRANCH),
        "created_at": created_at,
        "updated_at": created_at,
    }
