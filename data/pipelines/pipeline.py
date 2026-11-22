"""Resilient business performance pipeline: monthly_clinic_supply_performance.

Implements data/pipelines/PIPELINE_DESIGN.md (Part 1). It reads the 4 BRIEFING
supply events from public.telemetry_events (read-only) and upserts one row per
clinic and month into reporting.monthly_clinic_supply_performance.

    extract_supply_events ─┐
    extract_inventory_counts (optional) ─┤
                           ├─> transform_monthly_clinic_kpis (cached 1 h)
                           ├─> validate_kpis
                           ├─> load_monthly_clinic_supply_performance (idempotent upsert)
                           └─> export_eval_snapshot (optional)
    run log: reporting.pipeline_runs / pipeline_run_months

Run it from the repository root (planned cadence: daily at 05:00 UTC, which keeps
the current month fresh and absorbs late events of the previous one, so the board
report is ready on the first business day of the month):

    python data/pipelines/pipeline.py                          # current + previous month (scheduled run)
    python data/pipelines/pipeline.py --month 2026-11-01       # one month (manual run)
    python data/pipelines/pipeline.py --backfill 2026-01-01 2026-06-01

Connections: REPORTING_PIPELINE_DATABASE_URL (role reporting_pipeline, Supabase
session pooler, port 5432) and, for the optional reconciliation,
INVENTORY_READONLY_DATABASE_URL or DATABASE_URL. See data/pipelines/connections.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # `python data/pipelines/pipeline.py`: make `data.*` importable.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import hashlib
import json
import logging
import os
import subprocess
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pandas as pd
from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE
from prefect.context import TaskRunContext
from prefect.runtime import flow_run
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from data.pipelines import run_log
from data.pipelines.connections import REPO_ROOT, inventory_engine, pipeline_engine, reporting_reader_engine
from data.pipelines.tables import (
    monthly_clinic_supply_performance as destination,
    pipeline_run_months,
    revisions,
    telemetry_events,
)
from data.process import supply_kpis
from data.process.supply_kpis import KPI_COLUMNS, SOURCE_EVENT_TYPES, KpiValidationError

PIPELINE_NAME = "monthly_clinic_supply_performance"
# Prefect tag with a concurrency limit of 1 (`prefect concurrency-limit create reporting-monthly-supply 1`).
CONCURRENCY_TAG = "reporting-monthly-supply"
CONFIG_VARIABLE = "monthly_clinic_supply_config"
DEFAULT_CONFIG = {
    "late_window_months": 1,
    "expected_clinics": 12,
    "coverage_tolerance": 0.05,
    "clinic_registry_path": "data/raw/clinics.csv",
}
EVAL_DIR = REPO_ROOT / "data" / "eval" / PIPELINE_NAME
MAX_MONTHS_PER_RUN = 24

# Retries for tasks that talk to a database (Supabase pooler or inventory Postgres):
# 3 retries waiting 10 s, 30 s and 90 s (~2 min in total). That covers the usual
# transient failures (pooler restart or failover, dropped connection, brief network
# loss), which recover in seconds to a minute. More retries would only delay the
# 05:00 UTC run during a real outage, which needs a human anyway. Every retried task
# is safe to repeat: extraction is read-only and the load is one idempotent transaction.
DB_RETRIES = 3
DB_RETRY_DELAYS = [10, 30, 90]

_TRANSIENT_ERRORS = (OperationalError, InterfaceError, ConnectionError, TimeoutError)


class RunInProgress(RuntimeError):
    """A run of this pipeline is already `running` (POST /reporting/pipeline-runs → 409)."""

    def __init__(self, run_id: str):
        super().__init__(f"Run {run_id} is still running")
        self.run_id = run_id


class StageError(RuntimeError):
    """A critical stage failed; carries the stage for pipeline_runs.error_stage."""

    def __init__(self, stage: str, error: BaseException):
        super().__init__(run_log.describe_error(error))
        self.stage = stage


def retry_on_transient_errors(_task: Any, _task_run: Any, state: Any) -> bool:
    """Retry connection problems only. A SQL or permission error will fail the same way again."""
    try:
        state.result()
    except _TRANSIENT_ERRORS:
        return True
    except DBAPIError as error:
        return bool(error.connection_invalidated)
    except Exception:
        return False
    return False


# --- helpers ------------------------------------------------------------------------

def first_of_month(value: date) -> date:
    return value.replace(day=1)


def add_months(month: date, offset: int) -> date:
    index = month.year * 12 + month.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def resolve_months(months: list[date] | None, late_window_months: int, today: date | None = None) -> list[date]:
    """Scheduled run: the current month plus `late_window_months` previous ones (late events)."""
    if months:
        resolved = sorted({first_of_month(month) for month in months})
    else:
        current = first_of_month(today or datetime.now(timezone.utc).date())
        resolved = [add_months(current, -offset) for offset in range(late_window_months, -1, -1)]
    if len(resolved) > MAX_MONTHS_PER_RUN:
        raise ValueError(f"A run covers at most {MAX_MONTHS_PER_RUN} months")
    return resolved


def load_config() -> dict[str, Any]:
    """Prefect Variable `monthly_clinic_supply_config` (JSON) over the design defaults."""
    try:
        from prefect.variables import Variable

        override = Variable.get(CONFIG_VARIABLE, default=None)
    except Exception:
        override = None
    return {**DEFAULT_CONFIG, **(override if isinstance(override, dict) else {})}


def code_version() -> str:
    if os.environ.get("CODE_VERSION"):
        return os.environ["CODE_VERSION"]
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# Hash of the transformation code: a change in data/process/supply_kpis.py invalidates the cache.
_TRANSFORM_CODE_HASH = hashlib.sha256(Path(supply_kpis.__file__).read_bytes()).hexdigest()[:16]


def transform_cache_key(_context: TaskRunContext, parameters: dict[str, Any]) -> str:
    """Cache key of transform_monthly_clinic_kpis: month + set of event ids + registry + code.

    telemetry_events is append-only (a trigger blocks UPDATE/DELETE), so the same set of
    event ids always means the same event contents. A late event changes the id set and
    therefore the key: the cache never hides new data.
    """
    events: pd.DataFrame = parameters["events"]
    digest = hashlib.sha256()
    digest.update(f"{_TRANSFORM_CODE_HASH}|{parameters['month_start']}|".encode())
    digest.update(parameters["clinics"].to_csv(index=False).encode())
    digest.update("\n".join(sorted(events["id"].astype(str))).encode() if not events.empty else b"<empty>")
    return f"{PIPELINE_NAME}-transform-{digest.hexdigest()}"


# --- tasks --------------------------------------------------------------------------

@task(name="acquire_run_lock", retries=DB_RETRIES, retry_delay_seconds=DB_RETRY_DELAYS,
      retry_condition_fn=retry_on_transient_errors, cache_policy=NO_CACHE, persist_result=False)
def acquire_run_lock(engine: Engine) -> Connection | None:
    """Session advisory lock: a second concurrent run (cron + manual) gets None and writes nothing."""
    connection = engine.connect()
    try:
        acquired = connection.execute(
            select(func.pg_try_advisory_lock(func.hashtext(PIPELINE_NAME)))
        ).scalar()
        connection.commit()  # the session lock survives; do not stay idle in a transaction
    except Exception:
        connection.close()
        raise
    if not acquired:
        connection.close()
        return None
    return connection


@task(name="extract_supply_events", retries=DB_RETRIES, retry_delay_seconds=DB_RETRY_DELAYS,
      retry_condition_fn=retry_on_transient_errors, cache_policy=NO_CACHE, persist_result=False)
def extract_supply_events(engine: Engine, month_start: date) -> pd.DataFrame:
    """Read-only: the 4 BRIEFING event types in [month_start, next month). Never the whole table."""
    statement = select(
        telemetry_events.c.id, telemetry_events.c.timestamp, telemetry_events.c.event_type, telemetry_events.c.tags,
    ).where(
        telemetry_events.c.event_type.in_(SOURCE_EVENT_TYPES),
        telemetry_events.c.timestamp >= datetime.combine(month_start, datetime.min.time(), timezone.utc),
        telemetry_events.c.timestamp < datetime.combine(add_months(month_start, 1), datetime.min.time(), timezone.utc),
    )
    with engine.connect() as connection:
        connection.execute(text("set transaction read only"))
        frame = pd.read_sql(statement, connection)
    get_run_logger().info("%s: %d source events", month_start, len(frame))
    return frame


# Optional: 2 retries of 5 s. The reconciliation only adds coverage warnings; if the
# inventory database stays down, the run goes on without coverage instead of waiting.
@task(name="extract_inventory_counts", retries=2, retry_delay_seconds=5,
      retry_condition_fn=retry_on_transient_errors, cache_policy=NO_CACHE, persist_result=False)
def extract_inventory_counts(month_start: date) -> pd.DataFrame:
    """Inventory movements per clinic in the month (reconciliation only, never KPI input)."""
    query = text(
        """
        select clinic_id, 'inbound' as movement, count(*) as movements from supply_delivery
         where created_at >= :start and created_at < :end group by clinic_id
        union all
        select clinic_id, 'outbound' as movement, count(*) as movements from supply_consumption
         where created_at >= :start and created_at < :end group by clinic_id
        """
    )
    window = {
        "start": datetime.combine(month_start, datetime.min.time(), timezone.utc),
        "end": datetime.combine(add_months(month_start, 1), datetime.min.time(), timezone.utc),
    }
    with inventory_engine().connect() as connection:
        connection.execute(text("set transaction read only"))
        long = pd.read_sql(query, connection, params=window)
    counts = long.pivot_table(index="clinic_id", columns="movement", values="movements", aggfunc="sum", fill_value=0)
    return counts.reindex(columns=["inbound", "outbound"], fill_value=0).astype(int).reset_index()


# Cached: this is the expensive step (JSON parsing of every event's tags, deduplication and
# registry join). A manual run shortly after the scheduled one, or a Prefect retry, reuses the
# result instead of recomputing it. The key (transform_cache_key) includes the event ids,
# so the cache is only reused for exactly the same source data. It is valid for 1 hour:
# long enough to cover retries and back-to-back runs, short enough that a code or registry
# change deployed without restarting Prefect never serves stale KPIs for long.
@task(name="normalize_supply_events", cache_key_fn=transform_cache_key,
      cache_expiration=timedelta(hours=1), persist_result=True)
def normalize_supply_events(events: pd.DataFrame, clinics: pd.DataFrame, month_start: date) -> dict[str, Any]:
    """Dedupe by event id, extract tags, UTC month, clinic registry join (no retries: deterministic)."""
    normalized, skipped = supply_kpis.normalize_events(events, clinics)
    return {
        "events": normalized,
        "events_skipped": skipped,
        "source_event_counts": supply_kpis.source_event_counts(normalized, month_start),
        "consumption_by_department": supply_kpis.consumption_by_department(normalized, month_start),
    }


# One task per BRIEFING KPI (§2). Deterministic and cheap once events are normalized:
# no retries (a failure is a data problem) and no cache (normalize_supply_events already is).
@task(name="compute_total_supply_cost", cache_policy=NO_CACHE, persist_result=False)
def compute_total_supply_cost(events: pd.DataFrame, month_start: date) -> pd.Series:
    """Costo de insumos por clínica."""
    return supply_kpis.total_supply_cost(events, month_start)


@task(name="compute_supply_consumption_count", cache_policy=NO_CACHE, persist_result=False)
def compute_supply_consumption_count(events: pd.DataFrame, month_start: date) -> pd.Series:
    """Volumen de consumo de insumos."""
    return supply_kpis.supply_consumption_count(events, month_start)


@task(name="compute_critical_stockout_count", cache_policy=NO_CACHE, persist_result=False)
def compute_critical_stockout_count(events: pd.DataFrame, month_start: date) -> pd.Series:
    """Frecuencia de quiebre crítico."""
    return supply_kpis.critical_stockout_count(events, month_start)


@task(name="compute_expiry_risk_count", cache_policy=NO_CACHE, persist_result=False)
def compute_expiry_risk_count(events: pd.DataFrame, month_start: date) -> pd.Series:
    """Conteo de riesgo de vencimiento."""
    return supply_kpis.expiry_risk_count(events, month_start)


@task(name="assemble_monthly_clinic_kpis", cache_policy=NO_CACHE, persist_result=False)
def assemble_monthly_clinic_kpis(
    clinics: pd.DataFrame,
    month_start: date,
    total_supply_cost: pd.Series,
    supply_consumption_count: pd.Series,
    critical_stockout_count: pd.Series,
    expiry_risk_count: pd.Series,
) -> pd.DataFrame:
    """12 destination rows (BRIEFING §5 columns), clinics without events at zero."""
    return supply_kpis.assemble_monthly_kpis(
        clinics, month_start, total_supply_cost, supply_consumption_count, critical_stockout_count, expiry_risk_count,
    )


@task(name="validate_kpis", cache_policy=NO_CACHE, persist_result=False)
def validate_kpis(
    kpis: pd.DataFrame,
    clinics: pd.DataFrame,
    month_start: date,
    events: pd.DataFrame,
    inventory_counts: pd.DataFrame | None,
    coverage_tolerance: float,
) -> dict[str, Any] | None:
    """Integrity (blocks the load) + coverage against inventory (warns, never blocks)."""
    supply_kpis.validate_kpis(kpis, clinics, month_start)
    if inventory_counts is None:
        return None
    return supply_kpis.coverage_report(events, inventory_counts, clinics, month_start, coverage_tolerance)


def _comparable(row: dict[str, Any]) -> tuple:
    return (
        Decimal(str(row["total_supply_cost"])).quantize(Decimal("0.01")),
        int(row["supply_consumption_count"]),
        int(row["critical_stockout_count"]),
        int(row["expiry_risk_count"]),
        row["country"],
        row["currency"],
    )


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "country": row["country"], "currency": row["currency"],
        "total_supply_cost": float(row["total_supply_cost"]),
        **{column: int(row[column]) for column in KPI_COLUMNS[1:]},
    }


@task(name="load_monthly_clinic_supply_performance", retries=DB_RETRIES, retry_delay_seconds=DB_RETRY_DELAYS,
      retry_condition_fn=retry_on_transient_errors, cache_policy=NO_CACHE, persist_result=False,
      tags=[CONCURRENCY_TAG])
def load_monthly_clinic_supply_performance(
    engine: Engine,
    kpis: pd.DataFrame,
    run_id: UUID,
    month_start: date,
    trigger: str,
    checkpoint: dict[str, Any],
) -> dict[str, int]:
    """Idempotent load of one month in ONE transaction: revisions + upsert + checkpoint.

    The upsert relies on the BRIEFING constraint unique (clinic_id, month_start), so a
    clinic/month can never be duplicated. `WHERE ... IS DISTINCT FROM` skips rows whose
    values did not change: a second run over the same data leaves the table identical
    (same values and same computed_at) and reports rows_changed = 0.
    """
    rows = kpis.to_dict(orient="records")
    computed_at = datetime.now(timezone.utc)
    with engine.begin() as connection:
        previous_counts = run_log.last_completed_event_counts(connection, month_start)
        existing = {
            row.clinic_id: dict(row._mapping)
            for row in connection.execute(
                select(destination).where(destination.c.month_start == month_start).with_for_update()
            )
        }
        changed = [row for row in rows if row["clinic_id"] in existing
                   and _comparable(existing[row["clinic_id"]]) != _comparable(row)]
        if trigger == "backfill":
            reason = "backfill"
        elif previous_counts is not None and previous_counts != checkpoint["source_event_counts"]:
            reason = "late_events"
        else:
            reason = "recompute"
        if changed:
            connection.execute(insert(revisions), [
                {"run_id": str(run_id), "clinic_id": row["clinic_id"], "month_start": month_start, "reason": reason,
                 "previous": _jsonable(existing[row["clinic_id"]]), "current": _jsonable(row)}
                for row in changed
            ])

        statement = insert(destination).values([{**row, "computed_at": computed_at} for row in rows])
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[destination.c.clinic_id, destination.c.month_start],
            set_={
                "country": excluded.country,
                **{column: excluded[column] for column in KPI_COLUMNS},
                "currency": excluded.currency,
                "computed_at": excluded.computed_at,
            },
            where=or_(*[
                destination.c[column].is_distinct_from(excluded[column])
                for column in (*KPI_COLUMNS, "country", "currency")
            ]),
        )
        # RETURNING lists only inserted rows and rows the WHERE let through (really changed);
        # cursor.rowcount is not reliable for this statement with psycopg.
        rows_changed = len(connection.execute(statement.returning(destination.c.clinic_id)).all())

        connection.execute(
            insert(pipeline_run_months)
            .values(run_id=str(run_id), month_start=month_start, status="completed", rows_changed=rows_changed,
                    **checkpoint)
            .on_conflict_do_nothing()  # a retry after a lost commit acknowledgement
        )
    return {"rows_upserted": len(rows), "rows_changed": rows_changed}


@task(name="export_eval_snapshot", cache_policy=NO_CACHE, persist_result=False)
def export_eval_snapshot(run_id: UUID, month_start: date, result: dict[str, Any], coverage: dict[str, Any] | None) -> str:
    """Optional: JSON snapshot in data/eval/ for validation and the board-pack review."""
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = EVAL_DIR / f"{month_start.isoformat()}.json"
    snapshot = {
        "run_id": str(run_id),
        "month_start": month_start.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "clinics": json.loads(result["kpis"].to_json(orient="records", date_format="iso")),
        "consumption_by_department": result["consumption_by_department"],
        "source_event_counts": result["source_event_counts"],
        "events_skipped": result["events_skipped"],
        "coverage": coverage,
    }
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return str(path)


# --- subflows ---------------------------------------------------------------------------
# Each phase of the design is its own flow: it can run, be monitored and be retried on its
# own, and the main flow only coordinates them. Inputs and outputs are explicit parameters
# and return values; connections are resolved inside each subflow (connections.py), never
# shared through globals.

@flow(name="extract_supply_events_flow")
def extract_supply_events_flow(month_start: date) -> pd.DataFrame:
    """Extraction: the 4 BRIEFING supply events of one month from public.telemetry_events (read-only)."""
    return extract_supply_events(pipeline_engine(), month_start)


@flow(name="reconcile_inventory_movements_flow")
def reconcile_inventory_movements_flow(month_start: date) -> pd.DataFrame:
    """Optional extraction: inventory movements per clinic (supply_delivery, supply_consumption)."""
    return extract_inventory_counts(month_start)


@flow(name="compute_monthly_clinic_supply_kpis_flow")
def compute_monthly_clinic_supply_kpis_flow(
    events: pd.DataFrame,
    clinics: pd.DataFrame,
    month_start: date,
    inventory_counts: pd.DataFrame | None = None,
    coverage_tolerance: float = DEFAULT_CONFIG["coverage_tolerance"],
) -> dict[str, Any]:
    """Transformation: normalize events, compute the 4 BRIEFING KPIs, assemble and validate the rows.

    Returns kpis (12 rows), events_skipped, source_event_counts, consumption_by_department
    and coverage (None when the inventory reconciliation was not available).
    """
    normalized = normalize_supply_events(events, clinics, month_start)
    clinic_events = normalized["events"]
    kpis = assemble_monthly_clinic_kpis(
        clinics,
        month_start,
        compute_total_supply_cost(clinic_events, month_start),
        compute_supply_consumption_count(clinic_events, month_start),
        compute_critical_stockout_count(clinic_events, month_start),
        compute_expiry_risk_count(clinic_events, month_start),
    )
    coverage = validate_kpis(kpis, clinics, month_start, clinic_events, inventory_counts, coverage_tolerance)
    return {
        "kpis": kpis,
        "events_skipped": normalized["events_skipped"],
        "source_event_counts": normalized["source_event_counts"],
        "consumption_by_department": normalized["consumption_by_department"],
        "coverage": coverage,
    }


@flow(name="load_monthly_clinic_supply_performance_flow")
def load_monthly_clinic_supply_performance_flow(
    kpis: pd.DataFrame,
    run_id: UUID,
    month_start: date,
    trigger: str,
    checkpoint: dict[str, Any],
) -> dict[str, int]:
    """Load: idempotent upsert of one month into reporting.monthly_clinic_supply_performance."""
    return load_monthly_clinic_supply_performance(pipeline_engine(), kpis, run_id, month_start, trigger, checkpoint)


@flow(name="export_board_pack_snapshot_flow")
def export_board_pack_snapshot_flow(run_id: UUID, month_start: date, kpis_result: dict[str, Any]) -> str:
    """Optional: JSON snapshot of the month in data/eval/ (board pack review and validation)."""
    return export_eval_snapshot(run_id, month_start, kpis_result, kpis_result["coverage"])


# --- main flow ---------------------------------------------------------------------------

@flow(name="monthly_clinic_supply_performance_flow", log_prints=True)
def monthly_clinic_supply_performance_flow(
    months: list[date] | None = None,
    trigger: str = "scheduled",
    triggered_by: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Coordinates the subflows month by month (PIPELINE_DESIGN.md §5–§7); holds no ETL logic."""
    logger = get_run_logger()
    if trigger not in {"scheduled", "manual", "backfill"}:
        raise ValueError(f"Unknown trigger: {trigger}")
    config = load_config()
    months = resolve_months(months, int(config["late_window_months"]))
    # On a Prefect retry of the same flow run the id is the same, so completed months are skipped.
    current_run = UUID(run_id) if run_id else UUID(str(flow_run.id)) if flow_run.id else uuid4()
    engine = pipeline_engine()
    run_log.start_run(
        engine, run_id=current_run, pipeline_name=PIPELINE_NAME, trigger=trigger, triggered_by=triggered_by,
        months=months, flow_run_id=str(flow_run.id) if flow_run.id else None, code_version=code_version(),
    )
    summary: dict[str, Any] = {"source_events_read": 0, "rows_upserted": 0, "rows_changed": 0}
    skipped = dict.fromkeys(supply_kpis.SKIP_REASONS, 0)
    warnings: list[str] = []

    try:
        lock = acquire_run_lock(engine)
    except Exception as error:
        run_log.finish_run(engine, current_run, status="failed", error_stage="lock",
                           error_message=run_log.describe_error(error))
        raise
    if lock is None:
        logger.warning("Another run holds the lock: nothing is written")
        run_log.finish_run(engine, current_run, status="skipped_locked")
        return {"run_id": str(current_run), "status": "skipped_locked", "months": [m.isoformat() for m in months]}

    stage = "validate"  # registry checks; each month then moves through extract → load
    try:
        orphaned = run_log.fail_orphaned_runs(engine, PIPELINE_NAME, current_run)
        if orphaned:
            warnings.append(f"orphaned_runs_marked_failed:{orphaned}")
        clinics = supply_kpis.load_clinic_registry(REPO_ROOT / config["clinic_registry_path"])
        if len(clinics) != int(config["expected_clinics"]):
            raise KpiValidationError(f"Clinic registry has {len(clinics)} clinics, expected {config['expected_clinics']}")
        done = run_log.completed_months(engine, current_run)

        for month in months:  # orchestration over months, one load transaction each
            if month in done:
                logger.info("%s already completed by this run: skipped", month)
                continue
            stage = "extract"
            events = extract_supply_events_flow(month)
            summary["source_events_read"] += len(events)

            # Optional subflow: return_state=True so a failure is handled here instead of
            # propagating. Without inventory the month is still loaded, only without coverage.
            inventory_state = reconcile_inventory_movements_flow(month, return_state=True)
            if inventory_state.is_completed():
                inventory_counts = inventory_state.result()
            else:
                inventory_counts = None
                warnings.append(f"{month}: inventory_reconciliation_unavailable")
                logger.warning("%s: inventory reconciliation failed (%s); continuing without coverage",
                               month, inventory_state.message)

            stage = "transform"  # includes validate_kpis: a KpiValidationError is reported as "validate"
            result = compute_monthly_clinic_supply_kpis_flow(
                events, clinics, month, inventory_counts, float(config["coverage_tolerance"]),
            )
            for reason, count in result["events_skipped"].items():
                skipped[reason] += count
            coverage = result["coverage"]
            if coverage and not coverage["ready_for_board"]:
                warnings.append(f"{month}: coverage_out_of_range:{','.join(coverage['warnings'] or coverage['clinics_without_events'])}")

            stage = "load"
            loaded = load_monthly_clinic_supply_performance_flow(
                result["kpis"], current_run, month, trigger,
                {
                    "source_event_counts": result["source_event_counts"],
                    "inventory_counts": (inventory_counts.to_dict(orient="records")
                                         if inventory_counts is not None else None),
                    "clinics_without_events": coverage["clinics_without_events"] if coverage else [],
                    "coverage": coverage,
                    "consumption_by_department": result["consumption_by_department"],
                },
            )
            summary["rows_upserted"] += loaded["rows_upserted"]
            summary["rows_changed"] += loaded["rows_changed"]

            # Optional subflow: a failed snapshot is logged and the run still completes.
            snapshot_state = export_board_pack_snapshot_flow(current_run, month, result, return_state=True)
            if not snapshot_state.is_completed():
                warnings.append(f"{month}: eval_snapshot_failed")
                logger.warning("%s: eval snapshot failed (%s)", month, snapshot_state.message)
    except Exception as error:
        if isinstance(error, KpiValidationError):
            stage = "validate"
        run_log.finish_run(engine, current_run, status="failed", events_skipped=skipped, warnings=warnings,
                           error_stage=stage, error_message=run_log.describe_error(error), **summary)
        raise
    finally:
        try:
            lock.execute(select(func.pg_advisory_unlock(func.hashtext(PIPELINE_NAME))))
        finally:
            lock.close()

    run_log.finish_run(engine, current_run, status="completed", events_skipped=skipped, warnings=warnings, **summary)
    outcome = {"run_id": str(current_run), "status": "completed", "months": [m.isoformat() for m in months],
               **summary, "events_skipped": skipped, "warnings": warnings}
    logger.info("Run summary: %s", json.dumps(outcome))
    return outcome


@flow(name="backfill_monthly_clinic_supply_performance_flow")
def backfill_monthly_clinic_supply_performance_flow(start_month: date, end_month: date) -> dict[str, Any]:
    """Recompute a closed range of months (outside the late-event window), leaving revisions."""
    start, end = first_of_month(start_month), first_of_month(end_month)
    if start > end:
        raise ValueError("start_month must not be after end_month")
    span = (end.year - start.year) * 12 + end.month - start.month
    return monthly_clinic_supply_performance_flow(
        months=[add_months(start, offset) for offset in range(span + 1)], trigger="backfill",
    )


# --- functions used by services/reporting -----------------------------------------------

def get_monthly_clinic_supply_performance(month_start: date | None = None, engine: Engine | None = None) -> dict[str, Any] | None:
    """BRIEFING §6 payload for one month (default: latest computed month); None if not computed."""
    engine = engine or reporting_reader_engine()
    with engine.connect() as connection:
        if month_start is None:
            month_start = connection.execute(select(func.max(destination.c.month_start))).scalar()
            if month_start is None:
                return None
        rows = connection.execute(
            select(destination)
            .where(destination.c.month_start == month_start)
            .order_by(desc(destination.c.country), destination.c.clinic_id)
        ).all()
        if not rows:
            return None
        revised = connection.execute(
            select(func.count()).select_from(revisions).where(revisions.c.month_start == month_start)
        ).scalar()
        checkpoint = connection.execute(
            select(pipeline_run_months.c.run_id, pipeline_run_months.c.coverage,
                   pipeline_run_months.c.consumption_by_department, pipeline_run_months.c.completed_at)
            .where(pipeline_run_months.c.month_start == month_start, pipeline_run_months.c.status == "completed")
            .order_by(desc(pipeline_run_months.c.completed_at)).limit(1)
        ).first()
    coverage = checkpoint.coverage if checkpoint else None
    return {
        "month_start": month_start.isoformat(),
        "clinics": [
            {
                "clinic_id": row.clinic_id,
                "country": row.country,
                "total_supply_cost": float(row.total_supply_cost),
                "supply_consumption_count": row.supply_consumption_count,
                "critical_stockout_count": row.critical_stockout_count,
                "expiry_risk_count": row.expiry_risk_count,
                "currency": row.currency,
            }
            for row in rows
        ],
        # KPI «Volumen de consumo de insumos» por departamento (from the month's last checkpoint).
        "consumption_by_department": (checkpoint.consumption_by_department or []) if checkpoint else [],
        "computed_at": max(row.computed_at for row in rows).isoformat(),
        "last_run_id": str(checkpoint.run_id) if checkpoint else None,
        "revised": bool(revised),
        "coverage_checked": coverage is not None,
        "coverage_warnings": sorted({*(coverage or {}).get("warnings", []),
                                     *(coverage or {}).get("clinics_without_events", [])}),
    }


def _run_in_background(**parameters: Any) -> None:
    try:
        monthly_clinic_supply_performance_flow(**parameters)
    except Exception:  # already recorded in reporting.pipeline_runs by the flow
        logging.getLogger("api.reporting").exception("Manual pipeline run failed")


def trigger_run(months: list[date] | None, triggered_by: str | None, engine: Engine | None = None) -> dict[str, Any]:
    """Start a manual run without executing the ETL inside the HTTP request.

    - With REPORTING_PREFECT_DEPLOYMENT (e.g. "monthly_clinic_supply_performance_flow/daily"),
      it schedules a run of that Prefect deployment (run_deployment(..., timeout=0)).
    - Without it (local development) the flow runs in a background thread of this process.
    """
    running = run_log.get_running_run(engine or reporting_reader_engine(), PIPELINE_NAME)
    if running:
        raise RunInProgress(running["run_id"])
    run_id = str(uuid4())
    resolved = resolve_months(months, int(DEFAULT_CONFIG["late_window_months"]))
    # Register the run as `running` before handing it off: a second POST arriving while the
    # flow is still starting already gets 409. The flow reopens this same row (same run_id).
    run_log.start_run(
        pipeline_engine(), run_id=UUID(run_id), pipeline_name=PIPELINE_NAME, trigger="manual",
        triggered_by=triggered_by, months=resolved, flow_run_id=None, code_version=code_version(),
    )
    parameters = {
        "months": [first_of_month(month) for month in months] if months else None,
        "trigger": "manual",
        "triggered_by": triggered_by,
        "run_id": run_id,
    }
    deployment = os.environ.get("REPORTING_PREFECT_DEPLOYMENT")
    if deployment:
        from prefect.deployments import run_deployment

        scheduled = run_deployment(
            name=deployment,
            parameters={**parameters, "months": [m.isoformat() for m in parameters["months"] or []] or None},
            timeout=0,
        )
        return {"run_id": run_id, "flow_run_id": str(scheduled.id), "mode": "deployment"}
    threading.Thread(target=_run_in_background, kwargs=parameters, name=f"pipeline-{run_id}", daemon=True).start()
    return {"run_id": run_id, "flow_run_id": None, "mode": "in_process"}


# --- CLI ----------------------------------------------------------------------------

def _month(value: str) -> date:
    parsed = date.fromisoformat(value if len(value) > 7 else f"{value}-01")
    if parsed.day != 1:
        raise argparse.ArgumentTypeError("use the first day of the month, e.g. 2026-11-01")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HealthCore monthly clinic supply performance pipeline")
    parser.add_argument("--month", type=_month, action="append",
                        help="month to compute (YYYY-MM-01); repeatable. Default: current and previous month")
    parser.add_argument("--backfill", type=_month, nargs=2, metavar=("START", "END"),
                        help="recompute every month from START to END (inclusive)")
    args = parser.parse_args(argv)
    if args.backfill:
        result = backfill_monthly_clinic_supply_performance_flow(*args.backfill)
    else:
        result = monthly_clinic_supply_performance_flow(
            months=args.month, trigger="manual" if args.month else "scheduled",
        )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") in {"completed", "skipped_locked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
