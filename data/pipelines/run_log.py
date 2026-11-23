"""Execution log of the business pipeline: reporting.pipeline_runs (PIPELINE_DESIGN.md §6).

Every run leaves one row with, at least: started_at, finished_at, rows processed
(source_events_read, rows_upserted, rows_changed), final status and the captured
error (error_stage, error_message). services/reporting reads it through get_latest_run.
"""

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine

from data.pipelines.tables import pipeline_run_months, pipeline_runs

PUBLIC_FIELDS = (
    "run_id", "pipeline_name", "flow_run_id", "status", "trigger", "started_at", "finished_at", "months",
    "source_events_read", "rows_upserted", "rows_changed", "events_skipped", "warnings",
    "error_stage", "error_message", "code_version",
)
# Max length of error_message: class and message only, never event payloads.
_MAX_ERROR_LENGTH = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def describe_error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:_MAX_ERROR_LENGTH]


def start_run(
    engine: Engine,
    *,
    run_id: UUID,
    pipeline_name: str,
    trigger: str,
    triggered_by: str | None,
    months: list[date],
    flow_run_id: str | None,
    code_version: str,
) -> None:
    """Insert the `running` row. On a Prefect retry of the same flow run it is reopened."""
    values = {
        "run_id": str(run_id), "pipeline_name": pipeline_name, "flow_run_id": flow_run_id,
        "trigger": trigger, "triggered_by": triggered_by, "months": months, "started_at": _now(),
        "status": "running", "code_version": code_version,
    }
    statement = insert(pipeline_runs).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[pipeline_runs.c.run_id],
        set_={"status": "running", "finished_at": None, "error_stage": None, "error_message": None,
              "flow_run_id": statement.excluded.flow_run_id, "months": statement.excluded.months},
    )
    with engine.begin() as connection:
        connection.execute(statement)


def finish_run(
    engine: Engine,
    run_id: UUID,
    *,
    status: str,
    source_events_read: int = 0,
    rows_upserted: int = 0,
    rows_changed: int = 0,
    events_skipped: dict[str, int] | None = None,
    warnings: list[str] | None = None,
    error_stage: str | None = None,
    error_message: str | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.run_id == str(run_id))
            .values(
                status=status, finished_at=_now(), source_events_read=source_events_read,
                rows_upserted=rows_upserted, rows_changed=rows_changed,
                events_skipped=events_skipped or {}, warnings=warnings or [],
                error_stage=error_stage, error_message=error_message,
            )
        )


def fail_orphaned_runs(engine: Engine, pipeline_name: str, current_run_id: UUID) -> int:
    """Runs left `running` by a crashed process. Only called while holding the run lock,
    so no other run of this pipeline can be alive."""
    with engine.begin() as connection:
        result = connection.execute(
            update(pipeline_runs)
            .where(
                pipeline_runs.c.pipeline_name == pipeline_name,
                pipeline_runs.c.status == "running",
                pipeline_runs.c.run_id != str(current_run_id),
            )
            .values(status="failed", finished_at=_now(),
                    error_message="orphaned: the process stopped without reporting (Prefect Crashed)")
        )
    return result.rowcount


def completed_months(engine: Engine, run_id: UUID) -> set[date]:
    """Months already checkpointed by this run: a retry of the same flow run skips them."""
    with engine.connect() as connection:
        rows = connection.execute(
            select(pipeline_run_months.c.month_start).where(
                pipeline_run_months.c.run_id == str(run_id), pipeline_run_months.c.status == "completed"
            )
        )
        return {row.month_start for row in rows}


def last_completed_event_counts(connection: Connection, month_start: date) -> dict[str, int] | None:
    """source_event_counts of the most recent completed checkpoint of a month (late-event detection)."""
    return connection.execute(
        select(pipeline_run_months.c.source_event_counts)
        .where(pipeline_run_months.c.month_start == month_start, pipeline_run_months.c.status == "completed")
        .order_by(desc(pipeline_run_months.c.completed_at))
        .limit(1)
    ).scalar()


def _public(row: Any) -> dict[str, Any]:
    record = dict(row._mapping)
    return {
        **{field: record.get(field) for field in PUBLIC_FIELDS},
        "run_id": str(record["run_id"]),
        "months": [month.isoformat() for month in record["months"]],
    }


def get_latest_run(engine: Engine, pipeline_name: str) -> dict[str, Any] | None:
    """Most recent run of a pipeline (any status), for GET /reporting/pipeline-runs/latest."""
    with engine.connect() as connection:
        row = connection.execute(
            select(pipeline_runs)
            .where(pipeline_runs.c.pipeline_name == pipeline_name)
            .order_by(desc(pipeline_runs.c.started_at))
            .limit(1)
        ).first()
    return _public(row) if row else None


def get_running_run(engine: Engine, pipeline_name: str) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = connection.execute(
            select(pipeline_runs)
            .where(and_(pipeline_runs.c.pipeline_name == pipeline_name, pipeline_runs.c.status == "running"))
            .order_by(desc(pipeline_runs.c.started_at))
            .limit(1)
        ).first()
    return _public(row) if row else None
