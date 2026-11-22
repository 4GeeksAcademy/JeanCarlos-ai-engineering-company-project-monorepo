"""End-to-end flow against a real PostgreSQL (upsert, advisory lock and jsonb need Postgres).

Set REPORTING_TEST_DATABASE_URL to a disposable database where the user can create
schemas; every test drops and re-creates `reporting` and `public.telemetry_events`
from the Supabase migrations. Skipped when the variable is not set.
"""

import json
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from data.pipelines import pipeline, run_log
from data.pipelines.connections import REPO_ROOT, engine_for
from data.pipelines.tables import monthly_clinic_supply_performance, pipeline_runs, revisions

TEST_URL = os.environ.get("REPORTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="REPORTING_TEST_DATABASE_URL is not set")

NOVEMBER = date(2026, 11, 1)
MIGRATIONS = [
    "20261121180000_telemetry_events.sql",
    "20261122120000_reporting_monthly_clinic_supply_performance.sql",
]


@pytest.fixture(scope="module", autouse=True)
def prefect_server():
    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness():
        yield


@pytest.fixture
def engine(monkeypatch, tmp_path):
    engine = engine_for(TEST_URL)
    with engine.begin() as connection:
        # DBAPI cursor without parameters: the migrations contain literal '%' (format strings).
        cursor = connection.connection.cursor()
        cursor.execute("drop schema if exists reporting cascade; drop table if exists public.telemetry_events cascade")
        for name in MIGRATIONS:
            cursor.execute((REPO_ROOT / "supabase" / "migrations" / name).read_text())
    monkeypatch.setattr(pipeline, "pipeline_engine", lambda: engine)
    monkeypatch.setattr(pipeline, "inventory_engine", lambda: engine_for(TEST_URL))  # no inventory tables → see below
    monkeypatch.setattr(pipeline, "EVAL_DIR", tmp_path / "eval")
    monkeypatch.setattr(pipeline, "load_config", lambda: dict(pipeline.DEFAULT_CONFIG))
    yield engine
    engine.dispose()


@pytest.fixture
def inventory(engine, monkeypatch):
    """Inventory tables in the same test database, with one movement per source event of clinic 1."""
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "drop table if exists supply_delivery, supply_consumption;"
            "create table supply_delivery (clinic_id int, created_at timestamptz);"
            "create table supply_consumption (clinic_id int, created_at timestamptz);"
            "insert into supply_delivery values (1, '2026-11-03T10:00:00Z');"
            "insert into supply_consumption values (1, '2026-11-05T10:00:00Z'), (1, '2026-11-05T11:00:00Z');"
        )


def add_event(engine, event_type, timestamp, **tags):
    with engine.begin() as connection:
        connection.execute(
            text("insert into public.telemetry_events (id, \"timestamp\", service, event_type, tags) "
                 "values (:id, :ts, 'backoffice', :type, cast(:tags as jsonb))"),
            {"id": str(uuid4()), "ts": timestamp, "type": event_type, "tags": json.dumps(tags)},
        )


@pytest.fixture
def november_events(engine):
    add_event(engine, "inbound_order_created", "2026-11-03T10:00:00Z", clinic_id=1, country="US", product_id=5,
              product_category="ppe", quantity=10, supplier_id=None, total_cost=125.5)
    add_event(engine, "inbound_order_created", "2026-11-04T10:00:00Z", clinic_id=10, country="UK", product_id=6,
              product_category="medication", quantity=4, supplier_id=None, total_cost=80)
    add_event(engine, "outbound_order_created", "2026-11-05T10:00:00Z", clinic_id=1, country="US", product_id=5,
              product_category="ppe", quantity=3, department="emergency")
    add_event(engine, "outbound_order_created", "2026-11-05T11:00:00Z", clinic_id=1, country="US", product_id=5,
              product_category="ppe", quantity=3, department="surgery")
    add_event(engine, "stock_threshold_triggered", "2026-11-05T11:00:00Z", clinic_id=1, country="US", product_id=5,
              product_category="ppe", quantity=3, min_stock_threshold=5, department="surgery")
    add_event(engine, "supply_expiry_flagged", "2026-11-06T09:00:00Z", clinic_id=11, country="UK", product_id=7,
              product_category="medication", quantity=20, expiry_date="2026-12-10", days_until_expiry=34,
              expiry_window_days=60)
    add_event(engine, "page_viewed", "2026-11-06T09:00:00Z", clinic_id=2)  # not a source event


def table_snapshot(engine):
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(
            select(monthly_clinic_supply_performance).order_by("clinic_id", "month_start"))]


def run(**kwargs):
    return pipeline.monthly_clinic_supply_performance_flow(months=[NOVEMBER], trigger="manual", **kwargs)


def test_two_runs_over_the_same_data_leave_the_table_identical(engine, inventory, november_events):
    first = run()
    after_first = table_snapshot(engine)
    second = run()

    assert first["status"] == second["status"] == "completed"
    assert first["rows_changed"] == 12 and second["rows_changed"] == 0
    assert table_snapshot(engine) == after_first  # same ids, values and computed_at
    assert len(after_first) == 12
    by_clinic = {row["clinic_id"]: row for row in after_first}
    tx = by_clinic["us-tx-001"]
    assert (float(tx["total_supply_cost"]), tx["supply_consumption_count"], tx["critical_stockout_count"]) == (125.5, 2, 1)
    assert by_clinic["uk-lon-002"]["expiry_risk_count"] == 1
    assert by_clinic["uk-lon-001"]["currency"] == "GBP"

    with engine.connect() as connection:
        runs = connection.execute(select(pipeline_runs).order_by("started_at")).all()
    assert [r.status for r in runs] == ["completed", "completed"]
    for logged in runs:  # minimum run metadata
        assert logged.started_at and logged.finished_at and logged.finished_at >= logged.started_at
        assert logged.source_events_read == 6 and logged.rows_upserted == 12
        assert logged.error_message is None


def test_late_event_updates_the_month_and_keeps_a_revision(engine, inventory, november_events):
    run()
    add_event(engine, "outbound_order_created", "2026-11-07T10:00:00Z", clinic_id=4, country="US", product_id=5,
              product_category="ppe", quantity=1, department="pharmacy")
    result = run()

    assert result["rows_changed"] == 1
    with engine.connect() as connection:
        revision = connection.execute(select(revisions)).one()
        rows = connection.execute(select(func.count()).select_from(monthly_clinic_supply_performance)).scalar()
    assert rows == 12  # updated, never duplicated
    assert (revision.clinic_id, revision.reason) == ("us-fl-001", "late_events")
    assert revision.previous["supply_consumption_count"] == 0 and revision.current["supply_consumption_count"] == 1


def test_optional_steps_fail_without_stopping_the_run(engine, november_events, monkeypatch, tmp_path):
    def inventory_down():
        raise RuntimeError("inventory unavailable")

    monkeypatch.setattr(pipeline, "inventory_engine", inventory_down)
    (tmp_path / "not-a-dir").write_text("")
    monkeypatch.setattr(pipeline, "EVAL_DIR", tmp_path / "not-a-dir")  # mkdir fails → snapshot fails

    result = run()

    assert result["status"] == "completed"
    assert result["rows_upserted"] == 12
    assert result["warnings"] == ["2026-11-01: inventory_reconciliation_unavailable", "2026-11-01: eval_snapshot_failed"]
    assert len(table_snapshot(engine)) == 12


def test_coverage_against_inventory_is_recorded(engine, inventory, november_events):
    result = run()
    # clinic 1: 1/1 inbound and 2/2 outbound → covered; clinic 10 has telemetry but no inventory movement.
    assert result["warnings"] == ["2026-11-01: coverage_out_of_range:uk-lon-001"]
    report = pipeline.get_monthly_clinic_supply_performance(NOVEMBER, engine)
    assert report["coverage_checked"] is True
    assert report["coverage_warnings"] == ["uk-lon-001"]  # telemetry inbound without an inventory movement
    assert report["revised"] is False
    assert [clinic["clinic_id"] for clinic in report["clinics"]][:1] == ["us-fl-001"]  # US first, then UK
    assert set(report["clinics"][0]) == {"clinic_id", "country", "total_supply_cost", "supply_consumption_count",
                                         "critical_stockout_count", "expiry_risk_count", "currency"}


def test_critical_failure_is_logged_with_its_stage_and_writes_nothing(engine, november_events, monkeypatch):
    def broken(*args):
        raise pipeline.KpiValidationError("2026-11-01: negative KPI values")

    monkeypatch.setattr(pipeline.supply_kpis, "validate_kpis", broken)
    with pytest.raises(Exception):
        run()
    latest = run_log.get_latest_run(engine, pipeline.PIPELINE_NAME)
    assert (latest["status"], latest["error_stage"]) == ("failed", "validate")
    assert "negative KPI values" in latest["error_message"]
    assert latest["finished_at"] is not None
    assert table_snapshot(engine) == []


def test_concurrent_run_is_skipped_while_the_lock_is_held(engine, november_events):
    with engine.connect() as holder:
        holder.execute(select(func.pg_advisory_lock(func.hashtext(pipeline.PIPELINE_NAME))))
        result = run()
        holder.execute(select(func.pg_advisory_unlock(func.hashtext(pipeline.PIPELINE_NAME))))
    assert result["status"] == "skipped_locked"
    assert table_snapshot(engine) == []
    assert run_log.get_latest_run(engine, pipeline.PIPELINE_NAME)["status"] == "skipped_locked"


def test_orphaned_running_rows_are_failed_and_trigger_reports_conflicts(engine, november_events):
    orphan = uuid4()
    run_log.start_run(engine, run_id=orphan, pipeline_name=pipeline.PIPELINE_NAME, trigger="manual",
                      triggered_by=None, months=[NOVEMBER], flow_run_id=None, code_version="test")
    with pytest.raises(pipeline.RunInProgress):
        pipeline.trigger_run([NOVEMBER], triggered_by="x" * 64, engine=engine)

    result = run()

    assert result["warnings"][0] == "orphaned_runs_marked_failed:1"
    with engine.connect() as connection:
        status = connection.execute(select(pipeline_runs.c.status).where(pipeline_runs.c.run_id == str(orphan))).scalar()
    assert status == "failed"


def test_manual_trigger_registers_the_run_before_the_flow_starts(engine, monkeypatch):
    started = []

    class FakeThread:  # the flow itself is covered above; here only the hand-off matters
        def __init__(self, target, kwargs, name, daemon):
            started.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(pipeline.threading, "Thread", FakeThread)
    accepted = pipeline.trigger_run([NOVEMBER], triggered_by="a" * 64, engine=engine)

    assert accepted["mode"] == "in_process"
    assert started[0]["run_id"] == accepted["run_id"] and started[0]["trigger"] == "manual"
    latest = run_log.get_latest_run(engine, pipeline.PIPELINE_NAME)
    assert (latest["run_id"], latest["status"]) == (accepted["run_id"], "running")
    with pytest.raises(pipeline.RunInProgress):  # an immediate second POST → 409
        pipeline.trigger_run(None, triggered_by="a" * 64, engine=engine)
