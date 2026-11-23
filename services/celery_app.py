"""Celery task queue (ticket DEV-55): Redis as broker and result backend.

    Client ─► API (producer) ─► Redis (broker) ─► worker (consumer) ─► Redis (result)

- The API only enqueues: POST /reporting/pipeline-runs answers 202 + task_id at once.
- Workers are separate processes (`celery -A services.celery_app worker -Q reporting`),
  never inside FastAPI. Stopping the API stops neither the workers nor the queued messages
  (Redis with AOF persistence and noeviction).
- Messages are lightweight: identifiers only (run_id, ISO months, an HMAC pseudonym).
  The worker reads the telemetry from the database itself.
- ACK only after success (task_acks_late): if a worker dies mid-task the message goes back
  to the queue. Every task has a time limit, so a stuck task cannot block the pool.
- Retries: max_retries=3 with exponential backoff (10 s, 20 s, 40 s by default). After the
  last attempt the failure goes to the Dead Letter Queue: a row in
  reporting.task_dead_letters (task_id, attempt, error, timestamp) plus a message in the
  `dead_letter` queue, which no worker consumes, so it stays visible in Flower until
  someone inspects or replays it.

Configuration: REDIS_URL (default redis://localhost:6379/0), CELERY_RETRY_BASE_SECONDS
(default 10), CELERY_TASK_SOFT_TIME_LIMIT (default 900 s).
"""

import os
import sys
import time
import traceback as traceback_module
from pathlib import Path
from typing import Any

# `celery -A services.celery_app` puts the working directory on sys.path only while it
# imports this module and removes it afterwards; tasks import data.pipelines later, so
# add the repository root unconditionally (a duplicate entry survives that removal).
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, _REPO_ROOT)

from celery import Celery, Task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger
from kombu import Queue

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
PIPELINE_QUEUE = "reporting"
DEAD_LETTER_QUEUE = "dead_letter"
MAX_RETRIES = 3
RETRY_BASE_SECONDS = float(os.environ.get("CELERY_RETRY_BASE_SECONDS", "10"))
SOFT_TIME_LIMIT = int(os.environ.get("CELERY_TASK_SOFT_TIME_LIMIT", "900"))
HARD_TIME_LIMIT = SOFT_TIME_LIMIT + 60
# One manual pipeline run at a time: Redis key held from enqueue to success or dead letter.
ACTIVE_RUN_KEY = "healthcore:reporting:monthly_clinic_supply_performance:active_task"
# Safety expiry (every attempt at its hard limit + the backoff waits): a lost worker never
# blocks manual runs forever.
ACTIVE_RUN_TTL = (MAX_RETRIES + 1) * HARD_TIME_LIMIT + int(sum(RETRY_BASE_SECONDS * 2 ** n for n in range(MAX_RETRIES)))

app = Celery("healthcore", broker=REDIS_URL, backend=REDIS_URL)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],  # no pickle: messages are plain identifiers
    task_acks_late=True,  # ACK only after the task finished
    task_reject_on_worker_lost=True,  # a killed worker puts the message back in the queue
    worker_prefetch_multiplier=1,  # one long task per process at a time
    task_track_started=True,  # GET /tasks/{id} can report `started`
    result_extended=True,
    result_expires=7 * 24 * 3600,
    task_soft_time_limit=SOFT_TIME_LIMIT,
    task_time_limit=HARD_TIME_LIMIT,
    # Unacked messages return to the queue after this; longer than any task + retry wait.
    broker_transport_options={"visibility_timeout": 4 * 3600},
    broker_connection_retry_on_startup=True,
    # Fail fast when the broker is down, so the API answers 503 instead of hanging.
    task_publish_retry_policy={"max_retries": 2, "interval_start": 0, "interval_step": 0.2, "interval_max": 0.5},
    # Declared explicitly so they exist (and show in Flower → Broker) before the first message.
    task_queues=(Queue("celery"), Queue(PIPELINE_QUEUE), Queue(DEAD_LETTER_QUEUE)),
    task_routes={
        "reporting.run_monthly_clinic_supply_performance": {"queue": PIPELINE_QUEUE},
        "reporting.dead_letter": {"queue": DEAD_LETTER_QUEUE},
    },
    worker_hijack_root_logger=False,
    worker_task_log_format="%(asctime)s %(levelname)s [%(task_name)s] %(message)s",
    worker_send_task_events=True,  # Flower
    task_send_sent_event=True,
)

logger = get_task_logger(__name__)

_RELEASE_IF_OWNER = app.backend.client.register_script(
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
) if hasattr(app.backend, "client") else None


def claim_pipeline_slot(task_id: str) -> str | None:
    """Producer side: take the single manual-run slot. Returns the holder's task_id if taken.

    Only Redis is touched (SET NX EX, ~1 ms), so POST /reporting/pipeline-runs answers 202
    without waiting on the database. The pipeline's advisory lock still protects the data
    against any other run (nightly job, CLI).
    """
    client = app.backend.client
    if client.set(ACTIVE_RUN_KEY, task_id, nx=True, ex=ACTIVE_RUN_TTL):
        return None
    holder = client.get(ACTIVE_RUN_KEY)
    return holder.decode() if isinstance(holder, bytes) else holder


def mark_pending(task_id: str) -> None:
    """Producer side: store a PENDING record so GET /tasks/{id} knows the task before a worker
    picks it up (Celery itself reports PENDING for any id, known or not)."""
    app.backend.store_result(task_id, None, "PENDING")


def read_task_meta(task_id: str) -> dict[str, Any] | None:
    """Result-backend record of a task (None if the id was never enqueued).

    For FAILURE/RETRY, `error` holds "ExceptionType: message" rebuilt from the stored exception.
    """
    backend = app.backend
    if not backend.client.exists(backend.get_key_for_task(task_id)):
        return None
    meta = dict(backend.get_task_meta(task_id))
    if meta.get("status") in {"FAILURE", "RETRY"}:
        error = backend.exception_to_python(meta.get("result"))
        meta["error"] = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    return meta


def release_pipeline_slot(task_id: str) -> None:
    """Free the slot only if this task holds it (atomic compare-and-delete)."""
    if _RELEASE_IF_OWNER is not None:
        _RELEASE_IF_OWNER(keys=[ACTIVE_RUN_KEY], args=[task_id])


def retry_countdown(retries_done: int) -> float:
    """Exponential backoff: 10 s, 20 s, 40 s (never an immediate retry)."""
    return RETRY_BASE_SECONDS * 2 ** retries_done


def _log(level: int, task: Task, status: str, started: float, message: str = "", *args: Any) -> None:
    attempt = task.request.retries + 1
    logger.log(
        level,
        "task_id=%s attempt=%d/%d status=%s duration=%.2fs" + (" " + message if message else ""),
        task.request.id, attempt, MAX_RETRIES + 1, status, time.monotonic() - started, *args,
    )


def _send_to_dead_letter(task: Task, error: BaseException, trace: str, kwargs: dict[str, Any]) -> None:
    """Park a lightweight message in the DLQ queue and record the failure in the database.

    Redis first: if the final failure was the database itself, the DLQ message still
    survives. Each step is guarded on its own, so one failing never hides the other.
    """
    attempt = task.request.retries + 1
    try:
        app.send_task(
            "reporting.dead_letter",
            kwargs={"task_id": task.request.id, "task_name": task.name, "attempt": attempt,
                    "error": f"{type(error).__name__}: {error}"[:500]},
            queue=DEAD_LETTER_QUEUE,
        )
    except Exception:
        logger.exception("task_id=%s could not publish to the %s queue", task.request.id, DEAD_LETTER_QUEUE)
    try:
        from data.pipelines.connections import pipeline_engine
        from services.task_dead_letters import record_dead_letter

        record_dead_letter(
            pipeline_engine(), task_id=task.request.id, task_name=task.name, attempt=attempt,
            max_retries=MAX_RETRIES, error=error, traceback=trace, task_kwargs=kwargs,
        )
    except Exception:
        logger.exception("task_id=%s could not record the dead letter in reporting.task_dead_letters", task.request.id)


@app.task(bind=True, name="reporting.run_monthly_clinic_supply_performance", max_retries=MAX_RETRIES)
def run_monthly_clinic_supply_performance(
    self: Task, run_id: str, months: list[str] | None = None, triggered_by: str | None = None
) -> dict[str, Any]:
    """Heavy operation of POST /reporting/pipeline-runs: the whole business pipeline
    (extract from telemetry_events, KPIs, idempotent load). Only identifiers travel in
    the message; the flow reads the data from the database."""
    started = time.monotonic()
    _log(20, self, "started", started, "run_id=%s months=%s", run_id, months)
    try:
        from data.pipelines.pipeline import run_manual_run  # heavy import, only in workers

        result = run_manual_run(run_id, months, triggered_by)
    except (Exception, SoftTimeLimitExceeded) as error:
        trace = traceback_module.format_exc()
        if self.request.retries >= self.max_retries:
            _log(40, self, "failure", started, "dead_letter=true error=%s: %s\n%s", type(error).__name__, error, trace)
            _send_to_dead_letter(self, error, trace, {"run_id": run_id, "months": months})
            release_pipeline_slot(self.request.id)
            raise
        countdown = retry_countdown(self.request.retries)
        _log(30, self, "retry", started, "next_attempt_in=%.0fs error=%s: %s\n%s",
             countdown, type(error).__name__, error, trace)
        raise self.retry(exc=error, countdown=countdown)
    release_pipeline_slot(self.request.id)
    _log(20, self, "success", started, "run_id=%s pipeline_status=%s rows_changed=%s",
         run_id, result.get("status"), result.get("rows_changed"))
    return result


@app.task(name="reporting.dead_letter")
def dead_letter(task_id: str, task_name: str, attempt: int, error: str) -> None:
    """Consumer of the DLQ, for manual inspection only (`-Q dead_letter`): logs and acks.
    Regular workers do not listen to this queue, so dead letters wait there."""
    logger.warning("dead letter task_id=%s task=%s attempt=%d error=%s", task_id, task_name, attempt, error)
