"""services/telemetry/analysis.py metrics and GET /telemetry/report."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from services.api.routes import telemetry as telemetry_route
from services.api.services.cache import response_cache
from services.api.services.telemetry_store import metadata, telemetry_events
from services.telemetry import analysis

DAY1 = datetime(2026, 11, 20, tzinfo=timezone.utc)
DAY2 = datetime(2026, 11, 21, tzinfo=timezone.utc)
START, END = DAY1, DAY2 + timedelta(days=1)


def row(when, event_type, tags=None):
    return {
        "id": uuid4(), "timestamp": when, "service": "backoffice", "event_type": event_type,
        "session_id": None, "user_id": None, "request_id": None, "tags": tags or {},
    }


def latency(when, route, method, status_class, count, p50, p95):
    return row(when, "api_latency_aggregated", {
        "route_template": route, "method": method, "status_class": status_class,
        "request_count": count, "p50_ms": p50, "p95_ms": p95, "_schema_version": "2.0.0",
    })


ROWS = [
    # Day 1
    row(DAY1 + timedelta(hours=9), "auth_login_failed", {"failure_reason": "invalid_credentials"}),
    row(DAY1 + timedelta(hours=9, minutes=1), "auth_login_succeeded", {"role": "admin"}),
    row(DAY1 + timedelta(hours=10), "inbound_order_created", {"clinic_id": 1}),
    latency(DAY1 + timedelta(hours=10), "/inventory/products", "GET", "2xx", 8, 10.0, 20.0),
    latency(DAY1 + timedelta(hours=11), "/inventory/products", "GET", "2xx", 2, 20.0, 40.0),
    latency(DAY1 + timedelta(hours=11), "/inventory/orders/outbound", "POST", "2xx", 3, 15.0, 30.0),
    latency(DAY1 + timedelta(hours=11), "/inventory/orders/outbound", "POST", "4xx", 1, 12.0, 12.0),
    # Day 2
    row(DAY2 + timedelta(hours=8), "auth_login_succeeded", {"role": "user"}),
    row(DAY2 + timedelta(hours=8, minutes=5), "frontend_unhandled_error",
        {"route_template": "/inventory/products", "error_code": "network_failure", "release": "v1"}),
    row(DAY2 + timedelta(hours=8, minutes=6), "frontend_unhandled_error",
        {"route_template": "/inventory/orders", "error_code": "network_failure", "release": "v1"}),
    latency(DAY2 + timedelta(hours=9), "/inventory/products", "GET", "5xx", 1, 300.0, 300.0),
    latency(DAY2 + timedelta(hours=9), "/inventory/products", "GET", "2xx", 3, 12.0, 18.0),
    # Missing dimension: dropped in Pandas, never counted.
    row(DAY2 + timedelta(hours=9), "api_latency_aggregated", {"method": "GET"}),
    # Outside [START, END): filtered in SQL.
    row(END, "auth_login_failed"),
    row(DAY1 - timedelta(seconds=1), "auth_login_failed"),
]


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(telemetry_events), ROWS)
    yield engine
    engine.dispose()


def by(records, *keys):
    return {tuple(record[key] for key in keys): record for record in records}


def test_events_per_day_counts_the_window_only(engine):
    result = analysis.events_per_day(engine, START, END)
    assert result == [
        {"date": "2026-11-20", "events": 7, "distinct_event_types": 4},
        {"date": "2026-11-21", "events": 6, "distinct_event_types": 3},
    ]


def test_event_type_distribution_shares_sum_to_one_per_day(engine):
    result = analysis.event_type_distribution(engine, START, END)
    day1 = [record for record in result if record["date"] == "2026-11-20"]
    assert day1[0] == {"date": "2026-11-20", "event_type": "api_latency_aggregated", "events": 4, "share_of_day": 0.5714}
    assert round(sum(record["share_of_day"] for record in day1), 3) == 1.0


def test_error_rate_by_type_separates_client_and_server_errors(engine):
    result = by(analysis.error_rate_by_type(engine, START, END), "date", "route_template")
    outbound = result[("2026-11-20", "/inventory/orders/outbound")]
    assert (outbound["requests"], outbound["errors_4xx"], outbound["errors_5xx"]) == (4, 1, 0)
    assert outbound["error_rate"] == 0.25 and outbound["server_error_rate"] == 0.0
    products = result[("2026-11-21", "/inventory/products")]
    assert (products["requests"], products["errors_5xx"], products["server_error_rate"]) == (4, 1, 0.25)


def test_api_latency_is_request_weighted(engine):
    result = by(analysis.api_latency_by_route(engine, START, END), "date", "route_template")
    products = result[("2026-11-20", "/inventory/products")]
    # (10*8 + 20*2) / 10 and (20*8 + 40*2) / 10
    assert (products["requests"], products["p50_ms"], products["p95_ms"], products["p95_max_ms"]) == (10, 12.0, 24.0, 40.0)


def test_frontend_errors_per_day(engine):
    assert analysis.frontend_errors_per_day(engine, START, END) == [
        {"date": "2026-11-21", "error_code": "network_failure", "errors": 2, "affected_routes": 2, "releases": 1},
    ]


def test_auth_failure_rate_per_day(engine):
    assert analysis.auth_failure_rate(engine, START, END) == [
        {"date": "2026-11-20", "attempts": 2, "failures": 1, "failure_rate": 0.5},
        {"date": "2026-11-21", "attempts": 1, "failures": 0, "failure_rate": 0.0},
    ]


def test_report_is_deterministic_and_json_serialisable(engine):
    first = analysis.build_report(engine, START, END)
    assert first == analysis.build_report(engine, START, END)
    assert json.loads(json.dumps(first)) == first
    assert first["period"] == {"from": "2026-11-20T00:00:00Z", "to": "2026-11-22T00:00:00Z"}
    assert set(first["metrics"]) == set(analysis.METRICS)


def test_empty_window_returns_empty_lists(engine):
    report = analysis.build_report(engine, END + timedelta(days=30), END + timedelta(days=31))
    assert all(value == [] for value in report["metrics"].values())


@pytest.fixture
def api(client, user_factory, auth_headers, engine, monkeypatch):
    calls = []

    def counting_build_report(report_engine, start, end):
        calls.append((start, end))
        return analysis.build_report(report_engine, start, end)

    monkeypatch.setattr(telemetry_route, "get_report_engine", lambda: engine)
    monkeypatch.setattr(telemetry_route, "build_report", counting_build_report)
    response_cache.invalidate(telemetry_route.REPORT_CACHE_NAMESPACE)
    headers = auth_headers(user_factory(username="engineer"))
    return client, headers, calls


def test_report_requires_authentication(api):
    client, _, _ = api
    assert client.get("/telemetry/report").status_code == 401


def test_report_with_explicit_window(api):
    client, headers, calls = api
    response = client.get("/telemetry/report", headers=headers,
                          params={"start_date": "2026-11-20", "end_date": "2026-11-22T00:00:00Z"})
    assert response.status_code == 200
    body = response.json()
    assert body["period"] == {"from": "2026-11-20T00:00:00Z", "to": "2026-11-22T00:00:00Z"}
    assert body["metrics"]["auth_failure_rate"][0]["failure_rate"] == 0.5
    assert calls == [(START, END)]


def test_default_window_is_last_seven_days(api):
    client, headers, calls = api
    body = client.get("/telemetry/report", headers=headers).json()
    start, end = calls[0]
    assert end - start == timedelta(days=7)
    assert abs(datetime.now(timezone.utc) - end) < timedelta(minutes=1)
    assert body["period"]["from"].endswith("Z")


def test_report_is_cached_for_the_same_parameters(api, monkeypatch):
    client, headers, calls = api
    params = {"start_date": "2026-11-20", "end_date": "2026-11-22"}
    assert client.get("/telemetry/report", headers=headers, params=params).status_code == 200
    assert client.get("/telemetry/report", headers=headers, params=params).status_code == 200
    client.get("/telemetry/report", headers=headers)
    client.get("/telemetry/report", headers=headers)
    assert len(calls) == 2  # one per parameter combination

    # After the 60 s TTL the pipeline runs again.
    from services.api.services import cache as cache_module
    real_monotonic = cache_module.monotonic
    monkeypatch.setattr(cache_module, "monotonic", lambda: real_monotonic() + 61)
    client.get("/telemetry/report", headers=headers, params=params)
    assert len(calls) == 3


@pytest.mark.parametrize("params", [
    {"start_date": "2026-11-22", "end_date": "2026-11-20"},
    {"start_date": "2026-01-01", "end_date": "2026-11-20"},
    {"start_date": "not-a-date"},
])
def test_invalid_windows_are_rejected(api, params):
    client, headers, calls = api
    assert client.get("/telemetry/report", headers=headers, params=params).status_code == 422
    assert calls == []


def test_report_unavailable_without_database(client, user_factory, auth_headers, monkeypatch):
    monkeypatch.delenv("TELEMETRY_REPORT_DATABASE_URL", raising=False)
    telemetry_route.get_report_engine.cache_clear()
    response_cache.invalidate(telemetry_route.REPORT_CACHE_NAMESPACE)
    headers = auth_headers(user_factory(username="engineer"))
    assert client.get("/telemetry/report", headers=headers).status_code == 503
