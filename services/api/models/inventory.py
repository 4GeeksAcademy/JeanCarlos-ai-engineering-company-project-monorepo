from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

PRODUCT_CATEGORIES = ("medication", "ppe", "consumable", "equipment")
DEPARTMENTS = ("primary_care", "general_consultation", "specialty_care", "chronic_care")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class MedicalSupply(SQLModel, table=True):
    __tablename__ = "medical_supply"
    __table_args__ = (
        CheckConstraint(
            "category IN ('ppe', 'wound_care', 'diagnostics', 'medications', 'consumables')",
            name="medical_supply_category_check",
        ),
        CheckConstraint("unit IN ('box', 'unit', 'pack', 'vial')", name="medical_supply_unit_check"),
        CheckConstraint("country IN ('US', 'UK')", name="medical_supply_country_check"),
        CheckConstraint(
            _in("product_category", PRODUCT_CATEGORIES), name="medical_supply_product_category_check"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str
    sku: str = Field(index=True, unique=True)
    category: str
    unit: str
    country: str
    # BRIEFING category; nullable only for rows created before it existed.
    product_category: str | None = None
    # Expiry lives on the product, not on the order (BRIEFING §6).
    expiry_date: date | None = None


class SupplyDelivery(SQLModel, table=True):
    __tablename__ = "supply_delivery"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="supply_delivery_quantity_check"),
        CheckConstraint("clinic_id BETWEEN 1 AND 12", name="supply_delivery_clinic_check"),
    )

    id: int | None = Field(default=None, primary_key=True)
    supply_id: int = Field(foreign_key="medical_supply.id", index=True)
    quantity: int
    vendor_name: str
    clinic_id: int
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_type=DateTime(timezone=True)
    )
    user_uuid: str


class SupplyConsumption(SQLModel, table=True):
    __tablename__ = "supply_consumption"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="supply_consumption_quantity_check"),
        CheckConstraint("clinic_id BETWEEN 1 AND 12", name="supply_consumption_clinic_check"),
        CheckConstraint(
            "consumption_type IN ('clinical_use', 'expiry_waste')",
            name="supply_consumption_type_check",
        ),
        CheckConstraint(_in("department", DEPARTMENTS), name="supply_consumption_department_check"),
    )

    id: int | None = Field(default=None, primary_key=True)
    supply_id: int = Field(foreign_key="medical_supply.id", index=True)
    quantity: int
    consumption_type: str
    # Clinical area, never a patient. Nullable only for rows created before it existed.
    department: str | None = None
    clinic_id: int
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_type=DateTime(timezone=True)
    )
    user_uuid: str


class ClinicStockMinimum(SQLModel, table=True):
    __tablename__ = "clinic_stock_minimum"
    __table_args__ = (
        UniqueConstraint("clinic_id", "supply_id", name="clinic_stock_minimum_unique"),
        CheckConstraint("min_quantity > 0", name="clinic_stock_minimum_quantity_check"),
        CheckConstraint("clinic_id BETWEEN 1 AND 12", name="clinic_stock_minimum_clinic_check"),
    )

    id: int | None = Field(default=None, primary_key=True)
    clinic_id: int
    supply_id: int = Field(foreign_key="medical_supply.id", index=True)
    min_quantity: int
