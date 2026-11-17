"""API-042 — supplier directory (routes.suppliers): business rules, not HTTP."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.api.models import (
    Category, Country, SupplierCreate, SupplierRateUpdate, SupplierStatus, SupplierStatusUpdate,
)
from services.api.routes import suppliers as routes
from services.api.services.cache import response_cache

BASE = {
    "name": "MedLine Industries", "country": "USA", "categories": ["medical_supplies"],
    "monthly_rate": 1200.0, "currency": "USD", "status": "active",
}


@pytest.fixture(autouse=True)
def clean_cache():
    response_cache.invalidate("suppliers")


def create(db, **changes):
    return routes.create_supplier(SupplierCreate(**{**BASE, **changes}), db)


def list_for(db, user, country=None, category=None):
    return routes.list_suppliers(db, user, country, category)


def assert_404(call, *args):
    with pytest.raises(HTTPException) as error:
        call(*args)
    assert error.value.status_code == 404


# --- Happy path ---------------------------------------------------------------------

def test_created_supplier_gets_an_id_and_update_timestamp(db):
    supplier = create(db)
    assert supplier.id == 1
    assert supplier.updated_at is not None
    assert routes.get_supplier(supplier.id, db).name == "MedLine Industries"


def test_filters_combine_country_and_category(db, user_account):
    create(db)
    create(db, name="NHS Supply Chain", country="UK", currency="GBP", categories=["pharmaceutical"])
    create(db, name="Epic", categories=["clinical_software", "it_infrastructure"])

    assert len(list_for(db, user_account)) == 3
    assert [s.name for s in list_for(db, user_account, Country.UK)] == ["NHS Supply Chain"]
    assert [s.name for s in list_for(db, user_account, Country.USA, Category.it_infrastructure)] == ["Epic"]


def test_rate_and_status_updates_change_only_their_field(db):
    supplier = create(db)
    routes.update_rate(supplier.id, SupplierRateUpdate(monthly_rate=1500.0), db)
    updated = routes.update_status(supplier.id, SupplierStatusUpdate(status="suspended"), db)
    assert (updated.monthly_rate, updated.status, updated.name) == (1500.0, SupplierStatus.suspended, "MedLine Industries")


# --- Edge cases -------------------------------------------------------------------------

def test_empty_directory_and_filters_without_matches_return_empty_lists(db, user_account):
    assert list_for(db, user_account) == []
    create(db)
    assert list_for(db, user_account, Country.UK) == []


def test_replace_keeps_updated_at_when_the_rate_does_not_change(db):
    supplier = create(db)
    replaced = routes.replace_supplier(supplier.id, SupplierCreate(**{**BASE, "notes": "renewed"}), db)
    # updated_at tracks rate changes only (billing review), not every edit.
    assert replaced.updated_at == supplier.updated_at
    assert replaced.notes == "renewed"
    changed = routes.replace_supplier(supplier.id, SupplierCreate(**{**BASE, "monthly_rate": 99.0}), db)
    assert changed.updated_at > supplier.updated_at


def test_list_is_cached_per_user_and_invalidated_by_writes(db, user_account, admin_account):
    create(db)
    assert len(list_for(db, user_account)) == 1
    # Write directly to the DB, bypassing the routes: the cached list is still served...
    db.insert({**BASE, "name": "Hidden", "updated_at": "2024-01-01T00:00:00+00:00"})
    assert len(list_for(db, user_account)) == 1
    # ...while another user's first request reads fresh data.
    assert len(list_for(db, admin_account)) == 2
    create(db, name="Visible after invalidation")
    assert len(list_for(db, user_account)) == 3


@pytest.mark.parametrize("changes", [{"categories": []}, {"monthly_rate": 0}, {"name": ""}])
def test_boundary_values_are_rejected(changes):
    with pytest.raises(ValidationError):
        SupplierCreate(**{**BASE, **changes})


# --- Failure modes ------------------------------------------------------------------

@pytest.mark.parametrize("changes", [
    {"country": "UK"},                        # UK suppliers must bill in GBP
    {"currency": "GBP"},                      # USA suppliers must bill in USD
    {"contract_renewal_date": "2024-02-30"},  # well-formed but impossible date
    {"monthly_rate": float("nan")},
    {"categories": ["catering"]},
    {"internal_id": 7},
])
def test_inconsistent_or_unknown_data_is_rejected(changes):
    with pytest.raises(ValidationError):
        SupplierCreate(**{**BASE, **changes})


def test_unknown_supplier_is_404_for_every_operation(db):
    assert_404(routes.get_supplier, 99, db)
    assert_404(routes.replace_supplier, 99, SupplierCreate(**BASE), db)
    assert_404(routes.update_rate, 99, SupplierRateUpdate(monthly_rate=10.0), db)
    assert_404(routes.update_status, 99, SupplierStatusUpdate(status="active"), db)
