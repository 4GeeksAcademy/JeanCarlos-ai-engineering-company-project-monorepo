from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClinicSupplyPerformance(BaseModel):
    """One clinic in the BRIEFING §6 contract."""

    clinic_id: str
    country: Literal["US", "UK"]
    total_supply_cost: float
    supply_consumption_count: int
    critical_stockout_count: int
    expiry_risk_count: int
    currency: Literal["USD", "GBP"]


class MonthlyClinicSupplyPerformance(BaseModel):
    """BRIEFING §6: month_start + clinics[]; the rest is audit context from the design (§8)."""

    month_start: date
    clinics: list[ClinicSupplyPerformance]
    computed_at: datetime
    last_run_id: str | None
    revised: bool
    coverage_checked: bool
    coverage_warnings: list[str]


class PipelineRun(BaseModel):
    run_id: str
    pipeline_name: str
    flow_run_id: str | None
    status: Literal["running", "completed", "failed", "skipped_locked"]
    trigger: Literal["scheduled", "manual", "backfill"]
    started_at: datetime
    finished_at: datetime | None
    months: list[date]
    source_events_read: int
    rows_upserted: int
    rows_changed: int
    events_skipped: dict[str, int]
    warnings: list[str]
    error_stage: str | None
    error_message: str | None
    code_version: str | None


class PipelineRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    months: list[date] | None = Field(default=None, min_length=1, max_length=24)


class PipelineRunAccepted(BaseModel):
    run_id: str
    flow_run_id: str | None
    status: Literal["accepted"] = "accepted"
    mode: Literal["deployment", "in_process"]
