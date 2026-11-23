import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select
from tinydb import Query

from .database import get_inventory_engine, open_database
from .models.inventory import ClinicStockMinimum, MedicalSupply, SupplyConsumption, SupplyDelivery
from .services.inventory import stock_for_supply, upgrade_inventory_schema

logger = logging.getLogger(__name__)

SUPPLIES = [
    {"name": "Guantes de nitrilo (caja de 100)", "sku": "HCR-PPE-001", "category": "ppe", "unit": "box", "country": "US", "product_category": "ppe"},
    {"name": "Mascarilla quirúrgica (pack de 50)", "sku": "HCR-PPE-002", "category": "ppe", "unit": "pack", "country": "UK", "product_category": "ppe"},
    {"name": "Apósito adhesivo para heridas", "sku": "HCR-WND-001", "category": "wound_care", "unit": "box", "country": "US", "product_category": "consumable"},
    {"name": "Test rápido de estreptococo", "sku": "HCR-DIAG-001", "category": "diagnostics", "unit": "unit", "country": "US", "product_category": "consumable"},
    {"name": "Tiras reactivas glucemia (50)", "sku": "HCR-DIAG-002", "category": "diagnostics", "unit": "box", "country": "UK", "product_category": "consumable"},
    {"name": "Solución salina 0,9% 500ml", "sku": "HCR-MED-001", "category": "medications", "unit": "vial", "country": "US", "product_category": "medication", "expiry_in_days": 180},
    {"name": "Vacuna antigripal (dosis)", "sku": "HCR-MED-002", "category": "medications", "unit": "vial", "country": "UK", "product_category": "medication", "expiry_in_days": 20},
    {"name": "Paracetamol 500 mg (caja)", "sku": "HCR-MED-003", "category": "medications", "unit": "box", "country": "US", "product_category": "medication", "expiry_in_days": 400},
    {"name": "Pulsioxímetro de dedo", "sku": "HCR-EQP-001", "category": "diagnostics", "unit": "unit", "country": "US", "product_category": "equipment"},
    {"name": "Tensiómetro digital", "sku": "HCR-EQP-002", "category": "diagnostics", "unit": "unit", "country": "UK", "product_category": "equipment"},
]
BASE_FIELDS = ("name", "sku", "category", "unit", "country")

# Synthetic development movements, not records of actual clinical activity.
# Clinics 1 and 2 are in the US; 10 and 11 in the UK.
DELIVERIES = [
    ("HCR-PPE-001", 120, "MedLine Industries", 1),
    ("HCR-PPE-001", 70, "Bound Tree Medical", 2),
    ("HCR-PPE-002", 90, "Cardinal Health UK", 10),
    ("HCR-DIAG-002", 60, "Cardinal Health UK", 11),
    ("HCR-WND-001", 40, "MedLine Industries", 1),
    ("HCR-WND-001", 25, "MedLine Industries", 2),
    ("HCR-DIAG-001", 30, "Bound Tree Medical", 1),
    ("HCR-MED-001", 50, "McKesson", 1),
    ("HCR-MED-001", 30, "McKesson", 2),
    ("HCR-MED-002", 40, "Cardinal Health UK", 10),
    ("HCR-MED-003", 60, "McKesson", 1),
    ("HCR-MED-003", 20, "McKesson", 2),
    ("HCR-EQP-001", 8, "Bound Tree Medical", 1),
    ("HCR-EQP-002", 6, "Cardinal Health UK", 10),
    ("HCR-PPE-002", 40, "Cardinal Health UK", 11),
    ("HCR-DIAG-002", 30, "Cardinal Health UK", 11),
]
# Consumptions are recorded per department, never per patient.
CONSUMPTIONS = [
    ("HCR-PPE-001", 30, "clinical_use", 1, "primary_care"),
    ("HCR-PPE-001", 5, "expiry_waste", 2, "general_consultation"),
    ("HCR-PPE-002", 10, "clinical_use", 10, "primary_care"),
    ("HCR-PPE-001", 40, "clinical_use", 1, "general_consultation"),
    ("HCR-PPE-001", 20, "clinical_use", 2, "chronic_care"),
    ("HCR-PPE-002", 25, "clinical_use", 10, "specialty_care"),
    ("HCR-WND-001", 28, "clinical_use", 1, "general_consultation"),
    ("HCR-WND-001", 10, "clinical_use", 2, "primary_care"),
    ("HCR-DIAG-001", 12, "clinical_use", 1, "primary_care"),
    ("HCR-MED-001", 20, "clinical_use", 1, "chronic_care"),
    ("HCR-MED-001", 18, "clinical_use", 2, "chronic_care"),
    ("HCR-MED-002", 15, "clinical_use", 10, "primary_care"),
    ("HCR-MED-003", 25, "clinical_use", 1, "general_consultation"),
    ("HCR-EQP-001", 2, "clinical_use", 1, "specialty_care"),
    ("HCR-DIAG-002", 20, "clinical_use", 11, "chronic_care"),
    ("HCR-PPE-002", 10, "clinical_use", 11, "primary_care"),
]
# Per-clinic minimums. Two pairs are left 2 units above their minimum, so one
# outbound order of 3 units triggers stock_threshold_triggered:
# HCR-WND-001 at clinic 1 (12 units, minimum 10) and HCR-MED-001 at clinic 2 (12, minimum 10).
MINIMUMS = [
    (1, "HCR-WND-001", 10),
    (2, "HCR-MED-001", 10),
    (1, "HCR-PPE-001", 20),
    (10, "HCR-PPE-002", 20),
    (10, "HCR-MED-002", 10),
    (1, "HCR-EQP-001", 3),
]


class SeedError(Exception):
    pass


def seed_inventory(user_uuid: str) -> tuple[int, int, int, int]:
    with open_database() as database:
        if database.table("users").get(Query().id == user_uuid) is None:
            raise SeedError("The specified TinyDB user does not exist")

    engine = get_inventory_engine()
    upgrade_inventory_schema(engine)
    created_supplies = created_deliveries = created_consumptions = created_minimums = 0
    today = datetime.now(timezone.utc).date()
    with Session(engine) as session:
        supplies = {}
        for spec in SUPPLIES:
            data = {field: spec[field] for field in BASE_FIELDS}
            expiry = today + timedelta(days=spec["expiry_in_days"]) if "expiry_in_days" in spec else None
            supply = session.exec(
                select(MedicalSupply).where(MedicalSupply.sku == data["sku"]).with_for_update()
            ).first()
            if supply is None:
                supply = MedicalSupply(**data, product_category=spec["product_category"], expiry_date=expiry)
                session.add(supply)
                session.flush()
                created_supplies += 1
            elif any(getattr(supply, field) != value for field, value in data.items()) or (
                supply.product_category not in (None, spec["product_category"])
            ):
                raise SeedError(f"Existing supply differs from seed: {data['sku']}")
            else:
                # Fill fields added after the supply was first seeded; never overwrite them.
                supply.product_category = spec["product_category"]
                if supply.expiry_date is None:
                    supply.expiry_date = expiry
                session.add(supply)
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

        for sku, quantity, consumption_type, clinic_id, department in CONSUMPTIONS:
            supply = supplies[sku]
            existing = session.exec(select(SupplyConsumption).where(
                SupplyConsumption.supply_id == supply.id,
                SupplyConsumption.quantity == quantity,
                SupplyConsumption.consumption_type == consumption_type,
                SupplyConsumption.clinic_id == clinic_id,
                SupplyConsumption.user_uuid == user_uuid,
            )).first()
            if existing is None:
                if stock_for_supply(session, supply.id, clinic_id) < quantity:
                    raise SeedError(f"Insufficient clinic stock for seed consumption: {sku}")
                session.add(SupplyConsumption(
                    supply_id=supply.id, quantity=quantity, consumption_type=consumption_type,
                    department=department, clinic_id=clinic_id, user_uuid=user_uuid,
                ))
                session.flush()
                created_consumptions += 1
            elif existing.department is None:
                existing.department = department
                session.add(existing)

        for clinic_id, sku, min_quantity in MINIMUMS:
            supply = supplies[sku]
            existing = session.exec(select(ClinicStockMinimum).where(
                ClinicStockMinimum.clinic_id == clinic_id,
                ClinicStockMinimum.supply_id == supply.id,
            )).first()
            if existing is None:
                session.add(ClinicStockMinimum(
                    clinic_id=clinic_id, supply_id=supply.id, min_quantity=min_quantity,
                ))
                created_minimums += 1
        session.commit()
    return created_supplies, created_deliveries, created_consumptions, created_minimums


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic development inventory")
    parser.add_argument("--user-id", required=True, help="ID of an existing TinyDB user")
    args = parser.parse_args()
    try:
        supplies, deliveries, consumptions, minimums = seed_inventory(args.user_id)
    except SeedError as exc:
        logger.error("Inventory seed failed: %s", exc)
        return 1
    except (OSError, ValueError, RuntimeError, SQLAlchemyError) as exc:
        logger.error("Inventory seed failed: %s", type(exc).__name__)
        return 1
    print(
        f"Created: {supplies} supplies, {deliveries} deliveries, "
        f"{consumptions} consumptions, {minimums} clinic minimums"
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())
