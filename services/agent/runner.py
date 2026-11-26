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
    conversation_id: str | None = None
    memory: dict[str, Any] | None = None  # pending proposal / decision of this turn


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
        proposal = state.get("memory_proposal") or {}
        memory = {"proposal": ({"id": proposal["proposal_id"], "kind": proposal["kind"], "content": proposal["content"]}
                               if proposal.get("outcome") == "proposed" else None),
                  "blocked_phi": proposal.get("outcome") == "blocked_phi" or state.get("status") == "phi_refused",
                  "decision": ({k: state["memory_decision"].get(k) for k in ("proposal_id", "status", "content")}
                               if state.get("memory_decision") else None)}
        return AgentResult(run_id, "answered", state["answer"], state.get("conversation_id"), memory)

    @staticmethod
    def screen(question: str) -> tuple[str, list[str]]:
        """Sanitization + patient-identifier guard BEFORE the graph: direct identifiers or a combination of
        quasi-identifiers (age + diagnosis + location…) never reach the state, the checkpoints or
        the trace; the graph only sees a placeholder and refuses, asking to reformulate."""
        from .harness.rules import detect_patient_identifiers
        from .harness.sanitize import InvalidInput, sanitize_input

        try:  # explicit sanitization first (NFKC, invisible/bidi/control characters): the guards
            # and the model see the same clean text; empty/too long is decided by the graph itself
            question = sanitize_input(question or "", max_chars=10_000, min_chars=0).text
        except InvalidInput:
            question = (question or "")[:10_000]
        categories = detect_patient_identifiers(question)
        if not categories:
            return question, []
        return f"[mensaje redactado: posible PHI ({', '.join(categories)})]", categories

    def _failed(self, run_id: str, error: BaseException) -> AgentRunError:
        trace = self.tracer.get_trace(run_id) or {"steps": []}
        failed = next((s["node"] for s in reversed(trace["steps"]) if s["status"] == "error"), None)
        self.tracer.finish_run(run_id, "failed", error=f"{failed or '?'}: {type(error).__name__}: {error}")
        return AgentRunError(run_id, failed, error)

    def run(self, question: str, run_id: str | None = None, *, conversation_id: str | None = None,
            actor: str | None = None) -> AgentResult:
        """`conversation_id` groups turns (a memory proposal is answered in the next turn);
        `actor` is the authenticated staff member, recorded as who approved a memory."""
        from .memory.store import get_store

        run_id = run_id or str(uuid.uuid4())
        conversation_id = conversation_id or str(uuid.uuid4())
        safe_question, phi = self.screen(question)
        pending = get_store().pending(conversation_id)
        inputs = {"question": safe_question, "run_id": run_id, "conversation_id": conversation_id, "actor": actor or "unknown",
                  "phi_categories": phi,
                  "pending_proposal": ({"id": pending["id"], "kind": pending["kind"], "content": pending["content"]}
                                       if pending else None)}
        self.tracer.start_run(run_id, safe_question)
        try:
            state = self.graph.invoke(inputs, self.config(run_id))
        except Exception as error:
            raise self._failed(run_id, error) from error
        return self._finish(run_id, state)

    def stream_run(self, question: str, run_id: str, *, conversation_id: str, actor: str | None,
                   on_token, cancel) -> AgentResult:
        """Same run as run(), streamed (WebSocket chat): LangGraph `custom` mode carries every token
        the generation model produces (data/pipelines/rag.py) to `on_token`, and `values` the
        state. `cancel` (threading.Event) aborts: the model stream is closed by the node that is
        generating, and no further node is scheduled. Raises GenerationCancelled when aborted."""
        from data.pipelines.rag import GenerationCancelled

        from .memory.store import get_store

        safe_question, phi = self.screen(question)
        pending = get_store().pending(conversation_id)
        inputs = {"question": safe_question, "run_id": run_id, "conversation_id": conversation_id,
                  "actor": actor or "unknown", "phi_categories": phi,
                  "pending_proposal": ({"id": pending["id"], "kind": pending["kind"], "content": pending["content"]}
                                       if pending else None)}
        config = {"configurable": {"thread_id": run_id, "stream_tokens": True, "generation_cancel": cancel}}
        self.tracer.start_run(run_id, safe_question)
        state: dict[str, Any] | None = None
        try:
            for mode, chunk in self.graph.stream(inputs, config, stream_mode=["custom", "values"]):
                if cancel.is_set():
                    raise GenerationCancelled("the user interrupted the generation")
                if mode == "custom" and isinstance(chunk, dict) and "token" in chunk:
                    on_token(chunk["token"])
                elif mode == "values":
                    state = chunk
        except GenerationCancelled:
            self.tracer.finish_run(run_id, "interrupted", error="interrupted by the user")
            raise
        except Exception as error:
            if cancel.is_set():
                self.tracer.finish_run(run_id, "interrupted", error="interrupted by the user")
                raise GenerationCancelled("the user interrupted the generation") from error
            raise self._failed(run_id, error) from error
        return self._finish(run_id, state or {})

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


def run_agent(question: str, *, conversation_id: str | None = None, actor: str | None = None) -> AgentResult:
    return get_runtime().run(question, conversation_id=conversation_id, actor=actor)
