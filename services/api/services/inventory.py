from datetime import date

from sqlalchemy import func, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from ..models.inventory import (
    DEPARTMENTS,
    PRODUCT_CATEGORIES,
    ClinicStockMinimum,
    MedicalSupply,
    SupplyConsumption,
    SupplyDelivery,
)


def stock_for_supply(session: Session, supply_id: int, clinic_id: int | None = None) -> int:
    """Delivered minus consumed units; global, or for one clinic when clinic_id is given."""
    delivered = select(func.coalesce(func.sum(SupplyDelivery.quantity), 0)).where(
        SupplyDelivery.supply_id == supply_id
    )
    consumed = select(func.coalesce(func.sum(SupplyConsumption.quantity), 0)).where(
        SupplyConsumption.supply_id == supply_id
    )
    if clinic_id is not None:
        delivered = delivered.where(SupplyDelivery.clinic_id == clinic_id)
        consumed = consumed.where(SupplyConsumption.clinic_id == clinic_id)
    return int(session.exec(delivered).one() - session.exec(consumed).one())


def minimum_for(session: Session, supply_id: int, clinic_id: int) -> int | None:
    return session.exec(
        select(ClinicStockMinimum.min_quantity).where(
            ClinicStockMinimum.supply_id == supply_id, ClinicStockMinimum.clinic_id == clinic_id
        )
    ).first()


def expiring_stock(session: Session, today: date, within_days: int):
    """(supply, clinic_id, quantity) with positive clinic stock expiring within the window."""
    limit = date.fromordinal(today.toordinal() + within_days)
    delivered = (
        select(
            SupplyDelivery.supply_id,
            SupplyDelivery.clinic_id,
            func.sum(SupplyDelivery.quantity).label("total"),
        )
        .group_by(SupplyDelivery.supply_id, SupplyDelivery.clinic_id)
        .subquery()
    )
    consumed = (
        select(
            SupplyConsumption.supply_id,
            SupplyConsumption.clinic_id,
            func.sum(SupplyConsumption.quantity).label("total"),
        )
        .group_by(SupplyConsumption.supply_id, SupplyConsumption.clinic_id)
        .subquery()
    )
    stock = delivered.c.total - func.coalesce(consumed.c.total, 0)
    return session.exec(
        select(MedicalSupply, delivered.c.clinic_id, stock)
        .join(delivered, delivered.c.supply_id == MedicalSupply.id)
        .outerjoin(
            consumed,
            (consumed.c.supply_id == delivered.c.supply_id)
            & (consumed.c.clinic_id == delivered.c.clinic_id),
        )
        .where(MedicalSupply.expiry_date.is_not(None))
        .where(MedicalSupply.expiry_date >= today, MedicalSupply.expiry_date <= limit)
        .where(stock > 0)
        .order_by(MedicalSupply.expiry_date, MedicalSupply.id, delivered.c.clinic_id)
    ).all()


# create_all does not alter existing tables; these idempotent statements add the
# columns introduced for the telemetry BRIEFING to databases created earlier.
_LEGACY_CATEGORY = {
    "medications": "medication",
    "ppe": "ppe",
    "consumables": "consumable",
    "wound_care": "consumable",
}


def upgrade_inventory_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    categories = ", ".join(f"'{value}'" for value in PRODUCT_CATEGORIES)
    departments = ", ".join(f"'{value}'" for value in DEPARTMENTS)
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE medical_supply ADD COLUMN IF NOT EXISTS product_category VARCHAR "
            f"CONSTRAINT medical_supply_product_category_check CHECK (product_category IN ({categories}))"
        ))
        connection.execute(text("ALTER TABLE medical_supply ADD COLUMN IF NOT EXISTS expiry_date DATE"))
        connection.execute(text(
            "ALTER TABLE supply_consumption ADD COLUMN IF NOT EXISTS department VARCHAR "
            f"CONSTRAINT supply_consumption_department_check CHECK (department IN ({departments}))"
        ))
        for legacy, category in _LEGACY_CATEGORY.items():
            connection.execute(
                text(
                    "UPDATE medical_supply SET product_category = :category "
                    "WHERE product_category IS NULL AND category = :legacy"
                ),
                {"category": category, "legacy": legacy},
            )
