"""Run the compiled agent with checkpointing and tracing.

- Checkpoints: LangGraph SqliteSaver, one thread per run (thread_id = run_id). A checkpoint
  is written after every node, so a run can be inspected step by step (checkpoint_history)
  or resumed from the last completed node (resume_agent) if a later node failed.
- Trace: services.agent.tracing.TraceStore, same SQLite file.

The database lives in AGENT_STATE_DIR (default data/agent_state/, gitignored; /data/agent
in the API container).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from data.process.rag import REPO_ROOT

from .graph import compile_agent
from .tracing import TraceStore


class AgentRejected(ValueError):
    """The question was routed to reject_question (empty, too short or too long)."""

    def __init__(self, run_id: str, message: str):
        super().__init__(message)
        self.run_id = run_id


class AgentRunError(RuntimeError):
    """A node failed. Carries the run id and node so the trace can be inspected."""

    def __init__(self, run_id: str, node: str | None, cause: BaseException):
        super().__init__(f"Agent run {run_id} failed in node {node or '?'}: {type(cause).__name__}")
        self.run_id, self.node, self.cause = run_id, node, cause


@dataclass(frozen=True)
class AgentResult:
    run_id: str
    status: str
    answer: str


class AgentRuntime:
    def __init__(self, database: Path | str, node_fns=None):
        if str(database) != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.tracer = TraceStore(database)
        self._conn = sqlite3.connect(str(database), check_same_thread=False)
        self.checkpointer = SqliteSaver(self._conn)
        self.graph = compile_agent(checkpointer=self.checkpointer, tracer=self.tracer, node_fns=node_fns)

    @staticmethod
    def config(run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    def _finish(self, run_id: str, state: dict[str, Any]) -> AgentResult:
        if state.get("status") == "rejected":
            self.tracer.finish_run(run_id, "rejected", error=state.get("error"))
            raise AgentRejected(run_id, state.get("error") or "La pregunta no es válida.")
        self.tracer.finish_run(run_id, "answered", answer=state.get("answer"))
        return AgentResult(run_id, "answered", state["answer"])

    def _failed(self, run_id: str, error: BaseException) -> AgentRunError:
        trace = self.tracer.get_trace(run_id) or {"steps": []}
        failed = next((s["node"] for s in reversed(trace["steps"]) if s["status"] == "error"), None)
        self.tracer.finish_run(run_id, "failed", error=f"{failed or '?'}: {type(error).__name__}: {error}")
        return AgentRunError(run_id, failed, error)

    def run(self, question: str, run_id: str | None = None) -> AgentResult:
        run_id = run_id or str(uuid.uuid4())
        self.tracer.start_run(run_id, question)
        try:
            state = self.graph.invoke({"question": question}, self.config(run_id))
        except Exception as error:
            raise self._failed(run_id, error) from error
        return self._finish(run_id, state)

    def resume(self, run_id: str) -> AgentResult:
        """Continue a failed run from its last checkpoint: completed nodes are not run again."""
        snapshot = self.graph.get_state(self.config(run_id))
        if not snapshot.values:
            raise KeyError(run_id)
        self.tracer.start_run(run_id, snapshot.values.get("question", ""))
        try:
            state = self.graph.invoke(None, self.config(run_id)) if snapshot.next else snapshot.values
        except Exception as error:
            raise self._failed(run_id, error) from error
        return self._finish(run_id, state)

    def checkpoint_history(self, run_id: str) -> list[dict[str, Any]]:
        """Checkpoints of a run, oldest first: step, next node(s) and the state keys present."""
        history = list(self.graph.get_state_history(self.config(run_id)))
        return [
            {"step": s.metadata.get("step"), "checkpoint_id": s.config["configurable"]["checkpoint_id"],
             "next": list(s.next), "status": s.values.get("status"), "state_keys": sorted(s.values)}
            for s in reversed(history)
        ]

    def trace(self, run_id: str) -> dict[str, Any] | None:
        trace = self.tracer.get_trace(run_id)
        if trace is not None:
            trace["checkpoints"] = self.checkpoint_history(run_id)
        return trace


_runtime: AgentRuntime | None = None
_lock = threading.Lock()


def state_database() -> Path:
    return Path(os.environ.get("AGENT_STATE_DIR", REPO_ROOT / "data" / "agent_state")) / "agent.sqlite"


def get_runtime() -> AgentRuntime:
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = AgentRuntime(state_database())
        return _runtime


def run_agent(question: str) -> AgentResult:
    return get_runtime().run(question)
