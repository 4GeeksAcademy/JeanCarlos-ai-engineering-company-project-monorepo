"""Every non-public route rejects requests without a valid Bearer token."""
import pytest

from services.api.database import get_db
from services.api.main import app

PUBLIC_ROUTES = {
    ("POST", "/users"),
    ("POST", "/auth/login"),
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
    ("POST", "/telemetry/events"),
}

# Existing monorepo routes outside /users and /auth that now require a token.
PROTECTED_DOMAIN_ROUTES = [
    ("GET", "/suppliers"),
    ("POST", "/suppliers"),
    ("GET", "/suppliers/1"),
    ("PUT", "/suppliers/1"),
    ("PATCH", "/suppliers/1/rate"),
    ("PATCH", "/suppliers/1/status"),
    ("GET", "/inventory/products"),
    ("POST", "/inventory/products"),
    ("GET", "/inventory/products/1"),
    ("POST", "/inventory/orders/inbound"),
    ("POST", "/inventory/orders/outbound"),
    ("GET", "/inventory/orders"),
    ("GET", "/telemetry/session"),
]


@pytest.fixture
def no_inventory_db():
    def unavailable():
        raise AssertionError("auth must be checked before opening the inventory database")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = unavailable
    yield
    app.dependency_overrides.pop(get_db, None)


def concrete(path):
    return path.replace("{user_id}", "x").replace("{profile_id}", "x").replace(
        "{supplier_id}", "1").replace("{supply_id}", "1")


def test_every_route_except_the_public_ones_requires_a_token(client, no_inventory_db):
    checked = 0
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            method = method.upper()
            if (method, path) in PUBLIC_ROUTES:
                continue
            for headers in ({}, {"Authorization": "Bearer not-a-jwt"}):
                response = client.request(method, concrete(path), headers=headers, json={})
                assert response.status_code == 401, (method, path, headers)
            checked += 1
    assert checked >= 20


@pytest.mark.parametrize("method,path", PROTECTED_DOMAIN_ROUTES)
def test_existing_domain_routes_are_protected(client, no_inventory_db, method, path):
    assert client.request(method, path, json={}).status_code == 401


def test_protected_routes_keep_working_with_a_valid_token(client, user_factory, auth_headers):
    headers = auth_headers(user_factory())
    payload = {
        "name": "Regression supplier", "country": "USA", "categories": ["medical_supplies"],
        "monthly_rate": 10, "currency": "USD", "status": "active",
    }
    created = client.post("/suppliers", headers=headers, json=payload)
    assert created.status_code == 201
    supplier_id = created.json()["id"]
    assert client.get(f"/suppliers/{supplier_id}", headers=headers).status_code == 200
    assert client.get("/suppliers", headers=headers).status_code == 200
    assert client.get("/telemetry/session", headers=headers).status_code == 200
