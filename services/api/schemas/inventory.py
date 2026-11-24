from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SupplyFields(BaseModel):
    name: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    category: Literal["ppe", "wound_care", "diagnostics", "medications", "consumables"]
    unit: Literal["box", "unit", "pack", "vial"]
    country: Literal["US", "UK"]


ProductCategory = Literal["medication", "ppe", "consumable", "equipment"]
Department = Literal["primary_care", "general_consultation", "specialty_care", "chronic_care"]


class ProductCreate(SupplyFields):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_category: ProductCategory
    expiry_date: date | None = None


class SupplySummary(SupplyFields):
    id: int
    # Null only for supplies created before the BRIEFING category existed.
    product_category: ProductCategory | None
    expiry_date: date | None


class ProductResponse(SupplySummary):
    current_stock: int


class ProductListItem(ProductResponse):
    pass


class MovementCreate(InputModel):
    supply_id: int = Field(gt=0, strict=True)
    quantity: int = Field(gt=0, le=2_147_483_647, strict=True)
    clinic_id: int = Field(ge=1, le=12, strict=True)


class SupplyDeliveryCreate(MovementCreate):
    vendor_name: str = Field(min_length=1)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class SupplyConsumptionCreate(MovementCreate):
    consumption_type: Literal["clinical_use", "expiry_waste"]
    department: Department


class MovementResponse(BaseModel):
    id: int
    supply_id: int
    quantity: int
    clinic_id: int
    created_at: datetime
    user_uuid: str
    supply: SupplySummary


class SupplyDeliveryResponse(MovementResponse):
    vendor_name: str
    # Plain JSON numbers (2 decimals), not Decimal strings, so the backoffice can copy
    # total_cost (quantity × unit_cost, clinic currency) into inbound_order_created
    # (contract 2.1.0) without parsing.
    unit_cost: float | None = None
    total_cost: float | None = None


class SupplyConsumptionResponse(MovementResponse):
    consumption_type: Literal["clinical_use", "expiry_waste"]
    department: Department | None


class StockThresholdCrossing(BaseModel):
    """Present only when this outbound order took the clinic below its configured minimum."""

    clinic_stock: int
    min_stock_threshold: int


class SupplyConsumptionCreated(SupplyConsumptionResponse):
    stock_threshold: StockThresholdCrossing | None


class ExpiringSupply(BaseModel):
    supply: SupplySummary
    clinic_id: int
    quantity: int
    expiry_date: date
    days_until_expiry: int


class OrdersResponse(BaseModel):
    inbound: list[SupplyDeliveryResponse]
    outbound: list[SupplyConsumptionResponse]
