from datetime import datetime
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


class ProductCreate(SupplyFields):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SupplySummary(SupplyFields):
    id: int


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


class SupplyConsumptionCreate(MovementCreate):
    consumption_type: Literal["clinical_use", "expiry_waste"]


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


class SupplyConsumptionResponse(MovementResponse):
    consumption_type: Literal["clinical_use", "expiry_waste"]


class OrdersResponse(BaseModel):
    inbound: list[SupplyDeliveryResponse]
    outbound: list[SupplyConsumptionResponse]
