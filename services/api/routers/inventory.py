from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..core.dependencies import CurrentUser
from ..database import get_db
from ..models.inventory import MedicalSupply, SupplyConsumption, SupplyDelivery
from ..schemas.inventory import (
    ExpiringSupply,
    OrdersResponse,
    ProductCreate,
    ProductListItem,
    ProductResponse,
    StockThresholdCrossing,
    SupplyConsumptionCreate,
    SupplyConsumptionCreated,
    SupplyConsumptionResponse,
    SupplyDeliveryCreate,
    SupplyDeliveryResponse,
    SupplySummary,
)
from ..services.inventory import expiring_stock, minimum_for, stock_for_supply
from ..services.cache import response_cache

router = APIRouter(prefix="/inventory", tags=["inventory"])
Database = Annotated[Session, Depends(get_db)]
SupplyId = Annotated[int, Path(gt=0)]
ClinicId = Annotated[int, Path(ge=1, le=12)]
EXPIRY_WINDOW_DAYS = 30


def supply_or_404(session: Session, supply_id: int, *, lock: bool = False) -> MedicalSupply:
    statement = select(MedicalSupply).where(MedicalSupply.id == supply_id)
    if lock:
        statement = statement.with_for_update()
    supply = session.exec(statement).first()
    if supply is None:
        raise HTTPException(status_code=404, detail="Supply not found")
    return supply


def supply_summary(supply: MedicalSupply) -> SupplySummary:
    return SupplySummary.model_validate(supply.model_dump())


def delivery_response(delivery: SupplyDelivery, supply: MedicalSupply) -> SupplyDeliveryResponse:
    return SupplyDeliveryResponse.model_validate(
        {**delivery.model_dump(), "supply": supply_summary(supply)}
    )


def consumption_response(
    consumption: SupplyConsumption, supply: MedicalSupply
) -> SupplyConsumptionResponse:
    return SupplyConsumptionResponse.model_validate(
        {**consumption.model_dump(), "supply": supply_summary(supply)}
    )


@router.get("/products", response_model=list[ProductListItem])
def list_products(user: CurrentUser, session: Database):
    hit, cached, version = response_cache.get("inventory-products", user.id)
    if hit:
        return cached
    deliveries = (
        select(SupplyDelivery.supply_id, func.sum(SupplyDelivery.quantity).label("total"))
        .group_by(SupplyDelivery.supply_id)
        .subquery()
    )
    consumptions = (
        select(SupplyConsumption.supply_id, func.sum(SupplyConsumption.quantity).label("total"))
        .group_by(SupplyConsumption.supply_id)
        .subquery()
    )
    stock = func.coalesce(deliveries.c.total, 0) - func.coalesce(consumptions.c.total, 0)
    rows = session.exec(
        select(MedicalSupply, stock)
        .outerjoin(deliveries, deliveries.c.supply_id == MedicalSupply.id)
        .outerjoin(consumptions, consumptions.c.supply_id == MedicalSupply.id)
        .order_by(MedicalSupply.id)
    ).all()
    products = [
        ProductListItem.model_validate({**supply.model_dump(), "current_stock": int(quantity)})
        for supply, quantity in rows
    ]
    response_cache.set(
        "inventory-products",
        user.id,
        products,
        ttl_seconds=10,
        expected_version=version,
    )
    return products


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, _user: CurrentUser, session: Database):
    supply = MedicalSupply.model_validate(payload.model_dump())
    session.add(supply)
    try:
        session.flush()
        response = ProductResponse.model_validate({**supply.model_dump(), "current_stock": 0})
        session.commit()
        response_cache.invalidate("inventory-products")
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="SKU already exists") from exc
    return response


@router.get("/products/{supply_id}", response_model=ProductResponse)
def get_product(supply_id: SupplyId, _user: CurrentUser, session: Database):
    supply = supply_or_404(session, supply_id)
    stock = stock_for_supply(session, supply_id)
    return ProductResponse.model_validate({**supply.model_dump(), "current_stock": stock})


@router.post(
    "/orders/inbound", response_model=SupplyDeliveryResponse, status_code=status.HTTP_201_CREATED
)
def create_delivery(payload: SupplyDeliveryCreate, user: CurrentUser, session: Database):
    supply = supply_or_404(session, payload.supply_id, lock=True)
    delivery = SupplyDelivery(**payload.model_dump(), user_uuid=user.id)
    session.add(delivery)
    session.flush()
    response = delivery_response(delivery, supply)
    session.commit()
    response_cache.invalidate("inventory-products")
    return response


@router.post(
    "/orders/outbound", response_model=SupplyConsumptionCreated, status_code=status.HTTP_201_CREATED
)
def create_consumption(payload: SupplyConsumptionCreate, user: CurrentUser, session: Database):
    supply = supply_or_404(session, payload.supply_id, lock=True)
    # Stock is consumed from the clinic that registers the order.
    available = stock_for_supply(session, payload.supply_id, payload.clinic_id)
    if payload.quantity > available:
        raise HTTPException(
            status_code=400,
            headers={"X-Available-Stock": str(available)},
            detail=(
                f"Insufficient stock for supply '{supply.name}'. "
                f"Available: {available}, requested: {payload.quantity}."
            ),
        )
    consumption = SupplyConsumption(**payload.model_dump(), user_uuid=user.id)
    session.add(consumption)
    session.flush()
    remaining = available - payload.quantity
    minimum = minimum_for(session, payload.supply_id, payload.clinic_id)
    # Falling edge only: at or above the minimum before, below it after.
    crossing = (
        StockThresholdCrossing(clinic_stock=remaining, min_stock_threshold=minimum)
        if minimum is not None and available >= minimum > remaining
        else None
    )
    response = SupplyConsumptionCreated.model_validate(
        {**consumption_response(consumption, supply).model_dump(), "stock_threshold": crossing}
    )
    session.commit()
    response_cache.invalidate("inventory-products")
    return response


@router.api_route(
    "/clinics/{clinic_id}/products/{supply_id}/stock",
    methods=["PUT", "PATCH", "POST", "DELETE"],
    status_code=status.HTTP_403_FORBIDDEN,
    responses={403: {"description": "Stock only changes through inbound/outbound orders"}},
)
def reject_direct_stock_edit(clinic_id: ClinicId, supply_id: SupplyId, _user: CurrentUser, session: Database):
    supply_or_404(session, supply_id)
    raise HTTPException(
        status_code=403,
        detail="El stock no se modifica directamente: registra una orden de entrada o de salida.",
    )


@router.get("/expiring", response_model=list[ExpiringSupply])
def list_expiring(
    _user: CurrentUser,
    session: Database,
    within_days: Annotated[int, Query(ge=0, le=EXPIRY_WINDOW_DAYS)] = EXPIRY_WINDOW_DAYS,
):
    today = datetime.now(timezone.utc).date()
    return [
        ExpiringSupply(
            supply=supply_summary(supply),
            clinic_id=clinic_id,
            quantity=int(quantity),
            expiry_date=supply.expiry_date,
            days_until_expiry=(supply.expiry_date - today).days,
        )
        for supply, clinic_id, quantity in expiring_stock(session, today, within_days)
    ]


@router.get("/orders", response_model=OrdersResponse)
def list_orders(_user: CurrentUser, session: Database):
    deliveries = session.exec(
        select(SupplyDelivery, MedicalSupply)
        .join(MedicalSupply, SupplyDelivery.supply_id == MedicalSupply.id)
        .order_by(SupplyDelivery.id)
    ).all()
    consumptions = session.exec(
        select(SupplyConsumption, MedicalSupply)
        .join(MedicalSupply, SupplyConsumption.supply_id == MedicalSupply.id)
        .order_by(SupplyConsumption.id)
    ).all()
    return OrdersResponse(
        inbound=[delivery_response(order, supply) for order, supply in deliveries],
        outbound=[consumption_response(order, supply) for order, supply in consumptions],
    )
