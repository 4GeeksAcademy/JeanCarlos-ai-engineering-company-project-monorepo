from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..core.dependencies import CurrentUser
from ..database import get_db
from ..models.inventory import MedicalSupply, SupplyConsumption, SupplyDelivery
from ..schemas.inventory import (
    OrdersResponse,
    ProductCreate,
    ProductResponse,
    SupplyConsumptionCreate,
    SupplyConsumptionResponse,
    SupplyDeliveryCreate,
    SupplyDeliveryResponse,
    SupplySummary,
)
from ..services.inventory import stock_for_supply

router = APIRouter(prefix="/inventory", tags=["inventory"])
Database = Annotated[Session, Depends(get_db)]
SupplyId = Annotated[int, Path(gt=0)]


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


@router.get("/products", response_model=list[ProductResponse])
def list_products(_user: CurrentUser, session: Database):
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
    return [
        ProductResponse.model_validate({**supply.model_dump(), "current_stock": int(quantity)})
        for supply, quantity in rows
    ]


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, _user: CurrentUser, session: Database):
    supply = MedicalSupply.model_validate(payload.model_dump())
    session.add(supply)
    try:
        session.flush()
        response = ProductResponse.model_validate({**supply.model_dump(), "current_stock": 0})
        session.commit()
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
    return response


@router.post(
    "/orders/outbound", response_model=SupplyConsumptionResponse, status_code=status.HTTP_201_CREATED
)
def create_consumption(payload: SupplyConsumptionCreate, user: CurrentUser, session: Database):
    supply = supply_or_404(session, payload.supply_id, lock=True)
    available = stock_for_supply(session, payload.supply_id)
    if payload.quantity > available:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient stock for supply '{supply.name}'. "
                f"Available: {available}, requested: {payload.quantity}."
            ),
        )
    consumption = SupplyConsumption(**payload.model_dump(), user_uuid=user.id)
    session.add(consumption)
    session.flush()
    response = consumption_response(consumption, supply)
    session.commit()
    return response


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
