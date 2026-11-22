"""GET /tasks/{task_id}: state of an asynchronous Celery task, read from the Redis result backend."""

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from data.pipelines.connections import MissingConnection, reporting_reader_engine
from services.celery_app import read_task_meta
from services.task_dead_letters import get_dead_letter

from ..core.dependencies import CurrentUser
from ..schemas.tasks import DeadLetter, TaskStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = logging.getLogger("api.tasks")
TASK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@router.get("/{task_id}", response_model=TaskStatus, responses={404: {"description": "Unknown task"}})
def task_status(task_id: Annotated[str, Path(max_length=36)], _user: CurrentUser) -> TaskStatus:
    if not TASK_ID.match(task_id):
        raise HTTPException(status_code=404, detail="Unknown task")
    try:
        # Celery reports PENDING for any id it does not know; the producer stores a PENDING
        # record when it enqueues, so a missing record means the task does not exist.
        meta = read_task_meta(task_id)
    except RedisError as error:
        logger.error("Task backend unavailable: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Task queue unavailable") from error
    if meta is None:
        raise HTTPException(status_code=404, detail="Unknown task")

    state = str(meta.get("status", "PENDING")).lower()
    if state in {"failure", "retry"}:
        result: Any = {"error": meta.get("error")}
    elif state == "success":
        result = meta.get("result")
    else:
        result = None
    retries = meta.get("retries")
    status = TaskStatus(
        task_id=task_id, status=state, result=result,
        attempt=retries + 1 if isinstance(retries, int) and state != "pending" else None,
    )
    if state == "failure":
        try:
            dead = get_dead_letter(reporting_reader_engine(), task_id)
        except (MissingConnection, SQLAlchemyError) as error:
            logger.error("Dead letter lookup failed: %s", type(error).__name__)
            dead = None
        if dead:
            status.dead_letter = DeadLetter(
                **{key: dead[key] for key in ("attempt", "max_retries", "error_type", "error_message")},
                failed_at=dead["failed_at"].isoformat(),
            )
            status.attempt = dead["attempt"]
    return status
