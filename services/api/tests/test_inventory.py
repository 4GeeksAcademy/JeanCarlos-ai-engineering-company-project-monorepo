"""API-042 — medical supply inventory (routers.inventory): stock rules, not HTTP."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from services.api.routers import inventory as routes
from services.api.schemas.inventory import ProductCreate, SupplyConsumptionCreate, SupplyDeliveryCreate
from services.api.services.cache import response_cache

GLOVES = {"name": "Nitrile gloves", "sku": "HCR-PPE-001", "category": "ppe", "unit": "box", "country": "US"}


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    response_cache.invalidate("inventory-products")
    with Session(engine) as session:
        yield session


@pytest.fixture
def gloves(session, user_account):
    return routes.create_product(ProductCreate(**GLOVES), user_account, session)


def deliver(session, user, supply_id, quantity, clinic_id=1):
    return routes.create_delivery(
        SupplyDeliveryCreate(supply_id=supply_id, quantity=quantity, clinic_id=clinic_id, vendor_name="MedLine"),
        user, session,
    )


def consume(session, user, supply_id, quantity, kind="clinical_use"):
    return routes.create_consumption(
        SupplyConsumptionCreate(supply_id=supply_id, quantity=quantity, clinic_id=1, consumption_type=kind),
        user, session,
    )


def stock(session, user, supply_id):
    return routes.get_product(supply_id, user, session).current_stock


# --- Happy path ---------------------------------------------------------------------

def test_new_product_starts_with_zero_stock(session, user_account, gloves):
    assert gloves.current_stock == 0
    assert [p.sku for p in routes.list_products(user_account, session)] == ["HCR-PPE-001"]


def test_stock_is_deliveries_minus_consumptions(session, user_account, gloves):
    deliver(session, user_account, gloves.id, 120)
    deliver(session, user_account, gloves.id, 30, clinic_id=2)
    consume(session, user_account, gloves.id, 40)
    consume(session, user_account, gloves.id, 5, kind="expiry_waste")
    assert stock(session, user_account, gloves.id) == 105
    assert routes.list_products(user_account, session)[0].current_stock == 105


def test_movements_record_who_registered_them(session, user_account, gloves):
    delivery = deliver(session, user_account, gloves.id, 10)
    orders = routes.list_orders(user_account, session)
    # user_uuid is the TinyDB user id taken from the token, never from the payload.
    assert delivery.user_uuid == user_account.id
    assert [o.id for o in orders.inbound] == [delivery.id] and orders.outbound == []


# --- Edge cases -------------------------------------------------------------------------

def test_consuming_exactly_the_available_stock_is_allowed(session, user_account, gloves):
    deliver(session, user_account, gloves.id, 10)
    consume(session, user_account, gloves.id, 10)
    assert stock(session, user_account, gloves.id) == 0


def test_empty_inventory_lists_nothing(session, user_account):
    assert routes.list_products(user_account, session) == []
    orders = routes.list_orders(user_account, session)
    assert orders.inbound == [] and orders.outbound == []


def test_product_list_cache_is_refreshed_after_a_movement(session, user_account, gloves):
    assert routes.list_products(user_account, session)[0].current_stock == 0  # cached
    deliver(session, user_account, gloves.id, 7)
    assert routes.list_products(user_account, session)[0].current_stock == 7


@pytest.mark.parametrize("field,value", [("quantity", 0), ("clinic_id", 13), ("clinic_id", 0), ("quantity", "5")])
def test_out_of_range_or_loosely_typed_movements_are_rejected(field, value):
    data = {"supply_id": 1, "quantity": 1, "clinic_id": 1, "vendor_name": "MedLine", field: value}
    with pytest.raises(ValidationError):
        SupplyDeliveryCreate(**data)


# --- Failure modes ------------------------------------------------------------------

def test_consuming_more_than_available_is_rejected_and_reports_the_stock(session, user_account, gloves):
    deliver(session, user_account, gloves.id, 3)
    with pytest.raises(HTTPException) as error:
        consume(session, user_account, gloves.id, 4)
    assert error.value.status_code == 400
    # The frontend reads this header to show the available quantity.
    assert error.value.headers == {"X-Available-Stock": "3"}
    assert stock(session, user_account, gloves.id) == 3  # nothing was recorded


def test_duplicate_sku_is_409(session, user_account, gloves):
    with pytest.raises(HTTPException) as error:
        routes.create_product(ProductCreate(**{**GLOVES, "name": "Other gloves"}), user_account, session)
    assert error.value.status_code == 409


def test_movements_for_unknown_products_are_404(session, user_account):
    for call in (lambda: deliver(session, user_account, 99, 1), lambda: consume(session, user_account, 99, 1),
                 lambda: routes.get_product(99, user_account, session)):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 404


@pytest.mark.parametrize("changes", [{"category": "food"}, {"country": "ES"}, {"sku": ""}, {"price": 3}])
def test_invalid_products_are_rejected(changes):
    with pytest.raises(ValidationError):
        ProductCreate(**{**GLOVES, **changes})
