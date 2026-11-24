"""HealthCore incident catalog (BRIEFING values), loaded from catalog.json.

catalog.json is the single source of truth for Python (API + seed script) and,
through scripts/generate-incident-catalog.mjs, for the backoffice UI.
"""
import json
from pathlib import Path

_DATA = json.loads((Path(__file__).with_name("catalog.json")).read_text(encoding="utf-8"))


def _values(key: str) -> tuple[str, ...]:
    return tuple(item["value"] for item in _DATA[key])


BRANCHES: tuple[str, ...] = _values("branches")
CATEGORIES: tuple[str, ...] = _values("categories")
STATUSES: tuple[str, ...] = _values("statuses")
ORIGINS: tuple[str, ...] = _values("origins")
BRANCH_LABELS: dict[str, str] = {item["value"]: item["label"] for item in _DATA["branches"]}
TRANSITIONS: dict[str, tuple[str, ...]] = {
    status: tuple(targets) for status, targets in _DATA["transitions"].items()
}
FINAL_STATUSES: tuple[str, ...] = tuple(s for s, targets in TRANSITIONS.items() if not targets)

TITLE_MAX_LENGTH: int = _DATA["limits"]["title_max_length"]
DESCRIPTION_MIN_LENGTH: int = _DATA["limits"]["description_min_length"]
DESCRIPTION_MAX_LENGTH: int = _DATA["limits"]["description_max_length"]

_LEGACY = _DATA["legacy_csv"]
LEGACY_CLINIC_COUNTRIES: dict[str, str] = dict(_LEGACY["clinics"])
LEGACY_STATUS_MAP: dict[str, str] = dict(_LEGACY["status_map"])
LEGACY_CATEGORY_MAP: dict[str, str] = dict(_LEGACY["category_map"])
LEGACY_BRANCH_MAP: dict[str, str] = dict(_LEGACY["branch_map"])
LEGACY_DEFAULT_BRANCH: str = _LEGACY["default_branch"]
