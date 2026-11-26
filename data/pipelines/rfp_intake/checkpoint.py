"""Checkpointer and thread identity of the approval graphs (Hito 9 Parte 3).

- PostgreSQL (production, the same database as the tickets): `PostgresSaver` on a psycopg pool;
  its tables (checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations) are created
  by `setup()` and by the Supabase migration.
- SQLite (tests and local runs on a SQLite engine): `SqliteSaver` on a file next to the database.
  The in-memory checkpointer is never used: a paused approval must survive a restart of the API.

Thread ids are namespaced by ticket, and by department for the department branches, so two
tickets (or two departments of the same ticket) never share a checkpoint:

    rfp-{ticket_id}                coordinator of the ticket (arbitration, synthesis, closing)
    rfp-{ticket_id}:{department}   approval branch of one department

Every resume of a thread runs under `thread_lock`, so two simultaneous decisions on the SAME
thread are serialized; decisions on different departments use different threads and locks and
run in parallel.
"""

from __future__ import annotations

import atexit
import sqlite3
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine


def ticket_thread(ticket_id: str) -> str:
    return f"rfp-{ticket_id}"


def department_thread(ticket_id: str, department_id: str) -> str:
    return f"rfp-{ticket_id}:{department_id}"


def _is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


@lru_cache(maxsize=8)
def _postgres_saver(url: str):
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(url, min_size=1, max_size=8, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    atexit.register(pool.close)  # clean exit of the CLI / E2E script and of the API
    saver = PostgresSaver(pool)
    saver.setup()
    return saver


@lru_cache(maxsize=8)
def _sqlite_saver(path: str):
    from langgraph.checkpoint.sqlite import SqliteSaver

    saver = SqliteSaver(sqlite3.connect(path, check_same_thread=False, timeout=30))
    saver.setup()
    return saver


def checkpointer(engine: Engine):
    if _is_postgres(engine):
        url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        return _postgres_saver(url)
    database = engine.url.database
    path = f"{database}.checkpoints" if database and database != ":memory:" else str(Path.cwd() / "rfp-checkpoints.sqlite")
    return _sqlite_saver(path)


def delete_threads(engine: Engine, thread_ids: list[str]) -> None:
    saver = checkpointer(engine)
    for thread_id in thread_ids:
        saver.delete_thread(thread_id)


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@contextmanager
def thread_lock(engine: Engine, thread_id: str):
    """Serialize the resumes of one thread: in-process lock + PostgreSQL advisory lock (several
    API workers)."""
    with _locks_guard:
        lock = _locks.setdefault(thread_id, threading.Lock())
    with lock:
        if not _is_postgres(engine):
            yield
            return
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(hashtextextended(:k, 0))"), {"k": thread_id})
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": thread_id})
                connection.commit()
