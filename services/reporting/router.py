"""GET/POST /reporting/*: HTTP, auth and validation only.

Every query and the flow itself are imported from data/pipelines/; no ETL logic here.
"""

import logging
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from services.api.core.dependencies import CurrentUser
from services.api.models.auth import Role, User
from services.api.routes.telemetry import user_pseudonym
from data.pipelines import run_log
from data.pipelines.connections import MissingConnection, reporting_reader_engine
from data.pipelines.pipeline import (
    PIPELINE_NAME,
    RunInProgress,
    get_monthly_clinic_supply_performance,
    trigger_run,
)

from .schemas import MonthlyClinicSupplyPerformance, PipelineRun, PipelineRunAccepted, PipelineRunRequest

router = APIRouter(prefix="/reporting", tags=["reporting"])
logger = logging.getLogger("api.reporting")
_UNAVAILABLE = (MissingConnection, SQLAlchemyError)


def require_pipeline_operator(user: CurrentUser) -> User:
    if user.role not in {Role.admin, Role.manager}:
        raise HTTPException(status_code=403, detail="Manager or administrator role required")
    return user


def _unavailable(error: Exception) -> HTTPException:
    logger.error("Reporting storage failed: %s", type(error).__name__)
    return HTTPException(status_code=503, detail="Reporting database unavailable")


def _first_of_month(value: date, field: str) -> date:
    if value.day != 1:
        raise HTTPException(status_code=422, detail=f"{field} must be the first day of a month (YYYY-MM-01)")
    return value


@router.get("/monthly-clinic-supply-performance", response_model=MonthlyClinicSupplyPerformance)
def monthly_clinic_supply_performance(
    _user: CurrentUser,
    month_start: Annotated[date | None, Query(description="YYYY-MM-01; default: latest computed month")] = None,
) -> dict:
    if month_start is not None:
        _first_of_month(month_start, "month_start")
    try:
        report = get_monthly_clinic_supply_performance(month_start, reporting_reader_engine())
    except _UNAVAILABLE as error:
        raise _unavailable(error) from error
    if report is None:
        # A month without a completed run is "not computed", never a row of zeros.
        raise HTTPException(status_code=404, detail="No computed report for that month")
    return report


@router.get("/pipeline-runs/latest", response_model=PipelineRun)
def latest_pipeline_run(_user: CurrentUser) -> dict:
    try:
        run = run_log.get_latest_run(reporting_reader_engine(), PIPELINE_NAME)
    except _UNAVAILABLE as error:
        raise _unavailable(error) from error
    if run is None:
        raise HTTPException(status_code=404, detail="The pipeline has not run yet")
    return run


@router.post("/pipeline-runs", response_model=PipelineRunAccepted, status_code=status.HTTP_202_ACCEPTED,
             responses={409: {"description": "A run is already in progress"}})
def start_pipeline_run(
    user: Annotated[User, Depends(require_pipeline_operator)],
    payload: Annotated[PipelineRunRequest | None, Body()] = None,
) -> dict:
    months = [_first_of_month(month, "months[]") for month in (payload.months if payload and payload.months else [])]
    today = datetime.now(timezone.utc).date()
    if any(month > today for month in months):
        raise HTTPException(status_code=422, detail="months cannot be in the future")
    try:
        accepted = trigger_run(months or None, triggered_by=user_pseudonym(user.id), engine=reporting_reader_engine())
    except RunInProgress as running:
        raise HTTPException(status_code=409, detail=f"Run {running.run_id} is already in progress") from running
    except _UNAVAILABLE as error:
        raise _unavailable(error) from error
    logger.info("Manual pipeline run accepted run_id=%s mode=%s", accepted["run_id"], accepted["mode"])
    return accepted
