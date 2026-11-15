from datetime import date
from enum import Enum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class SupplierStatus(str, Enum):
    active = "active"
    suspended = "suspended"


class Country(str, Enum):
    USA = "USA"
    UK = "UK"


class Currency(str, Enum):
    USD = "USD"
    GBP = "GBP"


class Category(str, Enum):
    medical_supplies = "medical_supplies"
    laboratory_services = "laboratory_services"
    pharmaceutical = "pharmaceutical"
    clinical_software = "clinical_software"
    it_infrastructure = "it_infrastructure"
    hr_and_payroll_software = "hr_and_payroll_software"
    cleaning_and_facilities = "cleaning_and_facilities"
    patient_communication = "patient_communication"
    billing_and_coding_software = "billing_and_coding_software"
    training_platforms = "training_platforms"


class ComplianceAgreement(str, Enum):
    BAA = "BAA"
    DPA = "DPA"
    both = "both"


class SupplierInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SupplierRateUpdate(SupplierInput):
    monthly_rate: float = Field(gt=0, allow_inf_nan=False, strict=True)


class SupplierStatusUpdate(SupplierInput):
    status: SupplierStatus


class SupplierCreate(SupplierRateUpdate):
    name: str = Field(min_length=1, strict=True)
    country: Country
    categories: list[Category] = Field(min_length=1)
    currency: Currency
    status: SupplierStatus
    compliance_agreement: ComplianceAgreement | None = None
    contract_renewal_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    contact_email: EmailStr | None = None
    notes: str | None = None

    @field_validator("contract_renewal_date")
    @classmethod
    def validate_renewal_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_currency(self) -> "SupplierCreate":
        expected = Currency.USD if self.country == Country.USA else Currency.GBP
        if self.currency != expected:
            raise ValueError(f"{self.country.value} requires currency {expected.value}")
        return self


class SupplierResponse(SupplierCreate):
    id: int
    updated_at: AwareDatetime
