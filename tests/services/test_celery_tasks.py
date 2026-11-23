"""services/celery_app.py: retries with exponential backoff, Dead Letter Queue, slot release.

Runs the task with Celery's eager mode (no broker, no worker): `self.retry` re-executes
the task in-process, so the four attempts happen synchronously. The pipeline itself is
stubbed; its real run is covered by tests/pipelines/.
"""

import pytest

from data.pipelines import pipeline
from services import celery_app as tasks

TASK_ID = "5421a39f-d432-4383-aca6-b5e64ccd2611"


@pytest.fixture
def eager(monkeypatch):
    monkeypatch.setitem(tasks.app.conf, "task_always_eager", True)
    monkeypatch.setitem(tasks.app.conf, "task_eager_propagates", False)
    monkeypatch.setitem(tasks.app.conf, "task_store_eager_result", False)
    calls = {"attempts": 0, "dead": [], "dlq_messages": [], "released": []}
    monkeypatch.setattr(tasks, "release_pipeline_slot", lambda task_id: calls["released"].append(task_id))
    monkeypatch.setattr(tasks.app, "send_task", lambda name, kwargs, queue: calls["dlq_messages"].append((name, queue, kwargs)))
    monkeypatch.setattr("services.task_dead_letters.record_dead_letter", lambda engine, **kw: calls["dead"].append(kw))
    monkeypatch.setattr("data.pipelines.connections.pipeline_engine", lambda: "engine")
    return calls


def run(**kwargs):
    return tasks.run_monthly_clinic_supply_performance.apply(
        kwargs={"run_id": TASK_ID, "months": ["2026-11-01"], "triggered_by": "a" * 64, **kwargs}, task_id=TASK_ID)


def test_backoff_is_exponential_and_never_immediate():
    assert [tasks.retry_countdown(n) for n in range(3)] == [10.0, 20.0, 40.0]
    assert tasks.run_monthly_clinic_supply_performance.max_retries == 3


def test_success_on_first_attempt_releases_the_slot(eager, monkeypatch):
    monkeypatch.setattr(pipeline, "run_manual_run", lambda run_id, months, by: {"run_id": run_id, "status": "completed"})
    result = run()
    assert result.successful() and result.result == {"run_id": TASK_ID, "status": "completed"}
    assert eager["released"] == [TASK_ID] and eager["dead"] == []


def test_transient_failure_is_retried_then_succeeds(eager, monkeypatch, caplog):
    def flaky(run_id, months, by):
        eager["attempts"] += 1
        if eager["attempts"] < 3:
            raise ConnectionError("pooler restarting")
        return {"run_id": run_id, "status": "completed"}

    monkeypatch.setattr(pipeline, "run_manual_run", flaky)
    caplog.set_level("INFO", logger=tasks.logger.name)
    result = run()

    assert result.successful() and eager["attempts"] == 3 and eager["dead"] == []
    statuses = [line for line in caplog.messages if "status=" in line]
    assert any("attempt=1/4 status=retry" in line and "next_attempt_in=10s" in line for line in statuses)
    assert any("attempt=2/4 status=retry" in line and "next_attempt_in=20s" in line for line in statuses)
    assert any("attempt=3/4 status=success" in line and "duration=" in line for line in statuses)


def test_after_max_retries_the_task_goes_to_the_dead_letter_queue(eager, monkeypatch, caplog):
    def broken(run_id, months, by):
        eager["attempts"] += 1
        raise ValueError("No columns to parse from file")

    monkeypatch.setattr(pipeline, "run_manual_run", broken)
    caplog.set_level("INFO", logger=tasks.logger.name)
    result = run()

    assert result.failed() and eager["attempts"] == 4  # 1 attempt + max_retries=3
    (record,) = eager["dead"]
    assert record["task_id"] == TASK_ID and record["attempt"] == 4 and record["max_retries"] == 3
    assert str(record["error"]) == "No columns to parse from file" and "ValueError" in record["traceback"]
    assert record["task_kwargs"] == {"run_id": TASK_ID, "months": ["2026-11-01"]}
    ((name, queue, message),) = eager["dlq_messages"]
    assert (name, queue) == ("reporting.dead_letter", "dead_letter") and message["attempt"] == 4
    assert eager["released"] == [TASK_ID]
    failure = [line for line in caplog.messages if "status=failure" in line][0]
    assert "attempt=4/4" in failure and "dead_letter=true" in failure and "Traceback" in failure


def test_database_down_while_recording_still_parks_the_dlq_message(eager, monkeypatch):
    def db_down(engine, **kwargs):
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(pipeline, "run_manual_run", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("services.task_dead_letters.record_dead_letter", db_down)
    result = run()
    assert result.failed() and len(eager["dlq_messages"]) == 1 and eager["released"] == [TASK_ID]


def test_queue_configuration_matches_the_ticket():
    conf = tasks.app.conf
    assert conf.task_acks_late and conf.task_reject_on_worker_lost  # ACK only after success
    assert conf.task_time_limit > conf.task_soft_time_limit > 0  # every task has a timeout
    assert conf.accept_content == ["json"] and conf.worker_prefetch_multiplier == 1
    assert {queue.name for queue in conf.task_queues} == {"celery", "reporting", "dead_letter"}
