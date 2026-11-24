from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from services.api.database import get_db
from services.api.main import app
from services.api.services import cache as cache_module
from services.api.services.cache import TTLCache, response_cache


def test_ttl_cache_expires_and_rejects_stale_write_after_invalidation(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(cache_module, "monotonic", lambda: clock[0])
    cache = TTLCache()

    hit, _, version = cache.get("products", ("user-1",))
    assert not hit
    cache.set("products", ("user-1",), ["old"], 5, version)
    assert cache.get("products", ("user-1",))[1] == ["old"]

    cache.invalidate("products")
    cache.set("products", ("user-1",), ["stale"], 5, version)
    assert cache.get("products", ("user-1",))[0] is False

    hit, _, next_version = cache.get("products", ("user-1",))
    assert not hit and next_version == version + 1
    cache.set("products", ("user-1",), ["fresh"], 5, next_version)
    clock[0] += 6
    assert cache.get("products", ("user-1",))[0] is False


def test_supplier_cache_is_user_scoped_and_invalidated_on_write(
    client, user_factory, auth_headers
):
    response_cache.invalidate("suppliers")
    first_user = user_factory(username="first")
    second_user = user_factory(username="second")

    first_headers = auth_headers(first_user)
    second_headers = auth_headers(second_user)
    assert client.get("/suppliers", headers=first_headers).json() == []
    assert client.get("/suppliers", headers=second_headers).json() == []
    assert response_cache.get("suppliers", (first_user["id"], None, None))[0]
    assert response_cache.get("suppliers", (second_user["id"], None, None))[0]

    response = client.post("/suppliers", headers=first_headers, json={
        "name": "Cache invalidation sample",
        "country": "USA",
        "categories": ["medical_supplies"],
        "monthly_rate": 10,
        "currency": "USD",
        "status": "active",
    })
    assert response.status_code == 201
    assert client.get("/suppliers", headers=first_headers).json()[0]["name"] == "Cache invalidation sample"


def test_timing_log_uses_route_template_and_excludes_query_string(
    client, admin, auth_headers, caplog
):
    with caplog.at_level("INFO", logger="api.timing"):
        response = client.get(
            f"/users/{admin['id']}?access_token=must-not-be-logged",
            headers=auth_headers(admin),
        )
    assert response.status_code == 200
    assert f"GET /users/{{user_id}} -> 200" in caplog.text
    assert admin["id"] not in caplog.text
    assert "must-not-be-logged" not in caplog.text


def test_inventory_product_cache_is_invalidated_by_movements(
    client, user_factory, auth_headers
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def get_test_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    try:
        user = user_factory(username="inventory-user")
        headers = auth_headers(user)
        response_cache.invalidate("inventory-products")
        created = client.post("/inventory/products", headers=headers, json={
            "name": "Test supply",
            "sku": "CACHE-TEST",
            "category": "ppe",
            "unit": "box",
            "country": "US",
            "product_category": "ppe",
        })
        assert created.status_code == 201
        assert client.get("/inventory/products", headers=headers).json()[0]["current_stock"] == 0

        inbound = client.post("/inventory/orders/inbound", headers=headers, json={
            "supply_id": created.json()["id"],
            "quantity": 5,
            "clinic_id": 1,
            "vendor_name": "Test vendor",
        })
        assert inbound.status_code == 201
        assert client.get("/inventory/products", headers=headers).json()[0]["current_stock"] == 5

        outbound = client.post("/inventory/orders/outbound", headers=headers, json={
            "supply_id": created.json()["id"],
            "quantity": 2,
            "clinic_id": 1,
            "consumption_type": "clinical_use",
            "department": "primary_care",
        })
        assert outbound.status_code == 201
        assert client.get("/inventory/products", headers=headers).json()[0]["current_stock"] == 3
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
