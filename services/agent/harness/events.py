"""Observability of the harness: one row per guardrail activation, and a summary.

Every block, redirect or sanitization writes an event with the guard, the action, the failure
type (structural / content / security) and the category that fired — never the user's text, so
the log stays free of PHI. `summary()` counts them (GET /agent/guardrails/summary and
`python -m services.agent.harness summary`), optionally since a timestamp or for one
conversation, which is how a test session is summarized.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

FailureType = Literal["structural", "content", "security"]
Action = Literal["blocked", "redirected", "sanitized", "fallback"]

logger = logging.getLogger("api.guardrails")

SCHEMA = """
create table if not exists guardrail_events (
    seq integer primary key autoincrement,
    at text not null,
    run_id text,
    conversation_id text,
    guard text not null,
    action text not null check (action in ('blocked', 'redirected', 'sanitized', 'fallback')),
    failure_type text not null check (failure_type in ('structural', 'content', 'security')),
    category text not null,
    details text
);
create index if not exists guardrail_events_conversation on guardrail_events (conversation_id, seq);
create table if not exists conversation_flags (
    conversation_id text not null,
    flag text not null,
    at text not null,
    primary key (conversation_id, flag)
);
"""


class GuardEvents:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def record(self, guard: str, action: Action, failure_type: FailureType, category: str, *,
               run_id: str | None = None, conversation_id: str | None = None, **details: Any) -> None:
        at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._lock, self._conn:
            self._conn.execute(
                "insert into guardrail_events (at, run_id, conversation_id, guard, action, failure_type, category, details) "
                "values (?, ?, ?, ?, ?, ?, ?, ?)",
                (at, run_id, conversation_id, guard, action, failure_type, category,
                 json.dumps(details, ensure_ascii=False) if details else None))
        logger.warning("guardrail guard=%s action=%s failure_type=%s category=%s run_id=%s conversation_id=%s",
                       guard, action, failure_type, category, run_id, conversation_id)

    def set_flag(self, conversation_id: str, flag: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("insert or ignore into conversation_flags values (?, ?, ?)",
                               (conversation_id, flag, datetime.now(timezone.utc).isoformat(timespec="seconds")))

    def has_flag(self, conversation_id: str, flag: str) -> bool:
        with self._lock:
            return self._conn.execute("select 1 from conversation_flags where conversation_id = ? and flag = ?",
                                      (conversation_id, flag)).fetchone() is not None

    def count(self, conversation_id: str, category: str) -> int:
        with self._lock:
            return self._conn.execute("select count(*) from guardrail_events where conversation_id = ? and category = ?",
                                      (conversation_id, category)).fetchone()[0]

    def categories_in_conversation(self, conversation_id: str) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute(
                "select category from guardrail_events where conversation_id = ?", (conversation_id,))}

    def events(self, *, since: str | None = None, conversation_id: str | None = None) -> list[dict[str, Any]]:
        query, params = "select * from guardrail_events where 1=1", []
        if since:
            query += " and at >= ?"
            params.append(since)
        if conversation_id:
            query += " and conversation_id = ?"
            params.append(conversation_id)
        with self._lock:
            rows = self._conn.execute(query + " order by seq", params).fetchall()
        return [{**dict(r), "details": json.loads(r["details"]) if r["details"] else None} for r in rows]

    def summary(self, *, since: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
        rows = self.events(since=since, conversation_id=conversation_id)
        return {
            "since": since, "conversation_id": conversation_id, "total": len(rows),
            "by_guard": dict(Counter(r["guard"] for r in rows)),
            "by_failure_type": dict(Counter(r["failure_type"] for r in rows)),
            "by_action": dict(Counter(r["action"] for r in rows)),
            "by_category": dict(Counter(r["category"] for r in rows)),
            "by_guard_and_action": dict(Counter(f"{r['guard']}:{r['action']}" for r in rows)),
        }


_events: GuardEvents | None = None
_lock = threading.Lock()


def events_database() -> Path:
    from data.process.rag import REPO_ROOT

    return Path(os.environ.get("AGENT_STATE_DIR", REPO_ROOT / "data" / "agent_state")) / "guardrails.sqlite"


def get_events() -> GuardEvents:
    global _events
    with _lock:
        if _events is None:
            _events = GuardEvents(events_database())
        return _events
