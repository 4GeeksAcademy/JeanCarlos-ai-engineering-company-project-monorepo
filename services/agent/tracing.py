"""Structured trace of every agent run, persisted in SQLite so it can be queried afterwards
(GET /agent/runs/{run_id}/trace, or straight from the database), not only printed.

Two tables in the agent state database (same file as the LangGraph checkpoints):

- agent_runs: one row per run (question, final status, route, sources used in order,
  answer, error, timings).
- agent_trace_steps: one row per executed node, in order (node, status, duration and what
  the node produced; retrieved chunks are summarized to document/section/score).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_TEXT = 4000
# Which source each node consults: the trace says whether a run used the RAG, a tool or both.
SOURCE_OF_NODE = {"lookup_tickets": "incidents", "lookup_stock": "inventory", "retrieve_context": "knowledge_base"}

SCHEMA = """
create table if not exists agent_runs (
    run_id text primary key,
    question text not null,
    status text not null,              -- running | answered | rejected | failed
    route text,                        -- nodes executed, joined with ' > '
    sources text,                      -- sources consulted, in order: incidents > inventory > knowledge_base
    answer text,
    error text,
    started_at text not null,
    finished_at text
);
create table if not exists agent_trace_steps (
    run_id text not null references agent_runs(run_id),
    step integer not null,
    node text not null,
    status text not null,              -- ok | error
    started_at text not null,
    duration_ms real not null,
    output_json text,
    error text,
    primary key (run_id, step)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def summarize_output(update: dict[str, Any] | None) -> dict[str, Any]:
    """What a node produced, trace-sized: chunk texts are replaced by their source."""
    summary: dict[str, Any] = {}
    for key, value in (update or {}).items():
        if key == "context":
            summary["context"] = [
                {"source_document": c.get("source_document"), "chunk_index": c.get("chunk_index"),
                 "section": c.get("section"), "score": round(float(c.get("score", 0)), 4)}
                for c in value
            ]
        elif isinstance(value, str):
            summary[key] = value[:MAX_TEXT]
        else:
            summary[key] = value
    return summary


class TraceStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            columns = {row[1] for row in self._conn.execute("pragma table_info(agent_runs)")}
            if "sources" not in columns:  # databases created by Part 1
                self._conn.execute("alter table agent_runs add column sources text")

    def start_run(self, run_id: str, question: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "insert into agent_runs (run_id, question, status, started_at) values (?, ?, 'running', ?) "
                "on conflict(run_id) do update set status = 'running', finished_at = null, error = null",
                (run_id, question[:MAX_TEXT], _now()),
            )

    def record_step(self, run_id: str, node: str, *, started_at: str, duration_ms: float,
                    output: dict[str, Any] | None = None, error: str | None = None) -> int:
        with self._lock, self._conn:
            step = self._conn.execute(
                "select coalesce(max(step), 0) + 1 from agent_trace_steps where run_id = ?", (run_id,)
            ).fetchone()[0]
            self._conn.execute(
                "insert into agent_trace_steps (run_id, step, node, status, started_at, duration_ms, output_json, error) "
                "values (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, step, node, "error" if error else "ok", started_at, round(duration_ms, 2),
                 json.dumps(summarize_output(output), ensure_ascii=False) if output is not None else None, error),
            )
        return step

    def finish_run(self, run_id: str, status: str, *, answer: str | None = None, error: str | None = None) -> None:
        with self._lock, self._conn:
            executed = [row[0] for row in self._conn.execute(
                "select node from agent_trace_steps where run_id = ? and status = 'ok' order by step", (run_id,))]
            route = " > ".join(row[0] for row in self._conn.execute(
                "select node from agent_trace_steps where run_id = ? order by step", (run_id,)))
            sources = " > ".join(SOURCE_OF_NODE[node] for node in executed if node in SOURCE_OF_NODE)
            self._conn.execute(
                "update agent_runs set status = ?, route = ?, sources = ?, answer = ?, error = ?, finished_at = ? "
                "where run_id = ?",
                (status, route, sources or None, (answer or "")[:MAX_TEXT] or None, error, _now(), run_id),
            )

    def get_trace(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._conn.execute("select * from agent_runs where run_id = ?", (run_id,)).fetchone()
            if run is None:
                return None
            steps = self._conn.execute(
                "select * from agent_trace_steps where run_id = ? order by step", (run_id,)).fetchall()
        return {
            **dict(run),
            "steps": [{**{k: row[k] for k in row.keys() if k not in ("run_id", "output_json")},
                       "output": json.loads(row["output_json"]) if row["output_json"] else None}
                      for row in steps],
        }
