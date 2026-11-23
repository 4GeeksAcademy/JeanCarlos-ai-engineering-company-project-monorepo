#!/usr/bin/env python3
"""Nightly telemetry job (ticket DEV-53): CSV backup of yesterday + business pipeline run.

    python scripts/nightly_export.py                         # target = yesterday (UTC)
    TARGET_DATE=2026-11-21 python scripts/nightly_export.py  # any date, without code changes

Steps, recorded in reporting.job_runs (services/job_runner.py):

1. Resolve target_date: TARGET_DATE (YYYY-MM-DD) or the previous UTC calendar day.
2. Per-day idempotency: if (nightly_export, target_date) is already completed, log the
   duplicate as a `skipped` row and exit 0. No new CSV and no new pipeline run.
3. Lock: if another execution is `processing`, log it as `skipped` and exit 0 (silently).
4. pending → processing (atomic; the processing state is the lock).
5. Export the telemetry_events rows of target_date to data/raw/telemetry_YYYY-MM-DD.csv,
   only if the file does not exist yet. It is a backup for audit and recovery; the
   pipeline does not read it.
6. Run the business pipeline as a subprocess for the month of target_date:
   python data/pipelines/pipeline.py --month YYYY-MM-01 --trigger scheduled
   (it reads telemetry_events from the database and writes its own reporting.pipeline_runs).
7. processing → completed, or → failed with the exception message. try/except/finally
   (plus a SIGTERM handler) guarantee no row stays `processing`.

Independent process: it never imports FastAPI or the API app. It is triggered by cron,
outside the API (see scripts/nightly_export.crontab).

Environment:
- REPORTING_PIPELINE_DATABASE_URL (services/api/.env or Prefect block): role reporting_pipeline.
- TARGET_DATE (optional, YYYY-MM-DD).
- NIGHTLY_PIPELINE_TIMEOUT_SECONDS (optional, default 1800).
- NIGHTLY_STALE_AFTER_MINUTES (optional, default 120): a `processing` row older than this
  is from a hard-killed process and is marked failed.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # `python scripts/nightly_export.py` from any directory
    sys.path.insert(0, str(REPO_ROOT))

import csv
import json
import logging
import os
import signal
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from data.pipelines.connections import pipeline_engine
from data.pipelines.tables import telemetry_events
from services import job_runner

JOB_NAME = "nightly_export"
RAW_DIR = REPO_ROOT / "data" / "raw"
CSV_COLUMNS = ("id", "timestamp", "event_type", "tags")
PIPELINE_ENTRYPOINT = REPO_ROOT / "data" / "pipelines" / "pipeline.py"


# --- logging: every line carries timestamp, job name and resulting status ------------------

class _JobContext(logging.Filter):
    target_date = "-"

    def filter(self, record: logging.LogRecord) -> bool:
        record.job = JOB_NAME
        record.target_date = self.target_date
        record.status = getattr(record, "status", "-")
        return True


_context = _JobContext()
logger = logging.getLogger("jobs.nightly_export")


def configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s job=%(job)s target_date=%(target_date)s status=%(status)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = time.gmtime  # timestamps in UTC, like target_date
    handler.setFormatter(formatter)
    handler.addFilter(_context)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log(level: int, status: str, message: str, *args: Any, exc_info: bool = False) -> None:
    logger.log(level, message, *args, extra={"status": status}, exc_info=exc_info)


# --- steps -----------------------------------------------------------------------------------

def resolve_target_date(environ: dict[str, str] | None = None, today: date | None = None) -> date:
    """TARGET_DATE=YYYY-MM-DD, or yesterday as a UTC calendar day."""
    value = (environ if environ is not None else os.environ).get("TARGET_DATE", "").strip()
    today = today or datetime.now(timezone.utc).date()
    if not value:
        return today - timedelta(days=1)
    try:
        target = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"TARGET_DATE must be YYYY-MM-DD, got {value!r}") from error
    if target >= today:
        raise ValueError(f"TARGET_DATE {target} is not a closed day (today is {today} UTC)")
    return target


def csv_path_for(target_date: date, raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / f"telemetry_{target_date.isoformat()}.csv"


def export_telemetry_csv(engine: Engine, target_date: date, raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Backup of target_date's telemetry_events rows, only if the file does not exist yet.

    Columns: id, timestamp, event_type, tags. These are the ones the pipeline role may read;
    the pseudonymous session_id/user_id/request_id stay out of files on disk. The file is
    written to a temporary name and renamed, so a crash never leaves a half-written CSV that
    a later run would mistake for a finished export.
    """
    path = csv_path_for(target_date, raw_dir)
    if path.exists():
        return {"csv_path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
                "csv_created": False, "rows_exported": None}
    start = datetime.combine(target_date, datetime.min.time(), timezone.utc)
    statement = (
        select(*(telemetry_events.c[column] for column in CSV_COLUMNS))
        .where(telemetry_events.c.timestamp >= start, telemetry_events.c.timestamp < start + timedelta(days=1))
        .order_by(telemetry_events.c.timestamp, telemetry_events.c.id)
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(f".csv.partial-{os.getpid()}")
    rows = 0
    try:
        with engine.connect() as connection, partial.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_COLUMNS)
            for row in connection.execute(statement):
                writer.writerow([
                    row.id,
                    row.timestamp.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    row.event_type,
                    json.dumps(row.tags, sort_keys=True, ensure_ascii=False),
                ])
                rows += 1
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return {"csv_path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
            "csv_created": True, "rows_exported": rows}


class PipelineFailed(RuntimeError):
    pass


def run_pipeline(target_date: date, timeout_seconds: float) -> dict[str, Any]:
    """Business pipeline in its own process; it reads telemetry_events from the database."""
    month = target_date.replace(day=1).isoformat()
    command = [sys.executable, str(PIPELINE_ENTRYPOINT), "--month", month, "--trigger", "scheduled"]
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        raise PipelineFailed(f"pipeline did not finish within {timeout_seconds:g} s") from error
    result: dict[str, Any] = {
        "pipeline_command": f"python data/pipelines/pipeline.py --month {month} --trigger scheduled",
        "pipeline_exit_code": completed.returncode,
        "pipeline_seconds": round(time.monotonic() - started, 1),
    }
    summary = _last_json_object(completed.stdout)
    if summary:  # link to reporting.pipeline_runs instead of duplicating its metrics
        result["pipeline_run_id"] = summary.get("run_id")
        result["pipeline_status"] = summary.get("status")
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout).strip().splitlines()[-3:]
        raise PipelineFailed(f"pipeline exited with code {completed.returncode}: {' | '.join(tail)}")
    return result


def _last_json_object(output: str) -> dict[str, Any] | None:
    start = output.rfind("\n{")
    start = 0 if start == -1 and output.startswith("{") else start + 1
    try:
        value = json.loads(output[start:]) if start >= 0 else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# --- orchestration ------------------------------------------------------------------------------

class _Terminated(Exception):
    """SIGTERM (cron/container stop) turned into an exception so `except`/`finally` run."""


def _on_sigterm(signum: int, _frame: Any) -> None:
    raise _Terminated(f"terminated by signal {signum}")


def run_nightly_export(
    engine: Engine,
    target_date: date,
    *,
    pipeline_runner=run_pipeline,
    raw_dir: Path = RAW_DIR,
    timeout_seconds: float = 1800,
    stale_after: timedelta = timedelta(minutes=120),
) -> tuple[str, int | None]:
    """Returns (final status, job_runs.id). Never leaves the row in pending/processing."""
    _context.target_date = target_date.isoformat()

    stale = job_runner.fail_stale_processing(engine, JOB_NAME, stale_after)
    if stale:
        log(logging.ERROR, "failed", "marked %d stale processing row(s) as failed (process presumed dead)", stale)

    if job_runner.has_completed_for_date(engine, JOB_NAME, target_date):
        run_id = job_runner.create_job_run(engine, JOB_NAME, target_date)
        job_runner.mark_skipped(engine, run_id, "already_completed", "skipped: target_date already completed")
        log(logging.INFO, "skipped", "duplicate: %s already completed; no CSV export, no pipeline run (job_run=%d)",
            target_date, run_id)
        return "skipped", run_id

    if job_runner.has_processing_lock(engine, JOB_NAME):
        run_id = job_runner.create_job_run(engine, JOB_NAME, target_date)
        job_runner.mark_skipped(engine, run_id, "locked", "skipped: another execution is processing")
        log(logging.INFO, "skipped", "another execution is processing; aborting silently (job_run=%d)", run_id)
        return "skipped", run_id

    run_id = job_runner.create_job_run(engine, JOB_NAME, target_date)
    log(logging.INFO, "pending", "job run created (job_run=%d)", run_id)
    try:
        job_runner.mark_processing(engine, run_id)
    except job_runner.LockHeld:  # lost the race between the check above and this transition
        job_runner.mark_skipped(engine, run_id, "locked", "skipped: another execution took the lock first")
        log(logging.INFO, "skipped", "another execution took the lock first; aborting silently (job_run=%d)", run_id)
        return "skipped", run_id
    log(logging.INFO, "processing", "started (job_run=%d)", run_id)

    details: dict[str, Any] = {"target_date": target_date.isoformat()}
    status = "failed"
    try:
        details.update(export_telemetry_csv(engine, target_date, raw_dir))
        if details["csv_created"]:
            log(logging.INFO, "processing", "exported %d rows to %s", details["rows_exported"], details["csv_path"])
        else:
            log(logging.INFO, "processing", "CSV %s already exists; export skipped", details["csv_path"])
        details.update(pipeline_runner(target_date, timeout_seconds))
        log(logging.INFO, "processing", "pipeline finished (pipeline_run=%s, %.1f s)",
            details.get("pipeline_run_id"), details.get("pipeline_seconds", 0))
        job_runner.mark_completed(engine, run_id, job_runner.to_json(details))
        status = "completed"
        log(logging.INFO, "completed", "finished (job_run=%d)", run_id)
    except BaseException as error:  # includes SIGTERM (_Terminated) and KeyboardInterrupt
        job_runner.mark_failed(engine, run_id, job_runner.describe_error(error), job_runner.to_json(details))
        log(logging.ERROR, "failed", "%s (job_run=%d)", job_runner.describe_error(error), run_id, exc_info=True)
        if not isinstance(error, Exception):
            raise
    finally:
        # Last line of defence: whatever happened above, the row must not stay pending/processing.
        current = job_runner.get_job_run(engine, run_id)
        if current and current["status"] in ("pending", "processing"):
            job_runner.mark_failed(engine, run_id, "aborted without reaching a terminal state")
            log(logging.ERROR, "failed", "forced to failed in finally (job_run=%d)", run_id)
            status = "failed"
    return status, run_id


def main() -> int:
    configure_logging()
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        target_date = resolve_target_date()
    except ValueError as error:
        log(logging.ERROR, "failed", "%s", error)
        return 2
    _context.target_date = target_date.isoformat()
    try:
        status, _ = run_nightly_export(
            pipeline_engine(),
            target_date,
            timeout_seconds=float(os.environ.get("NIGHTLY_PIPELINE_TIMEOUT_SECONDS", "1800")),
            stale_after=timedelta(minutes=float(os.environ.get("NIGHTLY_STALE_AFTER_MINUTES", "120"))),
        )
    except Exception as error:  # e.g. database unreachable before any row could be written
        log(logging.ERROR, "failed", "%s", job_runner.describe_error(error), exc_info=True)
        return 1
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
