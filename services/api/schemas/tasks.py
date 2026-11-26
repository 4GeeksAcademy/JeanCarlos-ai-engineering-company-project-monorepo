from typing import Any, Literal

from pydantic import BaseModel


class DeadLetter(BaseModel):
    attempt: int
    max_retries: int
    error_type: str
    error_message: str
    failed_at: str


class TaskStatus(BaseModel):
    task_id: str
    # Celery states, lowercased: pending, started, retry, success, failure (revoked if cancelled).
    status: Literal["pending", "started", "retry", "success", "failure", "revoked"]
    attempt: int | None = None
    result: Any = None
    dead_letter: DeadLetter | None = None
