"""services/reporting: HTTP contract, auth and error mapping. The pipeline functions are stubbed."""

from datetime import date

import pytest

from data.pipelines.connections import MissingConnection
from data.pipelines.pipeline import RunInProgress
from services.reporting import router as reporting

REPORT = {
    "month_start": "2026-11-01",
    "clinics": [
        {"clinic_id": "us-tx-001", "country": "US", "total_supply_cost": 18420.5, "supply_consumption_count": 340,
         "critical_stockout_count": 1, "expiry_risk_count": 4, "currency": "USD"},
        {"clinic_id": "uk-lon-001", "country": "UK", "total_supply_cost": 80.0, "supply_consumption_count": 3,
         "critical_stockout_count": 0, "expiry_risk_count": 0, "currency": "GBP"},
    ],
    "consumption_by_department": [
        {"clinic_id": "us-tx-001", "department": "primary_care", "supply_consumption_count": 340},
    ],
    "computed_at": "2026-11-22T05:00:01+00:00",
    "last_run_id": "0e083ef3-900e-48ca-b025-03d1eae63aba",
    "revised": False,
    "coverage_checked": True,
    "coverage_warnings": [],
}
RUN = {
    "run_id": "0e083ef3-900e-48ca-b025-03d1eae63aba", "pipeline_name": "monthly_clinic_supply_performance",
    "flow_run_id": "8f0c", "status": "completed", "trigger": "scheduled",
    "started_at": "2026-11-22T05:00:00+00:00", "finished_at": "2026-11-22T05:00:09+00:00",
    "months": ["2026-10-01", "2026-11-01"], "source_events_read": 9, "rows_upserted": 24, "rows_changed": 0,
    "events_skipped": {"inbound_without_cost": 1}, "warnings": [], "error_stage": None, "error_message": None,
    "code_version": "4bf1530",
}


@pytest.fixture
def stub(monkeypatch):
    calls = {}
    monkeypatch.setattr(reporting, "reporting_reader_engine", lambda: "reader-engine")

    def report(month_start, engine):
        calls["report"] = (month_start, engine)
        return REPORT if month_start in (None, date(2026, 11, 1)) else None

    def trigger(months, triggered_by, engine):
        calls["trigger"] = (months, triggered_by, engine)
        return {"run_id": "run-1", "flow_run_id": None, "mode": "in_process"}

    monkeypatch.setattr(reporting, "get_monthly_clinic_supply_performance", report)
    monkeypatch.setattr(reporting, "trigger_run", trigger)
    monkeypatch.setattr(reporting.run_log, "get_latest_run", lambda engine, name: RUN)
    return calls


@pytest.fixture
def manager_headers(user_factory, auth_headers):
    return auth_headers(user_factory(username="marcus", role="manager"))


@pytest.fixture
def user_headers(user_factory, auth_headers):
    return auth_headers(user_factory(username="clinician", role="user"))


def test_kpi_feed_matches_briefing_contract(client, stub, user_headers):
    response = client.get("/reporting/monthly-clinic-supply-performance", headers=user_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["month_start"] == "2026-11-01"
    assert set(body["clinics"][0]) == {"clinic_id", "country", "total_supply_cost", "supply_consumption_count",
                                       "critical_stockout_count", "expiry_risk_count", "currency"}
    assert stub["report"] == (None, "reader-engine")  # default: latest computed month


def test_kpi_feed_by_month_and_not_computed_month(client, stub, user_headers):
    ok = client.get("/reporting/monthly-clinic-supply-performance?month_start=2026-11-01", headers=user_headers)
    assert ok.status_code == 200
    missing = client.get("/reporting/monthly-clinic-supply-performance?month_start=2026-09-01", headers=user_headers)
    assert missing.status_code == 404  # never a row of zeros for an uncomputed month
    bad = client.get("/reporting/monthly-clinic-supply-performance?month_start=2026-11-15", headers=user_headers)
    assert bad.status_code == 422


def test_latest_run_metadata(client, stub, user_headers):
    response = client.get("/reporting/pipeline-runs/latest", headers=user_headers)
    assert response.status_code == 200
    body = response.json()
    for field in ("status", "started_at", "finished_at", "source_events_read", "rows_upserted", "error_message"):
        assert field in body
    assert body["status"] == "completed"


def test_latest_run_404_before_first_run(client, stub, user_headers, monkeypatch):
    monkeypatch.setattr(reporting.run_log, "get_latest_run", lambda engine, name: None)
    assert client.get("/reporting/pipeline-runs/latest", headers=user_headers).status_code == 404


def test_endpoints_require_a_session(client, stub):
    assert client.get("/reporting/monthly-clinic-supply-performance").status_code == 401
    assert client.get("/reporting/pipeline-runs/latest").status_code == 401
    assert client.post("/reporting/pipeline-runs").status_code == 401


def test_manual_run_is_accepted_for_managers_with_pseudonymous_trigger(client, stub, manager_headers):
    response = client.post("/reporting/pipeline-runs", json={"months": ["2026-11-01"]}, headers=manager_headers)
    assert response.status_code == 202
    assert response.json() == {"run_id": "run-1", "flow_run_id": None, "status": "accepted", "mode": "in_process"}
    months, triggered_by, engine = stub["trigger"]
    assert months == [date(2026, 11, 1)]
    assert len(triggered_by) == 64 and "marcus" not in triggered_by  # HMAC, never the account
    assert engine == "reader-engine"


def test_manual_run_without_body_uses_default_months(client, stub, manager_headers):
    assert client.post("/reporting/pipeline-runs", headers=manager_headers).status_code == 202
    assert stub["trigger"][0] is None


def test_manual_run_is_forbidden_for_plain_users(client, stub, user_headers):
    assert client.post("/reporting/pipeline-runs", headers=user_headers).status_code == 403
    assert "trigger" not in stub


@pytest.mark.parametrize("months", [["2026-11-15"], ["2999-01-01"], []])
def test_manual_run_validates_months(client, stub, manager_headers, months):
    assert client.post("/reporting/pipeline-runs", json={"months": months}, headers=manager_headers).status_code == 422


def test_manual_run_conflicts_with_a_running_one(client, stub, manager_headers, monkeypatch):
    def busy(months, triggered_by, engine):
        raise RunInProgress("abc")

    monkeypatch.setattr(reporting, "trigger_run", busy)
    response = client.post("/reporting/pipeline-runs", headers=manager_headers)
    assert response.status_code == 409


def test_storage_errors_are_503(client, stub, user_headers, manager_headers, monkeypatch):
    def unavailable(*args, **kwargs):
        raise MissingConnection("REPORTING_DATABASE_URL")

    monkeypatch.setattr(reporting, "get_monthly_clinic_supply_performance", unavailable)
    monkeypatch.setattr(reporting.run_log, "get_latest_run", unavailable)
    monkeypatch.setattr(reporting, "trigger_run", unavailable)
    assert client.get("/reporting/monthly-clinic-supply-performance", headers=user_headers).status_code == 503
    assert client.get("/reporting/pipeline-runs/latest", headers=user_headers).status_code == 503
    assert client.post("/reporting/pipeline-runs", headers=manager_headers).status_code == 503
