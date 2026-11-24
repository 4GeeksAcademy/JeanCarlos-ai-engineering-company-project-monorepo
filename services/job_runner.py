"""State control of scheduled jobs: reporting.job_runs (ticket DEV-53).

Lifecycle of an execution:

    pending ──► processing ──► completed
                          └──► failed

- pending: the row exists before any work starts.
- processing: set before doing any work. It is the distributed lock: the partial unique
  index job_runs_one_processing_idx admits a single `processing` row per job, so the
  transition is atomic across processes and machines. There is no other lock.
- completed / failed: terminal states; both release the lock.
- skipped: terminal record for an execution that aborted silently (lock held, or the day
  already completed). It never was `processing`.

Plain SQLAlchemy Core, no FastAPI: the nightly script imports this module in its own process.
"""

import json
import os
import socket
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import BigInteger, Column, Date, DateTime, Integer, MetaData, Table, Text, and_, insert, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

PROCESSING_LOCK_INDEX = "job_runs_one_processing_idx"
COMPLETED_PER_DAY_INDEX = "job_runs_one_completed_per_day_idx"
_MAX_ERROR_LENGTH = 1000

metadata = MetaData()
job_runs = Table(
    "job_runs",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("job_name", Text, nullable=False),
    Column("target_date", Date, nullable=False),
    Column("status", Text, nullable=False),
    Column("skip_reason", Text),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("error_message", Text),
    Column("details", JSONB, nullable=False),
    Column("host", Text),
    Column("pid", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema="reporting",
)


class LockHeld(RuntimeError):
    """Another execution of the job is `processing`."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _violates(error: IntegrityError, index: str) -> bool:
    return index in str(error.orig)


def describe_error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:_MAX_ERROR_LENGTH]


def create_job_run(engine: Engine, job_name: str, target_date: date) -> int:
    """New `pending` row, created before any work."""
    with engine.begin() as connection:
        return connection.execute(
            insert(job_runs)
            .values(job_name=job_name, target_date=target_date, status="pending", details={},
                    host=socket.gethostname(), pid=os.getpid(), created_at=_now())
            .returning(job_runs.c.id)
        ).scalar_one()


def mark_processing(engine: Engine, run_id: int) -> None:
    """pending → processing: takes the lock or raises LockHeld (the unique index decides)."""
    try:
        with engine.begin() as connection:
            updated = connection.execute(
                update(job_runs)
                .where(job_runs.c.id == run_id, job_runs.c.status == "pending")
                .values(status="processing", started_at=_now())
            ).rowcount
    except IntegrityError as error:
        if _violates(error, PROCESSING_LOCK_INDEX):
            raise LockHeld(f"another execution is processing ({PROCESSING_LOCK_INDEX})") from error
        raise
    if updated != 1:
        raise RuntimeError(f"job run {run_id} is not pending")


def mark_completed(engine: Engine, run_id: int, details: dict[str, Any]) -> None:
    """processing → completed (releases the lock)."""
    with engine.begin() as connection:
        updated = connection.execute(
            update(job_runs)
            .where(job_runs.c.id == run_id, job_runs.c.status == "processing")
            .values(status="completed", finished_at=_now(), details=details, error_message=None)
        ).rowcount
    if updated != 1:
        raise RuntimeError(f"job run {run_id} is not processing")


def mark_failed(engine: Engine, run_id: int, error_message: str, details: dict[str, Any] | None = None) -> bool:
    """pending/processing → failed (releases the lock). Returns False if it was already terminal."""
    values: dict[str, Any] = {"status": "failed", "finished_at": _now(), "error_message": error_message[:_MAX_ERROR_LENGTH]}
    if details is not None:
        values["details"] = details
    with engine.begin() as connection:
        return connection.execute(
            update(job_runs)
            .where(job_runs.c.id == run_id, job_runs.c.status.in_(("pending", "processing")))
            .values(**values)
        ).rowcount == 1


def mark_skipped(engine: Engine, run_id: int, reason: str, message: str) -> None:
    """pending → skipped: this execution aborted silently before taking the lock."""
    with engine.begin() as connection:
        connection.execute(
            update(job_runs)
            .where(job_runs.c.id == run_id, job_runs.c.status == "pending")
            .values(status="skipped", skip_reason=reason, finished_at=_now(), error_message=message)
        )


def has_processing_lock(engine: Engine, job_name: str) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            select(job_runs.c.id).where(job_runs.c.job_name == job_name, job_runs.c.status == "processing").limit(1)
        ).first() is not None


def has_completed_for_date(engine: Engine, job_name: str, target_date: date) -> bool:
    """Per-day idempotency: (job_name, target_date) already completed."""
    with engine.connect() as connection:
        return connection.execute(
            select(job_runs.c.id).where(
                and_(job_runs.c.job_name == job_name, job_runs.c.target_date == target_date,
                     job_runs.c.status == "completed")
            ).limit(1)
        ).first() is not None


def fail_stale_processing(engine: Engine, job_name: str, older_than: timedelta) -> int:
    """Rows left `processing` by a process killed without a chance to clean up (SIGKILL,
    machine reboot). try/finally covers every exception and SIGTERM; only a hard kill can
    leave one. Marked failed once older than the job's maximum duration, so no zombie stays."""
    with engine.begin() as connection:
        return connection.execute(
            update(job_runs)
            .where(job_runs.c.job_name == job_name, job_runs.c.status == "processing",
                   job_runs.c.started_at < _now() - older_than)
            .values(status="failed", finished_at=_now(),
                    error_message=f"stale: still processing after {older_than}; process presumed dead")
        ).rowcount


def get_job_run(engine: Engine, run_id: int) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = connection.execute(select(job_runs).where(job_runs.c.id == run_id)).first()
    return dict(row._mapping) if row else None


def list_job_runs(engine: Engine, job_name: str, target_date: date | None = None) -> list[dict[str, Any]]:
    statement = select(job_runs).where(job_runs.c.job_name == job_name).order_by(job_runs.c.id)
    if target_date is not None:
        statement = statement.where(job_runs.c.target_date == target_date)
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(statement)]


def to_json(details: dict[str, Any]) -> dict[str, Any]:
    """details must be JSON-serialisable (dates/paths as strings)."""
    return json.loads(json.dumps(details, default=str))
