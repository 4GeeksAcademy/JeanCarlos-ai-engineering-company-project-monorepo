"""GET /tasks/{task_id}: Celery states read from the Redis result backend (stubbed)."""

from datetime import datetime, timezone

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from services.api.routes import tasks as tasks_route

TASK_ID = "5421a39f-d432-4383-aca6-b5e64ccd2611"


@pytest.fixture
def headers(user_factory, auth_headers):
    return auth_headers(user_factory(username="claire"))


@pytest.fixture
def backend(monkeypatch):
    """Stub of services.celery_app.read_task_meta (the Redis result backend)."""
    def install(meta=None, exists=True, error=None):
        def read(task_id):
            if error:
                raise error
            return meta if exists else None
        monkeypatch.setattr(tasks_route, "read_task_meta", read)
    return install


@pytest.mark.parametrize("state, retries, expected_attempt", [
    ("PENDING", None, None), ("STARTED", 0, 1), ("RETRY", 1, 2),
])
def test_in_flight_states(client, headers, backend, state, retries, expected_attempt):
    backend(meta={"status": state, "result": None, "retries": retries,
                  **({"error": "RuntimeError: boom"} if state == "RETRY" else {})})
    body = client.get(f"/tasks/{TASK_ID}", headers=headers).json()
    assert body["task_id"] == TASK_ID and body["status"] == state.lower() and body["attempt"] == expected_attempt
    if state == "RETRY":
        assert body["result"] == {"error": "RuntimeError: boom"}


def test_success_returns_the_result(client, headers, backend):
    backend(meta={"status": "SUCCESS", "result": {"run_id": TASK_ID, "status": "completed"}, "retries": 0})
    body = client.get(f"/tasks/{TASK_ID}", headers=headers).json()
    assert body == {"task_id": TASK_ID, "status": "success", "attempt": 1,
                    "result": {"run_id": TASK_ID, "status": "completed"}, "dead_letter": None}


def test_failure_includes_the_dead_letter_record(client, headers, backend, monkeypatch):
    backend(meta={"status": "FAILURE", "result": None, "retries": 3,
                  "error": "EmptyDataError: No columns to parse from file"})
    monkeypatch.setattr(tasks_route, "reporting_reader_engine", lambda: "reader")
    monkeypatch.setattr(tasks_route, "get_dead_letter", lambda engine, task_id: {
        "attempt": 4, "max_retries": 3, "error_type": "EmptyDataError",
        "error_message": "No columns to parse from file",
        "failed_at": datetime(2026, 11, 22, 15, 7, 58, tzinfo=timezone.utc)})
    body = client.get(f"/tasks/{TASK_ID}", headers=headers).json()
    assert body["status"] == "failure" and body["attempt"] == 4
    assert body["result"] == {"error": "EmptyDataError: No columns to parse from file"}
    assert body["dead_letter"]["error_type"] == "EmptyDataError" and body["dead_letter"]["max_retries"] == 3


def test_unknown_or_malformed_ids_are_404(client, headers, backend):
    backend(exists=False)
    assert client.get(f"/tasks/{TASK_ID}", headers=headers).status_code == 404
    assert client.get("/tasks/not-a-task-id", headers=headers).status_code == 404


def test_requires_session_and_maps_redis_errors_to_503(client, headers, backend):
    assert client.get(f"/tasks/{TASK_ID}").status_code == 401
    backend(error=RedisConnectionError("redis down"))
    assert client.get(f"/tasks/{TASK_ID}", headers=headers).status_code == 503
