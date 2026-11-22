"""services/reporting: HTTP contract, auth and error mapping. The pipeline and the queue are stubbed."""

import json
from datetime import date

import pytest
from kombu.exceptions import OperationalError

from data.pipelines.connections import MissingConnection
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

    calls["released"] = []
    monkeypatch.setattr(reporting, "claim_pipeline_slot", lambda task_id: calls.setdefault("holder"))
    monkeypatch.setattr(reporting, "release_pipeline_slot", lambda task_id: calls["released"].append(task_id))
    monkeypatch.setattr(reporting, "mark_pending", lambda task_id: calls.setdefault("stored", (task_id, "PENDING")))

    def apply_async(kwargs, task_id):
        calls["enqueued"] = (kwargs, task_id)

    monkeypatch.setattr(reporting.run_monthly_clinic_supply_performance, "apply_async", apply_async)
    monkeypatch.setattr(reporting, "get_monthly_clinic_supply_performance", report)
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


def test_manual_run_is_queued_and_answers_202_with_a_task_id(client, stub, manager_headers):
    response = client.post("/reporting/pipeline-runs", json={"months": ["2026-11-01"]}, headers=manager_headers)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted" and body["task_id"] == body["run_id"]
    kwargs, task_id = stub["enqueued"]
    assert task_id == body["task_id"] and stub["stored"] == (task_id, "PENDING")  # known before a worker starts
    # The message carries identifiers only: run id, ISO months and the HMAC pseudonym.
    assert set(kwargs) == {"run_id", "months", "triggered_by"} and kwargs["months"] == ["2026-11-01"]
    assert len(kwargs["triggered_by"]) == 64 and "marcus" not in kwargs["triggered_by"]
    assert len(json.dumps(kwargs)) < 200


def test_manual_run_without_body_uses_default_months(client, stub, manager_headers):
    assert client.post("/reporting/pipeline-runs", headers=manager_headers).status_code == 202
    assert stub["enqueued"][0]["months"] is None


def test_manual_run_is_forbidden_for_plain_users(client, stub, user_headers):
    assert client.post("/reporting/pipeline-runs", headers=user_headers).status_code == 403
    assert "enqueued" not in stub


@pytest.mark.parametrize("months", [["2026-11-15"], ["2999-01-01"], []])
def test_manual_run_validates_months(client, stub, manager_headers, months):
    assert client.post("/reporting/pipeline-runs", json={"months": months}, headers=manager_headers).status_code == 422


def test_manual_run_conflicts_with_a_queued_or_running_one(client, stub, manager_headers, monkeypatch):
    monkeypatch.setattr(reporting, "claim_pipeline_slot", lambda task_id: "abc")
    response = client.post("/reporting/pipeline-runs", headers=manager_headers)
    assert response.status_code == 409 and "abc" in response.json()["detail"]
    assert "enqueued" not in stub


def test_broker_down_is_503_and_frees_the_slot(client, stub, manager_headers, monkeypatch):
    def down(kwargs, task_id):
        raise OperationalError("Error 111 connecting to redis:6379")

    monkeypatch.setattr(reporting.run_monthly_clinic_supply_performance, "apply_async", down)
    response = client.post("/reporting/pipeline-runs", headers=manager_headers)
    assert response.status_code == 503
    assert stub["released"] == [stub["stored"][0]]


def test_storage_errors_are_503(client, stub, user_headers, manager_headers, monkeypatch):
    def unavailable(*args, **kwargs):
        raise MissingConnection("REPORTING_DATABASE_URL")

    monkeypatch.setattr(reporting, "get_monthly_clinic_supply_performance", unavailable)
    monkeypatch.setattr(reporting.run_log, "get_latest_run", unavailable)
    assert client.get("/reporting/monthly-clinic-supply-performance", headers=user_headers).status_code == 503
    assert client.get("/reporting/pipeline-runs/latest", headers=user_headers).status_code == 503
