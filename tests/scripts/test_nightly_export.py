"""scripts/nightly_export.py + services/job_runner.py against a real PostgreSQL.

The lock is a partial unique index and the export reads jsonb, so these tests need
Postgres: set REPORTING_TEST_DATABASE_URL (disposable database). The pipeline subprocess
is replaced by a stub; its real run is covered by tests/pipelines/.
"""

import csv
import json
import os
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from data.pipelines.connections import REPO_ROOT, engine_for
from scripts import nightly_export
from services import job_runner

TEST_URL = os.environ.get("REPORTING_TEST_DATABASE_URL")
DAY = date(2026, 11, 21)
MIGRATIONS = [
    "20261121180000_telemetry_events.sql",
    "20261122120000_reporting_monthly_clinic_supply_performance.sql",
    "20261122140000_reporting_consumption_by_department.sql",
    "20261122160000_reporting_job_runs.sql",
]


def test_target_date_defaults_to_yesterday_utc_and_accepts_overrides():
    today = date(2026, 11, 22)
    assert nightly_export.resolve_target_date({}, today) == date(2026, 11, 21)
    assert nightly_export.resolve_target_date({"TARGET_DATE": "2026-01-15"}, today) == date(2026, 1, 15)
    assert nightly_export.resolve_target_date({"TARGET_DATE": "2026-03-01"}, date(2026, 3, 2)) == date(2026, 3, 1)
    for bad in ("2026-13-01", "21/11/2026", "2026-11-22", "2026-12-01"):
        with pytest.raises(ValueError):
            nightly_export.resolve_target_date({"TARGET_DATE": bad}, today)


def test_script_is_independent_of_the_api():
    """Importing the script in a fresh interpreter never loads FastAPI nor the API app."""
    probe = ("import sys, runpy; runpy.run_path('scripts/nightly_export.py', run_name='probe'); "
             "print(sorted(m for m in sys.modules if m == 'fastapi' or m.startswith(('fastapi.', 'services.api'))))")
    result = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"


needs_postgres = pytest.mark.skipif(not TEST_URL, reason="REPORTING_TEST_DATABASE_URL is not set")


@pytest.fixture
def engine():
    engine = engine_for(TEST_URL)
    with engine.begin() as connection:
        cursor = connection.connection.cursor()  # migrations contain literal '%'
        cursor.execute("drop schema if exists reporting cascade; drop table if exists public.telemetry_events cascade")
        for name in MIGRATIONS:
            cursor.execute((REPO_ROOT / "supabase" / "migrations" / name).read_text())
    yield engine
    engine.dispose()


def add_event(engine, timestamp, event_type="outbound_order_created", **tags):
    with engine.begin() as connection:
        connection.execute(
            text("insert into public.telemetry_events (id, \"timestamp\", service, event_type, session_id, user_id, tags) "
                 "values (:id, :ts, 'backoffice', :type, 'session-x', :user, cast(:tags as jsonb))"),
            {"id": str(uuid4()), "ts": timestamp, "type": event_type, "user": "a" * 64,
             "tags": json.dumps(tags or {"clinic_id": 1, "quantity": 2})},
        )


class PipelineStub:
    def __init__(self, delay=0.0, error=None):
        self.calls, self.delay, self.error = [], delay, error

    def __call__(self, target_date, timeout_seconds):
        self.calls.append(target_date)
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return {"pipeline_exit_code": 0, "pipeline_run_id": "stub", "pipeline_seconds": self.delay}


def statuses(engine, target_date=DAY):
    return [(row["status"], row["skip_reason"]) for row in job_runner.list_job_runs(engine, "nightly_export", target_date)]


@needs_postgres
def test_successful_run_goes_pending_processing_completed(engine, tmp_path):
    add_event(engine, "2026-11-21T00:00:00Z", clinic_id=1, quantity=2)
    add_event(engine, "2026-11-21T23:59:59.999Z", "inbound_order_created", clinic_id=10, total_cost=60)
    add_event(engine, "2026-11-22T00:00:00Z")  # next day: not exported
    add_event(engine, "2026-11-20T23:59:59Z")  # previous day: not exported
    pipeline = PipelineStub()

    status, run_id = nightly_export.run_nightly_export(engine, DAY, pipeline_runner=pipeline, raw_dir=tmp_path)

    assert status == "completed" and pipeline.calls == [DAY]
    run = job_runner.get_job_run(engine, run_id)
    assert (run["status"], run["target_date"]) == ("completed", DAY)
    assert run["created_at"] <= run["started_at"] <= run["finished_at"] and run["error_message"] is None
    assert run["details"]["rows_exported"] == 2 and run["details"]["pipeline_run_id"] == "stub"
    with (tmp_path / "telemetry_2026-11-21.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["event_type"] for row in rows] == ["outbound_order_created", "inbound_order_created"]
    assert set(rows[0]) == {"id", "timestamp", "event_type", "tags"}  # no session/user ids on disk
    assert json.loads(rows[1]["tags"]) == {"clinic_id": 10, "total_cost": 60}
    assert rows[1]["timestamp"] == "2026-11-21T23:59:59.999Z"


@needs_postgres
def test_second_run_for_the_same_day_is_skipped_as_duplicate(engine, tmp_path):
    add_event(engine, "2026-11-21T10:00:00Z")
    pipeline = PipelineStub()
    nightly_export.run_nightly_export(engine, DAY, pipeline_runner=pipeline, raw_dir=tmp_path)
    csv_file = tmp_path / "telemetry_2026-11-21.csv"
    first_export = csv_file.read_bytes()
    csv_file.unlink()  # even without the file, a completed day is not exported again
    add_event(engine, "2026-11-21T11:00:00Z")

    status, _ = nightly_export.run_nightly_export(engine, DAY, pipeline_runner=pipeline, raw_dir=tmp_path)

    assert status == "skipped"
    assert pipeline.calls == [DAY]  # the pipeline ran once
    assert not csv_file.exists() and first_export.count(b"\n") == 2
    assert statuses(engine) == [("completed", None), ("skipped", "already_completed")]


@needs_postgres
def test_existing_csv_is_never_overwritten(engine, tmp_path):
    (tmp_path / "telemetry_2026-11-21.csv").write_text("id,timestamp,event_type,tags\nkept\n")
    add_event(engine, "2026-11-21T10:00:00Z")
    status, run_id = nightly_export.run_nightly_export(engine, DAY, pipeline_runner=PipelineStub(), raw_dir=tmp_path)
    assert status == "completed"
    assert (tmp_path / "telemetry_2026-11-21.csv").read_text().endswith("kept\n")
    assert job_runner.get_job_run(engine, run_id)["details"]["csv_created"] is False


@needs_postgres
def test_two_simultaneous_instances_run_the_job_once(engine, tmp_path):
    add_event(engine, "2026-11-21T10:00:00Z")
    pipeline = PipelineStub(delay=1.0)
    results, barrier = [], threading.Barrier(2)

    def instance():
        barrier.wait()  # both pass the initial checks at the same time
        results.append(nightly_export.run_nightly_export(engine_for(TEST_URL), DAY, pipeline_runner=pipeline,
                                                         raw_dir=tmp_path)[0])

    threads = [threading.Thread(target=instance) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["completed", "skipped"]
    assert pipeline.calls == [DAY]
    assert sorted(statuses(engine)) == [("completed", None), ("skipped", "locked")]


@needs_postgres
def test_processing_row_blocks_other_days_too(engine, tmp_path):
    other = job_runner.create_job_run(engine, "nightly_export", date(2026, 11, 20))
    job_runner.mark_processing(engine, other)
    assert job_runner.has_processing_lock(engine, "nightly_export")

    status, _ = nightly_export.run_nightly_export(engine, DAY, pipeline_runner=PipelineStub(), raw_dir=tmp_path)

    assert status == "skipped" and statuses(engine) == [("skipped", "locked")]
    with pytest.raises(job_runner.LockHeld):  # the index itself refuses a second processing row
        job_runner.mark_processing(engine, job_runner.create_job_run(engine, "nightly_export", DAY))


@needs_postgres
def test_failure_is_recorded_as_failed_with_the_message_and_releases_the_lock(engine, tmp_path):
    add_event(engine, "2026-11-21T10:00:00Z")
    failing = PipelineStub(error=nightly_export.PipelineFailed("pipeline exited with code 1: boom"))

    status, run_id = nightly_export.run_nightly_export(engine, DAY, pipeline_runner=failing, raw_dir=tmp_path)

    run = job_runner.get_job_run(engine, run_id)
    assert status == "failed"
    assert run["status"] == "failed" and run["finished_at"] is not None
    assert run["error_message"] == "PipelineFailed: pipeline exited with code 1: boom"
    assert not job_runner.has_processing_lock(engine, "nightly_export")
    # A failed day is not "done": the next run retries it (reusing the CSV already exported).
    status, retry_id = nightly_export.run_nightly_export(engine, DAY, pipeline_runner=PipelineStub(), raw_dir=tmp_path)
    assert status == "completed"
    assert job_runner.get_job_run(engine, retry_id)["details"]["csv_created"] is False


@needs_postgres
def test_interruptions_also_end_in_failed(engine, tmp_path):
    interrupted = PipelineStub(error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        nightly_export.run_nightly_export(engine, DAY, pipeline_runner=interrupted, raw_dir=tmp_path)
    assert statuses(engine) == [("failed", None)]


@needs_postgres
def test_database_failure_while_completing_still_leaves_no_zombie(engine, tmp_path, monkeypatch):
    def broken_complete(*args, **kwargs):
        raise RuntimeError("connection lost while completing")

    monkeypatch.setattr(job_runner, "mark_completed", broken_complete)
    status, run_id = nightly_export.run_nightly_export(engine, DAY, pipeline_runner=PipelineStub(), raw_dir=tmp_path)
    assert status == "failed"
    assert job_runner.get_job_run(engine, run_id)["error_message"] == "RuntimeError: connection lost while completing"


@needs_postgres
def test_stale_processing_from_a_killed_process_is_failed(engine, tmp_path):
    zombie = job_runner.create_job_run(engine, "nightly_export", date(2026, 11, 20))
    job_runner.mark_processing(engine, zombie)
    with engine.begin() as connection:  # pretend it started 3 hours ago and was SIGKILLed
        connection.execute(text("update reporting.job_runs set started_at = now() - interval '3 hours' where id = :id"),
                           {"id": zombie})

    status, _ = nightly_export.run_nightly_export(engine, DAY, pipeline_runner=PipelineStub(), raw_dir=tmp_path,
                                                  stale_after=timedelta(hours=2))

    assert status == "completed"
    stale = job_runner.get_job_run(engine, zombie)
    assert stale["status"] == "failed" and stale["error_message"].startswith("stale:")


@needs_postgres
def test_a_day_can_complete_only_once_even_bypassing_the_checks(engine):
    first = job_runner.create_job_run(engine, "nightly_export", DAY)
    job_runner.mark_processing(engine, first)
    job_runner.mark_completed(engine, first, {})
    second = job_runner.create_job_run(engine, "nightly_export", DAY)
    job_runner.mark_processing(engine, second)
    with pytest.raises(Exception, match="job_runs_one_completed_per_day_idx"):
        job_runner.mark_completed(engine, second, {})
