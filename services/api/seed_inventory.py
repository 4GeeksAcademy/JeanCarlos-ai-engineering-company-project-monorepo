import argparse
import logging
import sys

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, SQLModel, select
from tinydb import Query

from .database import get_inventory_engine, open_database
from .models.inventory import MedicalSupply, SupplyConsumption, SupplyDelivery
from .services.inventory import stock_for_supply

logger = logging.getLogger(__name__)

SUPPLIES = [
    {"name": "Guantes de nitrilo (caja de 100)", "sku": "HCR-PPE-001", "category": "ppe", "unit": "box", "country": "US"},
    {"name": "Mascarilla quirúrgica (pack de 50)", "sku": "HCR-PPE-002", "category": "ppe", "unit": "pack", "country": "UK"},
    {"name": "Apósito adhesivo para heridas", "sku": "HCR-WND-001", "category": "wound_care", "unit": "box", "country": "US"},
    {"name": "Test rápido de estreptococo", "sku": "HCR-DIAG-001", "category": "diagnostics", "unit": "unit", "country": "US"},
    {"name": "Tiras reactivas glucemia (50)", "sku": "HCR-DIAG-002", "category": "diagnostics", "unit": "box", "country": "UK"},
    {"name": "Solución salina 0,9% 500ml", "sku": "HCR-MED-001", "category": "medications", "unit": "vial", "country": "US"},
]

# Synthetic development movements, not records of actual clinical activity.
DELIVERIES = [
    ("HCR-PPE-001", 120, "MedLine Industries", 1),
    ("HCR-PPE-001", 70, "Bound Tree Medical", 2),
    ("HCR-PPE-002", 90, "Cardinal Health UK", 10),
    ("HCR-DIAG-002", 60, "Cardinal Health UK", 11),
]
CONSUMPTIONS = [
    ("HCR-PPE-001", 30, "clinical_use", 1),
    ("HCR-PPE-001", 5, "expiry_waste", 2),
    ("HCR-PPE-002", 10, "clinical_use", 10),
]


class SeedError(Exception):
    pass


def seed_inventory(user_uuid: str) -> tuple[int, int, int]:
    with open_database() as database:
        if database.table("users").get(Query().id == user_uuid) is None:
            raise SeedError("The specified TinyDB user does not exist")

    engine = get_inventory_engine()
    SQLModel.metadata.create_all(engine)
    created_supplies = created_deliveries = created_consumptions = 0
    with Session(engine) as session:
        supplies = {}
        for data in SUPPLIES:
            supply = session.exec(
                select(MedicalSupply).where(MedicalSupply.sku == data["sku"]).with_for_update()
            ).first()
            if supply is None:
                supply = MedicalSupply(**data)
                session.add(supply)
                session.flush()
                created_supplies += 1
            elif any(getattr(supply, field) != value for field, value in data.items()):
                raise SeedError(f"Existing supply differs from seed: {data['sku']}")
            supplies[data["sku"]] = supply

        for sku, quantity, vendor_name, clinic_id in DELIVERIES:
            supply = supplies[sku]
            existing = session.exec(select(SupplyDelivery).where(
                SupplyDelivery.supply_id == supply.id,
                SupplyDelivery.quantity == quantity,
                SupplyDelivery.vendor_name == vendor_name,
                SupplyDelivery.clinic_id == clinic_id,
                SupplyDelivery.user_uuid == user_uuid,
            )).first()
            if existing is None:
                session.add(SupplyDelivery(
                    supply_id=supply.id, quantity=quantity, vendor_name=vendor_name,
                    clinic_id=clinic_id, user_uuid=user_uuid,
                ))
                session.flush()
                created_deliveries += 1

        for sku, quantity, consumption_type, clinic_id in CONSUMPTIONS:
            supply = supplies[sku]
            existing = session.exec(select(SupplyConsumption).where(
                SupplyConsumption.supply_id == supply.id,
                SupplyConsumption.quantity == quantity,
                SupplyConsumption.consumption_type == consumption_type,
                SupplyConsumption.clinic_id == clinic_id,
                SupplyConsumption.user_uuid == user_uuid,
            )).first()
            if existing is None:
                if stock_for_supply(session, supply.id) < quantity:
                    raise SeedError(f"Insufficient stock for seed consumption: {sku}")
                session.add(SupplyConsumption(
                    supply_id=supply.id, quantity=quantity, consumption_type=consumption_type,
                    clinic_id=clinic_id, user_uuid=user_uuid,
                ))
                session.flush()
                created_consumptions += 1
        session.commit()
    return created_supplies, created_deliveries, created_consumptions


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic development inventory")
    parser.add_argument("--user-id", required=True, help="ID of an existing TinyDB user")
    args = parser.parse_args()
    try:
        supplies, deliveries, consumptions = seed_inventory(args.user_id)
    except SeedError as exc:
        logger.error("Inventory seed failed: %s", exc)
        return 1
    except (OSError, ValueError, RuntimeError, SQLAlchemyError) as exc:
        logger.error("Inventory seed failed: %s", type(exc).__name__)
        return 1
    print(f"Created: {supplies} supplies, {deliveries} deliveries, {consumptions} consumptions")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())
