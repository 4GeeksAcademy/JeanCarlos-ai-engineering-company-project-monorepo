"""Pure transformations (data/process/supply_kpis.py): KPI rules of the BRIEFING, no DB, no Prefect."""

import json
from datetime import date
from uuid import uuid4

import pandas as pd
import pytest

from data.pipelines.connections import REPO_ROOT
from data.pipelines.pipeline import add_months, resolve_months
from data.process import supply_kpis as kpis

NOVEMBER = date(2026, 11, 1)


@pytest.fixture
def registry():
    return kpis.load_clinic_registry(REPO_ROOT / "data" / "raw" / "clinics.csv")


def event(event_type, timestamp, **tags):
    return {"id": str(uuid4()), "timestamp": timestamp, "event_type": event_type, "tags": tags}


def frame(*events):
    return pd.DataFrame(list(events), columns=["id", "timestamp", "event_type", "tags"])


def row(result, clinic_id):
    return result.set_index("clinic_id").loc[clinic_id]


def test_registry_has_the_12_clinics_with_their_currency(registry):
    assert len(registry) == 12
    assert set(registry["clinic_id"]) == set(range(1, 13))
    assert (registry["country"].map(kpis.CURRENCY_BY_COUNTRY) == registry["currency"]).all()


def test_each_kpi_follows_its_briefing_definition(registry):
    source = frame(
        event(kpis.INBOUND, "2026-11-03T10:00:00Z", clinic_id=1, country="US", product_id=5, quantity=10, total_cost=125.5),
        event(kpis.INBOUND, "2026-11-04T10:00:00Z", clinic_id=1, country="US", product_id=6, quantity=1, total_cost=10.25),
        event(kpis.OUTBOUND, "2026-11-05T10:00:00Z", clinic_id=1, product_id=5, quantity=3, department="emergency"),
        event(kpis.OUTBOUND, "2026-11-05T11:00:00Z", clinic_id=1, product_id=5, quantity=30, department="surgery"),
        event(kpis.STOCKOUT, "2026-11-05T11:00:00Z", clinic_id=1, product_id=5, quantity=30, min_stock_threshold=5),
        # Same lot flagged from two browsers → 1 lot; another expiry date → another lot.
        event(kpis.EXPIRY, "2026-11-06T09:00:00Z", clinic_id=11, product_id=7, expiry_date="2026-12-10"),
        event(kpis.EXPIRY, "2026-11-06T09:30:00Z", clinic_id=11, product_id=7, expiry_date="2026-12-10"),
        event(kpis.EXPIRY, "2026-11-06T09:30:00Z", clinic_id=11, product_id=7, expiry_date="2027-01-15"),
    )
    events, _ = kpis.normalize_events(source, registry)
    result = kpis.aggregate_monthly_kpis(events, registry, NOVEMBER)

    assert list(result.columns) == list(kpis.DESTINATION_COLUMNS)
    tx = row(result, "us-tx-001")
    assert tx["total_supply_cost"] == 135.75  # sum of total_cost, not of quantity
    assert tx["supply_consumption_count"] == 2  # count of events, not units
    assert tx["critical_stockout_count"] == 1
    assert (tx["country"], tx["currency"]) == ("US", "USD")
    lon = row(result, "uk-lon-002")
    assert lon["expiry_risk_count"] == 2
    assert (lon["country"], lon["currency"]) == ("UK", "GBP")


def test_always_12_rows_and_clinics_without_events_are_zero(registry):
    events, _ = kpis.normalize_events(frame(), registry)
    result = kpis.aggregate_monthly_kpis(events, registry, NOVEMBER)
    assert len(result) == 12
    assert (result[list(kpis.KPI_COLUMNS)] == 0).all().all()
    kpis.validate_kpis(result, registry, NOVEMBER)


def test_month_boundaries_are_utc_calendar_months(registry):
    source = frame(
        event(kpis.OUTBOUND, "2026-10-31T23:59:59Z", clinic_id=2, product_id=1, quantity=1),
        event(kpis.OUTBOUND, "2026-11-01T00:00:00Z", clinic_id=2, product_id=1, quantity=1),
        # 2026-11-01 00:30 in UTC+02:00 is still October in UTC.
        event(kpis.OUTBOUND, "2026-11-01T00:30:00+02:00", clinic_id=2, product_id=1, quantity=1),
    )
    events, _ = kpis.normalize_events(source, registry)
    assert row(kpis.aggregate_monthly_kpis(events, registry, NOVEMBER), "us-tx-002")["supply_consumption_count"] == 1
    assert row(kpis.aggregate_monthly_kpis(events, registry, date(2026, 10, 1)), "us-tx-002")["supply_consumption_count"] == 2


def test_skip_reasons_are_counted_and_never_invent_cost(registry):
    duplicated = event(kpis.OUTBOUND, "2026-11-05T10:00:00Z", clinic_id=3, product_id=1, quantity=1)
    source = frame(
        duplicated,
        duplicated,
        event(kpis.INBOUND, "2026-11-03T10:00:00Z", clinic_id=4, country="US", product_id=5, quantity=10),  # 2.0.0, no cost
        event(kpis.INBOUND, "2026-11-03T10:00:00Z", clinic_id=10, country="US", product_id=5, quantity=1, total_cost=5),
        event(kpis.OUTBOUND, "2026-11-05T10:00:00Z", clinic_id=99, product_id=1, quantity=1),
        {"id": str(uuid4()), "timestamp": "2026-11-05T10:00:00Z", "event_type": kpis.OUTBOUND, "tags": "not json"},
        {"id": str(uuid4()), "timestamp": "2026-11-05T10:00:00Z", "event_type": kpis.OUTBOUND,
         "tags": json.dumps({"clinic_id": 5, "product_id": 1, "quantity": 1})},  # jsonb as text is accepted
    )
    events, skipped = kpis.normalize_events(source, registry)
    assert skipped == {"duplicate_event_id": 1, "invalid_tags": 1, "unknown_clinic": 1,
                       "inbound_without_cost": 1, "country_mismatch": 1}
    result = kpis.aggregate_monthly_kpis(events, registry, NOVEMBER)
    assert row(result, "us-fl-001")["total_supply_cost"] == 0  # no cost → contributes nothing
    assert row(result, "uk-lon-001")["currency"] == "GBP"  # registry country wins over the event's
    assert row(result, "us-tx-003")["supply_consumption_count"] == 1
    assert row(result, "us-fl-002")["supply_consumption_count"] == 1


@pytest.mark.parametrize("breakage, message", [
    (lambda df: df.iloc[1:], "one row for each"),
    (lambda df: df.assign(expiry_risk_count=df["expiry_risk_count"] - 1), "negative"),
    (lambda df: df.assign(currency="USD"), "currency"),
    (lambda df: df.assign(patient_id="x"), "patient"),
])
def test_validation_blocks_inconsistent_months(registry, breakage, message):
    events, _ = kpis.normalize_events(frame(), registry)
    result = kpis.aggregate_monthly_kpis(events, registry, NOVEMBER)
    with pytest.raises(kpis.KpiValidationError, match=message):
        kpis.validate_kpis(breakage(result), registry, NOVEMBER)


def test_coverage_flags_lost_events_and_capture_gaps(registry):
    source = frame(
        event(kpis.OUTBOUND, "2026-11-05T10:00:00Z", clinic_id=1, product_id=1, quantity=1),
        event(kpis.INBOUND, "2026-11-05T10:00:00Z", clinic_id=1, product_id=1, quantity=1, total_cost=1),
    )
    events, _ = kpis.normalize_events(source, registry)
    inventory = pd.DataFrame({"clinic_id": [1, 2], "inbound": [1, 3], "outbound": [2, 0]})
    report = kpis.coverage_report(events, inventory, registry, NOVEMBER, tolerance=0.05)
    assert report["warnings"] == ["us-tx-001", "us-tx-002"]  # 1/2 outbound; 0/3 inbound
    assert report["clinics_without_events"] == ["us-tx-002"]
    assert report["ready_for_board"] is False


def test_department_breakdown_of_consumption(registry):
    source = frame(
        event(kpis.OUTBOUND, "2026-11-05T10:00:00Z", clinic_id=1, product_id=1, quantity=1, department="emergency"),
        event(kpis.OUTBOUND, "2026-11-05T10:00:00Z", clinic_id=1, product_id=1, quantity=1, department="emergency"),
        event(kpis.OUTBOUND, "2026-11-05T10:00:00Z", clinic_id=1, product_id=1, quantity=1),
    )
    events, _ = kpis.normalize_events(source, registry)
    assert kpis.consumption_by_department(events, NOVEMBER) == [
        {"clinic_id": "us-tx-001", "department": "emergency", "supply_consumption_count": 2},
        {"clinic_id": "us-tx-001", "department": "unknown", "supply_consumption_count": 1},
    ]


def test_scheduled_run_covers_current_and_previous_month():
    assert resolve_months(None, 1, today=date(2027, 1, 15)) == [date(2026, 12, 1), date(2027, 1, 1)]
    assert resolve_months([date(2026, 11, 20), date(2026, 11, 1)], 1) == [NOVEMBER]
    assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    with pytest.raises(ValueError):
        resolve_months([add_months(NOVEMBER, -offset) for offset in range(25)], 1)
