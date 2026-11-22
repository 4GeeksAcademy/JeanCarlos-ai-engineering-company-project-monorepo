"""Dead Letter Queue records: reporting.task_dead_letters (ticket DEV-55).

Written by the Celery worker when a task exhausts its retries; read by the API to
explain a `failure` in GET /tasks/{task_id}. Plain SQLAlchemy Core.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, Table, Text, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine

metadata = MetaData()
task_dead_letters = Table(
    "task_dead_letters",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("task_id", Text, nullable=False, unique=True),
    Column("task_name", Text, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("max_retries", Integer, nullable=False),
    Column("error_type", Text, nullable=False),
    Column("error_message", Text, nullable=False),
    Column("traceback", Text),
    Column("task_kwargs", JSONB, nullable=False),
    Column("failed_at", DateTime(timezone=True), nullable=False),
    schema="reporting",
)

_MAX_TEXT = 8000


def record_dead_letter(
    engine: Engine,
    *,
    task_id: str,
    task_name: str,
    attempt: int,
    max_retries: int,
    error: BaseException,
    traceback: str | None,
    task_kwargs: dict[str, Any],
) -> None:
    """Idempotent per task_id: a redelivered final attempt does not create a second row."""
    with engine.begin() as connection:
        connection.execute(
            insert(task_dead_letters)
            .values(
                task_id=task_id, task_name=task_name, attempt=attempt, max_retries=max_retries,
                error_type=type(error).__name__, error_message=str(error)[:_MAX_TEXT] or type(error).__name__,
                traceback=(traceback or "")[:_MAX_TEXT] or None, task_kwargs=task_kwargs,
                failed_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=[task_dead_letters.c.task_id])
        )


def get_dead_letter(engine: Engine, task_id: str) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = connection.execute(select(task_dead_letters).where(task_dead_letters.c.task_id == task_id)).first()
    return dict(row._mapping) if row else None
