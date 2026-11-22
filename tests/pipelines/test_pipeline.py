"""Unit tests of the transformation tasks of monthly_clinic_supply_performance.

Every test calls a Prefect task's function (`task.fn`) on in-memory data shaped like the
rows the pipeline reads from public.telemetry_events: id, timestamp, event_type and tags,
where tags carries the properties the telemetry BRIEFING defines for the four supply events.
No database, no Prefect server, no external API.

    python -m pytest tests/pipelines/test_pipeline.py
"""

import json
from datetime import date
from uuid import uuid4

import pandas as pd
import pytest

from data.pipelines.connections import REPO_ROOT
from data.pipelines.pipeline import (
    add_months,
    assemble_monthly_clinic_kpis,
    compute_critical_stockout_count,
    compute_expiry_risk_count,
    compute_supply_consumption_count,
    compute_total_supply_cost,
    normalize_supply_events,
    resolve_months,
    transform_cache_key,
    validate_kpis,
)
from data.process import supply_kpis
from data.process.supply_kpis import KpiInputError, KpiValidationError

NOVEMBER = date(2026, 11, 1)
OCTOBER = date(2026, 10, 1)


# --- BRIEFING-shaped telemetry fixtures -----------------------------------------------------

def telemetry_row(event_type, timestamp, **properties):
    """A row of telemetry_events: tags = allowlisted properties + _schema_version."""
    return {"id": str(uuid4()), "timestamp": timestamp, "event_type": event_type,
            "tags": {**properties, "_schema_version": "2.1.0"}}


def inbound_order_created(timestamp, clinic_id, product_id, quantity, total_cost=None, country="US"):
    properties = {"clinic_id": clinic_id, "country": country, "product_id": product_id,
                  "product_category": "medication", "quantity": quantity, "supplier_id": None}
    if total_cost is not None:
        properties["total_cost"] = total_cost
    return telemetry_row(supply_kpis.INBOUND, timestamp, **properties)


def outbound_order_created(timestamp, clinic_id, product_id, quantity, department="emergency", country="US"):
    return telemetry_row(supply_kpis.OUTBOUND, timestamp, clinic_id=clinic_id, country=country,
                         product_id=product_id, product_category="ppe", quantity=quantity, department=department)


def stock_threshold_triggered(timestamp, clinic_id, product_id, quantity, minimum, country="US"):
    return telemetry_row(supply_kpis.STOCKOUT, timestamp, clinic_id=clinic_id, country=country,
                         product_id=product_id, product_category="ppe", quantity=quantity,
                         min_stock_threshold=minimum, department="surgery")


def supply_expiry_flagged(timestamp, clinic_id, product_id, expiry_date, quantity=20, country="UK"):
    return telemetry_row(supply_kpis.EXPIRY, timestamp, clinic_id=clinic_id, country=country,
                         product_id=product_id, product_category="medication", quantity=quantity,
                         expiry_date=expiry_date, days_until_expiry=30, expiry_window_days=60)


def telemetry_frame(*rows):
    return pd.DataFrame(list(rows), columns=["id", "timestamp", "event_type", "tags"])


@pytest.fixture
def clinics():
    return supply_kpis.load_clinic_registry(REPO_ROOT / "data" / "raw" / "clinics.csv")


@pytest.fixture
def november_month(clinics):
    """A month calculated by hand below (test_month_matches_the_briefing_definitions)."""
    rows = telemetry_frame(
        # us-tx-001 (clinic 1, USD): 2 purchases, 3 consumptions, 1 stockout
        inbound_order_created("2026-11-02T09:00:00Z", 1, product_id=5, quantity=10, total_cost=124.00),
        inbound_order_created("2026-11-09T09:00:00Z", 1, product_id=6, quantity=3, total_cost=21.45),
        outbound_order_created("2026-11-03T10:00:00Z", 1, product_id=5, quantity=4, department="emergency"),
        outbound_order_created("2026-11-04T10:00:00Z", 1, product_id=5, quantity=5, department="surgery"),
        outbound_order_created("2026-11-04T11:00:00Z", 1, product_id=6, quantity=2, department="surgery"),
        stock_threshold_triggered("2026-11-04T10:00:01Z", 1, product_id=5, quantity=5, minimum=3),
        # uk-lon-001 (clinic 10, GBP): 1 purchase, 1 consumption
        inbound_order_created("2026-11-05T09:00:00Z", 10, product_id=7, quantity=2, total_cost=60.00, country="UK"),
        outbound_order_created("2026-11-06T09:00:00Z", 10, product_id=7, quantity=1, department="pharmacy", country="UK"),
        # uk-lon-002 (clinic 11): lot (7, 2026-12-10) flagged from two browsers + another lot
        supply_expiry_flagged("2026-11-06T09:00:00Z", 11, product_id=7, expiry_date="2026-12-10"),
        supply_expiry_flagged("2026-11-06T09:30:00Z", 11, product_id=7, expiry_date="2026-12-10"),
        supply_expiry_flagged("2026-11-07T09:30:00Z", 11, product_id=8, expiry_date="2027-01-15"),
        # October: never counted in November
        outbound_order_created("2026-10-31T23:59:59Z", 1, product_id=5, quantity=1),
    )
    return normalize_supply_events.fn(rows, clinics, NOVEMBER)["events"]


def by_clinic(series):
    return series.to_dict()


# --- normalize_supply_events -----------------------------------------------------------------

def test_normalize_supply_events_dedupes_and_counts_skip_reasons(clinics):
    retried = outbound_order_created("2026-11-05T10:00:00Z", 3, product_id=1, quantity=1)
    rows = telemetry_frame(
        retried,
        retried,  # same eventId delivered twice (browser retry)
        inbound_order_created("2026-11-03T10:00:00Z", 4, product_id=5, quantity=10),  # 2.0.0: no cost
        inbound_order_created("2026-11-03T10:00:00Z", 10, product_id=5, quantity=1, total_cost=5, country="US"),
        outbound_order_created("2026-11-05T10:00:00Z", 99, product_id=1, quantity=1),  # not one of the 12 clinics
    )
    result = normalize_supply_events.fn(rows, clinics, NOVEMBER)

    assert result["events_skipped"] == {"duplicate_event_id": 1, "invalid_tags": 0, "unknown_clinic": 1,
                                        "inbound_without_cost": 1, "country_mismatch": 1}
    assert result["source_event_counts"] == {supply_kpis.INBOUND: 2, supply_kpis.OUTBOUND: 1,
                                             supply_kpis.STOCKOUT: 0, supply_kpis.EXPIRY: 0}
    events = result["events"]
    assert len(events) == 3
    # the clinic registry wins over the product country carried by the event
    assert events.loc[events["clinic_id"] == 10, ["country", "currency"]].values.tolist() == [["UK", "GBP"]]


def test_normalize_supply_events_rejects_malformed_tags_without_failing(clinics):
    rows = telemetry_frame(
        {"id": str(uuid4()), "timestamp": "2026-11-05T10:00:00Z", "event_type": supply_kpis.OUTBOUND, "tags": "not json"},
        {"id": str(uuid4()), "timestamp": "2026-11-05T10:00:00Z", "event_type": supply_kpis.OUTBOUND, "tags": None},
        {"id": str(uuid4()), "timestamp": "2026-11-05T10:00:00Z", "event_type": supply_kpis.OUTBOUND, "tags": [1, 2]},
        telemetry_row(supply_kpis.OUTBOUND, "2026-11-05T10:00:00Z", clinic_id=None, product_id=1, quantity=1),
        telemetry_row(supply_kpis.OUTBOUND, "2026-11-05T10:00:00Z", clinic_id="abc", product_id=1, quantity=1),
        # jsonb returned as text by some drivers is still parsed
        {"id": str(uuid4()), "timestamp": "2026-11-05T10:00:00Z", "event_type": supply_kpis.OUTBOUND,
         "tags": json.dumps({"clinic_id": 2, "product_id": 1, "quantity": 1})},
    )
    result = normalize_supply_events.fn(rows, clinics, NOVEMBER)

    assert result["events_skipped"]["invalid_tags"] == 5
    assert result["events"]["clinic_id"].tolist() == [2]


def test_months_are_utc_calendar_months(clinics):
    rows = telemetry_frame(
        outbound_order_created("2026-10-31T23:59:59Z", 2, product_id=1, quantity=1),
        outbound_order_created("2026-11-01T00:00:00Z", 2, product_id=1, quantity=1),
        # 00:30 on 1 November in UTC+02:00 is still 31 October in UTC
        outbound_order_created("2026-11-01T00:30:00+02:00", 2, product_id=1, quantity=1),
    )
    events = normalize_supply_events.fn(rows, clinics, NOVEMBER)["events"]
    assert by_clinic(compute_supply_consumption_count.fn(events, NOVEMBER)) == {2: 1}
    assert by_clinic(compute_supply_consumption_count.fn(events, OCTOBER)) == {2: 2}


# --- one task per BRIEFING KPI ---------------------------------------------------------------

def test_compute_total_supply_cost_sums_total_cost_per_clinic(november_month):
    """Costo de insumos por clínica = sum of inbound_order_created.total_cost (not of quantity)."""
    assert by_clinic(compute_total_supply_cost.fn(november_month, NOVEMBER)) == {1: 145.45, 10: 60.0}


def test_compute_supply_consumption_count_counts_events_not_units(november_month):
    """Volumen de consumo de insumos = number of outbound_order_created events (11 units, 3 events)."""
    assert by_clinic(compute_supply_consumption_count.fn(november_month, NOVEMBER)) == {1: 3, 10: 1}


def test_compute_critical_stockout_count_counts_threshold_crossings(november_month):
    """Frecuencia de quiebre crítico = number of stock_threshold_triggered events."""
    assert by_clinic(compute_critical_stockout_count.fn(november_month, NOVEMBER)) == {1: 1}


def test_compute_expiry_risk_count_counts_distinct_lots(november_month):
    """Conteo de riesgo de vencimiento = lots (product, expiry date): 3 flags, 2 lots."""
    assert by_clinic(compute_expiry_risk_count.fn(november_month, NOVEMBER)) == {11: 2}


def test_month_matches_the_briefing_definitions(november_month, clinics):
    """Full month calculated by hand → the rows written to reporting.monthly_clinic_supply_performance."""
    kpis = assemble_monthly_clinic_kpis.fn(
        clinics, NOVEMBER,
        compute_total_supply_cost.fn(november_month, NOVEMBER),
        compute_supply_consumption_count.fn(november_month, NOVEMBER),
        compute_critical_stockout_count.fn(november_month, NOVEMBER),
        compute_expiry_risk_count.fn(november_month, NOVEMBER),
    )
    rows = kpis.set_index("clinic_id").to_dict(orient="index")

    assert list(kpis.columns) == ["clinic_id", "country", "month_start", "total_supply_cost",
                                  "supply_consumption_count", "critical_stockout_count",
                                  "expiry_risk_count", "currency"]
    assert len(kpis) == 12  # always one row per clinic, zeros when there is no activity
    assert rows["us-tx-001"] == {"country": "US", "month_start": NOVEMBER, "total_supply_cost": 145.45,
                                 "supply_consumption_count": 3, "critical_stockout_count": 1,
                                 "expiry_risk_count": 0, "currency": "USD"}
    assert rows["uk-lon-001"] == {"country": "UK", "month_start": NOVEMBER, "total_supply_cost": 60.0,
                                  "supply_consumption_count": 1, "critical_stockout_count": 0,
                                  "expiry_risk_count": 0, "currency": "GBP"}
    assert rows["uk-lon-002"]["expiry_risk_count"] == 2
    assert rows["us-ga-001"] == {"country": "US", "month_start": NOVEMBER, "total_supply_cost": 0.0,
                                 "supply_consumption_count": 0, "critical_stockout_count": 0,
                                 "expiry_risk_count": 0, "currency": "USD"}
    # USD and GBP side by side, never summed into one row (BRIEFING §7)
    assert set(kpis.groupby("country")["currency"].unique().map(tuple)) == {("USD",), ("GBP",)}
    validate_kpis.fn(kpis, clinics, NOVEMBER, november_month, None, 0.05)


# --- defensive behaviour -----------------------------------------------------------------------

@pytest.mark.parametrize("bad_cost", [None, "12.50€", -40.0, float("nan")])
def test_compute_total_supply_cost_never_invents_or_subtracts_cost(clinics, bad_cost):
    """A null, non-numeric, negative or NaN total_cost adds nothing; it never crashes the month."""
    rows = telemetry_frame(
        inbound_order_created("2026-11-02T09:00:00Z", 1, product_id=5, quantity=10, total_cost=100.0),
        telemetry_row(supply_kpis.INBOUND, "2026-11-03T09:00:00Z", clinic_id=1, country="US", product_id=5,
                      product_category="medication", quantity=1, supplier_id=None, total_cost=bad_cost),
        telemetry_row(supply_kpis.INBOUND, "2026-11-03T09:00:00Z", clinic_id=2, country="US", product_id=5,
                      product_category="medication", quantity=1, supplier_id=None, total_cost=bad_cost),
    )
    events = normalize_supply_events.fn(rows, clinics, NOVEMBER)["events"]
    assert by_clinic(compute_total_supply_cost.fn(events, NOVEMBER)) == {1: 100.0}  # clinic 2: no cost, not 0


@pytest.mark.parametrize("task", [compute_total_supply_cost, compute_supply_consumption_count,
                                  compute_critical_stockout_count, compute_expiry_risk_count])
def test_kpi_tasks_reject_malformed_input(task, november_month):
    with pytest.raises(KpiInputError, match="missing columns"):
        task.fn(november_month.drop(columns=["clinic_id"]), NOVEMBER)
    with pytest.raises(KpiInputError, match="first day of a month"):
        task.fn(november_month, date(2026, 11, 15))
    with pytest.raises(KpiInputError, match="DataFrame"):
        task.fn([{"event_type": supply_kpis.OUTBOUND}], NOVEMBER)


def test_kpi_tasks_skip_rows_with_null_clinic(november_month):
    broken = pd.concat([november_month, november_month.head(1).assign(clinic_id=None)], ignore_index=True)
    assert by_clinic(compute_total_supply_cost.fn(broken, NOVEMBER)) == {1: 145.45, 10: 60.0}


def test_empty_month_gives_twelve_zero_rows(clinics):
    events = normalize_supply_events.fn(telemetry_frame(), clinics, NOVEMBER)["events"]
    kpis = assemble_monthly_clinic_kpis.fn(
        clinics, NOVEMBER,
        compute_total_supply_cost.fn(events, NOVEMBER),
        compute_supply_consumption_count.fn(events, NOVEMBER),
        compute_critical_stockout_count.fn(events, NOVEMBER),
        compute_expiry_risk_count.fn(events, NOVEMBER),
    )
    assert len(kpis) == 12
    assert (kpis[list(supply_kpis.KPI_COLUMNS)] == 0).all().all()


@pytest.mark.parametrize("breakage, message", [
    (lambda df: df.iloc[1:], "one row for each"),
    (lambda df: pd.concat([df.iloc[:-1], df.head(1)]), "one row for each"),  # 12 rows, one clinic twice
    (lambda df: df.assign(expiry_risk_count=df["expiry_risk_count"] - 1), "negative"),
    (lambda df: df.assign(currency="USD"), "currency"),
    (lambda df: df.assign(month_start=OCTOBER), "another month"),
    (lambda df: df.assign(patient_id="x"), "patient"),
])
def test_validate_kpis_blocks_inconsistent_months(november_month, clinics, breakage, message):
    kpis = supply_kpis.aggregate_monthly_kpis(november_month, clinics, NOVEMBER)
    with pytest.raises(KpiValidationError, match=message):
        validate_kpis.fn(breakage(kpis), clinics, NOVEMBER, november_month, None, 0.05)


# --- coverage, department detail, cache key, months -------------------------------------------

def test_validate_kpis_reports_coverage_against_inventory(november_month, clinics):
    inventory = pd.DataFrame({"clinic_id": [1, 2, 10], "inbound": [2, 3, 1], "outbound": [3, 0, 1]})
    kpis = supply_kpis.aggregate_monthly_kpis(november_month, clinics, NOVEMBER)
    coverage = validate_kpis.fn(kpis, clinics, NOVEMBER, november_month, inventory, 0.05)

    assert coverage["warnings"] == ["us-tx-002"]  # 0 of 3 inbound movements reached telemetry
    assert coverage["clinics_without_events"] == ["us-tx-002"]
    assert coverage["ready_for_board"] is False


def test_consumption_by_department_detail(november_month):
    detail = supply_kpis.consumption_by_department(november_month, NOVEMBER)
    assert detail == [
        {"clinic_id": "uk-lon-001", "department": "pharmacy", "supply_consumption_count": 1},
        {"clinic_id": "us-tx-001", "department": "emergency", "supply_consumption_count": 1},
        {"clinic_id": "us-tx-001", "department": "surgery", "supply_consumption_count": 2},
    ]


def test_transform_cache_key_changes_only_with_the_source_events(clinics):
    first = telemetry_frame(outbound_order_created("2026-11-05T10:00:00Z", 1, product_id=1, quantity=1))
    late = pd.concat([first, telemetry_frame(outbound_order_created("2026-11-29T10:00:00Z", 1, product_id=1, quantity=1))])

    def key(events, month=NOVEMBER):
        return transform_cache_key(None, {"events": events, "clinics": clinics, "month_start": month})

    assert key(first) == key(first.iloc[::-1])  # same events in another order: cache hit
    assert key(first) != key(late)  # a late event invalidates the cache
    assert key(first) != key(first, OCTOBER)


def test_scheduled_run_covers_current_and_previous_month():
    assert resolve_months(None, 1, today=date(2027, 1, 15)) == [date(2026, 12, 1), date(2027, 1, 1)]
    assert resolve_months([date(2026, 11, 20), NOVEMBER], 1) == [NOVEMBER]
    with pytest.raises(ValueError):
        resolve_months([add_months(NOVEMBER, -offset) for offset in range(25)], 1)
