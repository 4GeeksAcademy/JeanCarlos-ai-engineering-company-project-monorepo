from sqlalchemy import func
from sqlmodel import Session, select

from ..models.inventory import SupplyConsumption, SupplyDelivery


def stock_for_supply(session: Session, supply_id: int) -> int:
    delivered = session.exec(
        select(func.coalesce(func.sum(SupplyDelivery.quantity), 0))
        .where(SupplyDelivery.supply_id == supply_id)
    ).one()
    consumed = session.exec(
        select(func.coalesce(func.sum(SupplyConsumption.quantity), 0))
        .where(SupplyConsumption.supply_id == supply_id)
    ).one()
    return int(delivered - consumed)
