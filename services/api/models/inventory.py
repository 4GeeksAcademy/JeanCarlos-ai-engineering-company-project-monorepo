from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime
from sqlmodel import Field, SQLModel


class MedicalSupply(SQLModel, table=True):
    __tablename__ = "medical_supply"
    __table_args__ = (
        CheckConstraint(
            "category IN ('ppe', 'wound_care', 'diagnostics', 'medications', 'consumables')",
            name="medical_supply_category_check",
        ),
        CheckConstraint("unit IN ('box', 'unit', 'pack', 'vial')", name="medical_supply_unit_check"),
        CheckConstraint("country IN ('US', 'UK')", name="medical_supply_country_check"),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str
    sku: str = Field(index=True, unique=True)
    category: str
    unit: str
    country: str


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
    )

    id: int | None = Field(default=None, primary_key=True)
    supply_id: int = Field(foreign_key="medical_supply.id", index=True)
    quantity: int
    consumption_type: str
    clinic_id: int
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_type=DateTime(timezone=True)
    )
    user_uuid: str
