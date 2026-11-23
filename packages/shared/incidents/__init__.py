"""Shared HealthCore incident rules: catalog, validation and CSV transformation.

Used by services/api (incidents endpoints) and scripts/seed_incidents.py.
"""
from .catalog import (
    BRANCH_LABELS, BRANCHES, CATEGORIES, DESCRIPTION_MAX_LENGTH, DESCRIPTION_MIN_LENGTH,
    FINAL_STATUSES, ORIGINS, STATUSES, TITLE_MAX_LENGTH, TRANSITIONS,
)
from .transform import csv_row_to_incident, legacy_ref
from .validation import (
    CSV_RULES, FieldError, csv_row_errors, transition_error, validate_filter, validate_incident,
)

__all__ = [
    "BRANCHES", "BRANCH_LABELS", "CATEGORIES", "CSV_RULES", "DESCRIPTION_MAX_LENGTH",
    "DESCRIPTION_MIN_LENGTH", "FINAL_STATUSES", "FieldError", "ORIGINS", "STATUSES",
    "TITLE_MAX_LENGTH", "TRANSITIONS", "csv_row_errors", "csv_row_to_incident", "legacy_ref",
    "transition_error", "validate_filter", "validate_incident",
]
