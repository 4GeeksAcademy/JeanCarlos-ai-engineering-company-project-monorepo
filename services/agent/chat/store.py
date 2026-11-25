"""Chat sessions and messages of the Compliance assistant over WebSocket (Sistemas en Tiempo Real,
Parte 2), in the agent's state database (same SQLite file as the checkpoints and the trace).

BRIEFING §3 — ChatSession: session_id, agent_id, user_id (the staff member, never a patient),
status (active / interrupted / closed), created_at. Each message keeps its role, its text and its
status: an assistant message cut by the user stays as it was shown, with status `interrupted`
(never deleted nor overwritten); the next answer is a new message.

No PHI: user text is stored only after the agent's patient-identifier screen (the redacted form),
assistant text only after the output guard.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_ID = "compliance_assistant"
SESSION_STATUSES = ("active", "interrupted", "closed")
MESSAGE_STATUSES = ("complete", "streaming", "interrupted", "failed")

_SCHEMA = """
create table if not exists chat_session (
    session_id text primary key,
    agent_id text not null,
    user_id text not null,
    status text not null check (status in ('active', 'interrupted', 'closed')),
    created_at text not null,
    updated_at text not null
);
create table if not exists chat_message (
    message_id text primary key,
    session_id text not null references chat_session (session_id) on delete cascade,
    seq integer not null,
    role text not null check (role in ('user', 'assistant')),
    text text not null,
    status text not null check (status in ('complete', 'streaming', 'interrupted', 'failed')),
    run_id text,
    created_at text not null,
    updated_at text not null
);
create index if not exists ix_chat_message_session on chat_message (session_id, seq);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_session_id() -> str:
    return f"chat_{uuid.uuid4().hex[:12]}"


def new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:12]}"


class ChatStore:
    def __init__(self, database: Path | str):
        if str(database) != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(database), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            # an API restart in the middle of a generation: what was shown stays, marked interrupted
            self._conn.execute("update chat_message set status = 'interrupted' where status = 'streaming'")

    # ------------------------------------------------------------ sessions

    def create_session(self, user_id: str) -> dict[str, Any]:
        now = _now()
        session = {"session_id": new_session_id(), "agent_id": AGENT_ID, "user_id": user_id, "status": "active",
                   "created_at": now}
        with self._lock, self._conn:
            self._conn.execute("insert into chat_session values (?, ?, ?, ?, ?, ?)",
                               (session["session_id"], AGENT_ID, user_id, "active", now, now))
        return session

    def session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("select session_id, agent_id, user_id, status, created_at from chat_session "
                                     "where session_id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def sessions_of(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "select s.session_id, s.agent_id, s.user_id, s.status, s.created_at, "
                "(select text from chat_message m where m.session_id = s.session_id and m.role = 'user' "
                " order by seq limit 1) as first_message "
                "from chat_session s where user_id = ? order by s.updated_at desc limit ?", (user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, session_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("update chat_session set status = ?, updated_at = ? where session_id = ?",
                               (status, _now(), session_id))

    # ------------------------------------------------------------ messages

    def add_message(self, session_id: str, role: str, text: str, status: str, *, run_id: str | None = None,
                    message_id: str | None = None) -> dict[str, Any]:
        now = _now()
        message_id = message_id or new_message_id()
        with self._lock, self._conn:
            seq = self._conn.execute("select coalesce(max(seq), 0) + 1 from chat_message where session_id = ?",
                                     (session_id,)).fetchone()[0]
            self._conn.execute("insert into chat_message values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (message_id, session_id, seq, role, text, status, run_id, now, now))
            self._conn.execute("update chat_session set updated_at = ? where session_id = ?", (now, session_id))
        return {"message_id": message_id, "role": role, "text": text, "status": status, "created_at": now}

    def finish_message(self, message_id: str, text: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("update chat_message set text = ?, status = ?, updated_at = ? where message_id = ?",
                               (text, status, _now(), message_id))

    def message(self, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("select message_id, role, text, status, run_id, created_at from chat_message "
                                     "where message_id = ?", (message_id,)).fetchone()
        return dict(row) if row else None

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("select message_id, role, text, status, created_at from chat_message "
                                      "where session_id = ? order by seq", (session_id,)).fetchall()
        return [dict(r) for r in rows]
