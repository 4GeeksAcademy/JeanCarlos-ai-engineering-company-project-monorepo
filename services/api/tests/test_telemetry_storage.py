"""POST /telemetry/events: per-event validation and one bulk insert per batch."""

from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from services.api.routes import telemetry as telemetry_route
from services.api.services.telemetry_store import metadata, telemetry_events


def envelope(event_type, properties, **overrides):
    return {
        "eventId": str(uuid4()),
        "timestamp": "2026-11-21T12:00:00.000Z",
        "sessionId": str(uuid4()),
        "userId": "a" * 64,
        "event_type": event_type,
        "schemaVersion": "2.0.0",
        "requestId": str(uuid4()),
        "properties": properties,
        **overrides,
    }


INBOUND = {
    "clinic_id": 1, "country": "US", "product_id": 6, "product_category": "medication",
    "quantity": 10, "supplier_id": None,
}
ROUTE_VIEW = {"route_template": "/inventory/products", "section": "inventory"}


@pytest.fixture
def store(client, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata.create_all(engine)
    inserts = []

    @event.listens_for(engine, "before_cursor_execute")
    def count_inserts(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("INSERT"):
            inserts.append(statement)

    monkeypatch.setattr(telemetry_route, "get_telemetry_engine", lambda: engine)
    yield client, engine, inserts
    engine.dispose()


def rows(engine):
    with engine.connect() as connection:
        return connection.execute(select(telemetry_events).order_by(telemetry_events.c.event_type)).all()


def test_mixed_batch_stores_valid_events_and_counts_rejected(store):
    client, engine, inserts = store
    batch = [
        envelope("inbound_order_created", INBOUND),
        envelope("frontend_route_viewed", ROUTE_VIEW),
        envelope("inbound_order_created", INBOUND, eventId="not-a-uuid"),
        envelope("claim_denial_rate_calculated", {}),
        envelope("frontend_route_viewed", {**ROUTE_VIEW, "patient_id": "p-1"}),
        envelope("inbound_order_created", {**INBOUND, "product_category": "diagnostics"}),
        envelope("outbound_order_created", {k: v for k, v in INBOUND.items() if k != "supplier_id"}),
    ]

    response = client.post("/telemetry/events", json={"events": batch})

    assert response.status_code == 200
    assert response.json() == {"received": 7, "stored": 2, "rejected": 5}
    assert len(inserts) == 1
    stored = rows(engine)
    assert [row.event_type for row in stored] == ["frontend_route_viewed", "inbound_order_created"]
    inbound = stored[1]
    assert inbound.service == "backoffice"
    assert inbound.tags == {**INBOUND, "_schema_version": "2.0.0"}
    assert str(inbound.id) == batch[0]["eventId"]
    assert inbound.user_id == "a" * 64


def test_retried_batch_is_not_duplicated(store):
    client, engine, _ = store
    batch = [envelope("frontend_route_viewed", ROUTE_VIEW), envelope("inbound_order_created", INBOUND)]
    for _ in range(2):
        response = client.post("/telemetry/events", json={"events": batch})
        assert response.json() == {"received": 2, "stored": 2, "rejected": 0}
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(telemetry_events)).scalar() == 2


def test_batch_of_only_invalid_events_is_still_200(store):
    client, engine, inserts = store
    response = client.post("/telemetry/events", json={"events": [{"event_type": "x"}, 42]})
    assert response.status_code == 200
    assert response.json() == {"received": 2, "stored": 0, "rejected": 2}
    assert inserts == []


@pytest.mark.parametrize("body", [[], {"events": []}, {"events": "x"}, {"other": []},
                                  {"events": [envelope("frontend_route_viewed", ROUTE_VIEW)] * 21}])
def test_unparseable_envelope_is_422(store, body):
    client, _, _ = store
    assert client.post("/telemetry/events", json=body).status_code == 422


def test_missing_storage_configuration_is_503(client, monkeypatch):
    monkeypatch.delenv("TELEMETRY_DATABASE_URL", raising=False)
    telemetry_route.get_telemetry_engine.cache_clear()
    response = client.post(
        "/telemetry/events", json={"events": [envelope("frontend_route_viewed", ROUTE_VIEW)]}
    )
    assert response.status_code == 503


def test_contract_2_1_0_adds_total_cost_and_keeps_accepting_2_0_0(store):
    client, engine, _ = store
    batch = [
        envelope("inbound_order_created", {**INBOUND, "total_cost": 37.05}, schemaVersion="2.1.0"),
        envelope("inbound_order_created", INBOUND),  # 2.0.0 tab, no cost: still valid
        envelope("inbound_order_created", {**INBOUND, "total_cost": -1}, schemaVersion="2.1.0"),
        envelope("inbound_order_created", INBOUND, schemaVersion="3.0.0"),
    ]

    response = client.post("/telemetry/events", json={"events": batch})

    assert response.json() == {"received": 4, "stored": 2, "rejected": 2}
    tags = sorted((row.tags for row in rows(engine)), key=lambda value: value["_schema_version"])
    assert tags == [{**INBOUND, "_schema_version": "2.0.0"},
                    {**INBOUND, "total_cost": 37.05, "_schema_version": "2.1.0"}]
