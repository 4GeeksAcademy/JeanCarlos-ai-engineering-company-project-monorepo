"""API behaviour the BRIEFING telemetry events depend on (per-clinic stock, minimums, expiry)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from services.api.database import get_db
from services.api.main import app
from services.api.models.inventory import ClinicStockMinimum, MedicalSupply
from services.api.services.cache import response_cache


@pytest.fixture
def inventory(client, user_factory, auth_headers):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def get_test_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    response_cache.invalidate("inventory-products")
    headers = auth_headers(user_factory(username="inventory-user"))
    try:
        yield client, headers, engine
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def create_product(client, headers, sku="T-1", **extra):
    response = client.post("/inventory/products", headers=headers, json={
        "name": "Test supply", "sku": sku, "category": "medications", "unit": "vial",
        "country": "US", "product_category": "medication", **extra,
    })
    assert response.status_code == 201, response.text
    return response.json()


def inbound(client, headers, supply_id, quantity, clinic_id):
    response = client.post("/inventory/orders/inbound", headers=headers, json={
        "supply_id": supply_id, "quantity": quantity, "clinic_id": clinic_id, "vendor_name": "Vendor",
    })
    assert response.status_code == 201, response.text


def outbound(client, headers, supply_id, quantity, clinic_id, department="chronic_care"):
    return client.post("/inventory/orders/outbound", headers=headers, json={
        "supply_id": supply_id, "quantity": quantity, "clinic_id": clinic_id,
        "consumption_type": "clinical_use", "department": department,
    })


def test_product_requires_briefing_category(inventory):
    client, headers, _ = inventory
    response = client.post("/inventory/products", headers=headers, json={
        "name": "X", "sku": "X-1", "category": "ppe", "unit": "box", "country": "US",
    })
    assert response.status_code == 422


def test_outbound_requires_department_and_returns_it(inventory):
    client, headers, _ = inventory
    product = create_product(client, headers)
    inbound(client, headers, product["id"], 5, 1)
    missing = client.post("/inventory/orders/outbound", headers=headers, json={
        "supply_id": product["id"], "quantity": 1, "clinic_id": 1, "consumption_type": "clinical_use",
    })
    assert missing.status_code == 422
    created = outbound(client, headers, product["id"], 1, 1, department="specialty_care")
    assert created.status_code == 201
    assert created.json()["department"] == "specialty_care"
    assert created.json()["supply"]["product_category"] == "medication"


def test_outbound_is_limited_to_the_clinic_stock(inventory):
    client, headers, _ = inventory
    product = create_product(client, headers)
    inbound(client, headers, product["id"], 10, 1)
    rejected = outbound(client, headers, product["id"], 1, 2)
    assert rejected.status_code == 400
    assert rejected.headers["X-Available-Stock"] == "0"


def test_threshold_is_reported_only_on_the_falling_edge(inventory):
    client, headers, engine = inventory
    product = create_product(client, headers)
    inbound(client, headers, product["id"], 12, 1)
    with Session(engine) as session:
        session.add(ClinicStockMinimum(clinic_id=1, supply_id=product["id"], min_quantity=10))
        session.commit()

    assert outbound(client, headers, product["id"], 2, 1).json()["stock_threshold"] is None  # 12 -> 10
    crossing = outbound(client, headers, product["id"], 1, 1).json()["stock_threshold"]  # 10 -> 9
    assert crossing == {"clinic_stock": 9, "min_stock_threshold": 10}
    assert outbound(client, headers, product["id"], 1, 1).json()["stock_threshold"] is None  # 9 -> 8

    inbound(client, headers, product["id"], 5, 1)  # back to 13, re-armed
    assert outbound(client, headers, product["id"], 4, 1).json()["stock_threshold"] == {
        "clinic_stock": 9, "min_stock_threshold": 10,
    }


def test_direct_stock_edit_is_rejected_explicitly(inventory):
    client, headers, _ = inventory
    product = create_product(client, headers)
    for method in ("put", "patch"):
        response = getattr(client, method)(
            f"/inventory/clinics/1/products/{product['id']}/stock", headers=headers, json={"quantity": 99},
        )
        assert response.status_code == 403
    assert client.put("/inventory/clinics/1/products/999/stock", headers=headers).status_code == 404
    assert client.get("/inventory/products", headers=headers).json()[0]["current_stock"] == 0


def test_expiring_lists_clinic_stock_inside_the_window(inventory):
    client, headers, engine = inventory
    today = datetime.now(timezone.utc).date()
    soon = create_product(client, headers, sku="SOON", expiry_date=str(today + timedelta(days=20)))
    later = create_product(client, headers, sku="LATER", expiry_date=str(today + timedelta(days=45)))
    empty = create_product(client, headers, sku="EMPTY", expiry_date=str(today + timedelta(days=5)))
    inbound(client, headers, soon["id"], 8, 10)
    inbound(client, headers, soon["id"], 3, 1)
    outbound(client, headers, soon["id"], 3, 1)  # clinic 1 has no stock left
    inbound(client, headers, later["id"], 4, 1)
    inbound(client, headers, empty["id"], 2, 1)
    outbound(client, headers, empty["id"], 2, 1)

    items = client.get("/inventory/expiring", headers=headers).json()
    assert [(item["supply"]["sku"], item["clinic_id"], item["quantity"], item["days_until_expiry"]) for item in items] == [
        ("SOON", 10, 8, 20),
    ]
    with Session(engine) as session:
        assert session.get(MedicalSupply, soon["id"]).expiry_date == today + timedelta(days=20)


def test_inbound_total_cost_feeds_the_cost_kpi(inventory):
    """inbound_order_created.total_cost (contract 2.1.0) = quantity × unit_cost, as a JSON number."""
    client, headers, _ = inventory
    product = create_product(client, headers, sku="COST-1")
    base = {"supply_id": product["id"], "quantity": 3, "clinic_id": 10, "vendor_name": "Vendor"}

    priced = client.post("/inventory/orders/inbound", headers=headers, json={**base, "unit_cost": 12.35})
    assert priced.status_code == 201, priced.text
    assert (priced.json()["unit_cost"], priced.json()["total_cost"]) == (12.35, 37.05)

    unpriced = client.post("/inventory/orders/inbound", headers=headers, json=base)
    assert unpriced.status_code == 201
    assert unpriced.json()["total_cost"] is None  # never reported as a 0 cost

    for invalid in (-1, 1.234):
        response = client.post("/inventory/orders/inbound", headers=headers, json={**base, "unit_cost": invalid})
        assert response.status_code == 422
