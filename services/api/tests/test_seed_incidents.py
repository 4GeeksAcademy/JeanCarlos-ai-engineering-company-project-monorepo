"""The legacy CSV seed must reproduce the BRIEFING totals and be idempotent."""
import csv
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

import scripts.seed_incidents as seed_module
from packages.shared.incidents import csv_row_errors, csv_row_to_incident
from services.api.models.incident import Incident

ROOT = Path(__file__).resolve().parents[3]
CSV_PATH = ROOT / "data" / "raw" / "incidents-healthcore.csv"

EXPECTED_STATUS = {"open": 28, "resolved": 52, "discarded": 14}
EXPECTED_CATEGORY = {"patient_experience": 61, "billing_error": 20, "other": 13}
EXPECTED_BRANCH = {
    "manchester_central": 15, "atlanta_midtown": 12, "savannah": 10, "austin_north": 9,
    "london_west": 9, "london_city": 9, "miami_brickell": 8, "tampa_bay": 7, "central": 7,
    "houston_med_center": 4, "orlando_east": 4,
}


@pytest.fixture
def engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


def test_seed_loads_94_customer_incidents_matching_the_briefing(engine):
    report = seed_module.seed_incidents(CSV_PATH, engine)
    assert (report.total_rows, report.inserted, report.already_present, len(report.invalid)) == (100, 94, 0, 6)
    with Session(engine) as session:
        incidents = session.exec(select(Incident)).all()
    assert len(incidents) == 94
    assert {incident.origin for incident in incidents} == {"customer"}
    assert Counter(i.status for i in incidents) == EXPECTED_STATUS
    assert Counter(i.category for i in incidents) == EXPECTED_CATEGORY
    assert Counter(i.branch for i in incidents) == EXPECTED_BRANCH


def test_seed_is_idempotent(engine):
    seed_module.seed_incidents(CSV_PATH, engine)
    second = seed_module.seed_incidents(CSV_PATH, engine)
    assert (second.inserted, second.already_present, len(second.invalid)) == (0, 94, 6)
    with Session(engine) as session:
        assert len(session.exec(select(Incident)).all()) == 94


def test_invalid_rows_are_reported_by_rule_without_patient_ids(engine, capsys):
    report = seed_module.seed_incidents(CSV_PATH, engine)
    seed_module.print_report(report, CSV_PATH)
    output = capsys.readouterr().out
    rules = Counter(rule for _, _, rules in report.invalid for rule in rules)
    assert rules == {
        "invalid_clinic_id": 1, "country_mismatch": 1, "invalid_category": 1,
        "empty_description": 1, "missing_patient_id": 1, "closed_without_score": 1,
    }
    assert "Invalid (skipped) .... 6" in output
    assert "PAT-" not in output


def test_transformation_matches_the_briefing_rules():
    row = {
        "incident_id": "HC-1", "date": "2024-01-13", "clinic_id": "US-GA-02", "country": "US",
        "category": "ACCESSIBILITY", "description": "  " + "a" * 130 + "  ", "status": "CLOSED",
        "patient_id": "PAT-123456", "satisfaction_score": "4",
    }
    assert csv_row_errors(row) == []
    values = csv_row_to_incident(row)
    assert values["title"] == "a" * 120
    assert values["description"] == row["description"]
    assert values["created_at"].isoformat() == "2024-01-13T00:00:00+00:00"
    assert values["updated_at"] == values["created_at"]
    assert (values["status"], values["category"], values["branch"], values["origin"]) == (
        "resolved", "patient_experience", "atlanta_midtown", "customer",
    )
    assert "patient_id" not in values and "satisfaction_score" not in values


def test_seed_uses_the_shared_validation_rules():
    with CSV_PATH.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert sum(1 for row in rows if not csv_row_errors(row)) == 94
    assert seed_module.csv_row_errors is csv_row_errors
