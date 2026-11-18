"""Serialization contract of the supplier directory (no over-fetching in lists)."""
import pytest

from services.api.services.cache import response_cache

SUPPLIER = {
    "name": "MedLine Industries", "country": "USA", "categories": ["medical_supplies"],
    "monthly_rate": 1200.0, "currency": "USD", "status": "active", "compliance_agreement": "BAA",
    "contract_renewal_date": "2027-03-31", "contact_email": "accounts@medline.example",
    "notes": "Internal negotiation notes",
}
LIST_FIELDS = {
    "id", "name", "country", "categories", "monthly_rate", "currency", "status",
    "compliance_agreement", "contact_email", "updated_at",
}
DETAIL_ONLY = {"notes", "contract_renewal_date"}


@pytest.fixture
def headers(user_factory, auth_headers):
    response_cache.invalidate("suppliers")
    return auth_headers(user_factory())


@pytest.fixture
def created(client, headers):
    response = client.post("/suppliers", headers=headers, json=SUPPLIER)
    assert response.status_code == 201
    return response.json()


def test_list_returns_only_the_fields_the_backoffice_table_uses(client, headers, created):
    [item] = client.get("/suppliers", headers=headers).json()
    assert set(item) == LIST_FIELDS
    # Detail-only fields are not serialized for every row of the directory.
    assert not DETAIL_ONLY & set(item)


def test_filtered_and_cached_lists_keep_the_reduced_contract(client, headers, created):
    first = client.get("/suppliers", headers=headers, params={"country": "USA"}).json()
    cached = client.get("/suppliers", headers=headers, params={"country": "USA"}).json()
    assert first == cached
    assert all(set(item) == LIST_FIELDS for item in cached)


def test_detail_and_write_responses_keep_the_full_supplier(client, headers, created):
    detail = client.get(f"/suppliers/{created['id']}", headers=headers).json()
    assert detail["notes"] == "Internal negotiation notes"
    assert detail["contract_renewal_date"] == "2027-03-31"
    assert DETAIL_ONLY <= set(created)
    rate = client.patch(f"/suppliers/{created['id']}/rate", headers=headers, json={"monthly_rate": 1300.0}).json()
    assert DETAIL_ONLY <= set(rate)


def test_list_schema_is_independent_from_the_detail_schema(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert set(schemas["SupplierListItem"]["properties"]) == LIST_FIELDS
    assert DETAIL_ONLY <= set(schemas["SupplierResponse"]["properties"])
