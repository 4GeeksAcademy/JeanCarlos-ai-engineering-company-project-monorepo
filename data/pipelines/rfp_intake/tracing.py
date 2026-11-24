"""Traceability of the RFP flow (Hito 9 Parte 3): every node execution of the three graphs is
recorded with its agent, input, output, status and timestamps — in the `rfp_trace` table (one row
per execution, ordered by id) and, for the approval graphs, also in the graph state (`trace`).

`traced(agent, fn)` wraps a LangGraph node. The agent can be a fixed name ("agent:classifier") or
a function of the node input ("human:Tom Callahan"). A node that pauses on `interrupt()` is
recorded as `interrupted`; when it is resumed it runs again and is recorded as `ok`, so the trace
shows the pause and the resume in order.

No PHI: input and output are summarized, never copied. Long strings become their length and a
short hash; short strings are screened with the PHI rules and replaced by the category if they
match; Part 1 nodes that see the document before the PHI screening record only the shape (keys
and sizes) of their input.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
from datetime import datetime
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt

from . import phi

logger = logging.getLogger("api.rfp_intake")

MAX_STRING = 160
MAX_ITEMS = 8


def summarize(value: Any, depth: int = 0, *, shape_only: bool = False) -> Any:
    if isinstance(value, str):
        if shape_only or len(value) > MAX_STRING:
            return f"<str {len(value)} chars sha256:{hashlib.sha256(value.encode()).hexdigest()[:10]}>"
        categories = phi.find_phi(value)
        return f"[PHI:{','.join(categories)}]" if categories else value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if depth >= 3:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        return {str(k): summarize(v, depth + 1, shape_only=shape_only) for k, v in value.items()
                if k not in ("trace", "engine")}
    if isinstance(value, (list, tuple)):
        items = [summarize(v, depth + 1, shape_only=shape_only) for v in list(value)[:MAX_ITEMS]]
        return items + ([f"<+{len(value) - MAX_ITEMS} more>"] if len(value) > MAX_ITEMS else [])
    return f"<{type(value).__name__}>"


def ticket_of(state: Any) -> str | None:
    if not isinstance(state, dict):
        return None
    return state.get("ticket_id") or (state.get("ctx") or {}).get("ticket_id")


def traced(agent: str | Callable[[dict], str], fn: Callable, *, node: str | None = None, in_state: bool = False,
           shape_only: bool = False) -> Callable:
    """Wrap a node: run it, then record agent / input / output / timestamps (and re-raise)."""
    name = node or fn.__name__
    wants_config = len(inspect.signature(fn).parameters) > 1

    def wrapper(state, config: RunnableConfig):
        from . import store  # late import: store imports models only

        started = datetime.now().astimezone()
        configurable = config.get("configurable", {})
        who = agent(state) if callable(agent) else agent
        entry = {"node": name, "agent": who, "thread_id": configurable.get("thread_id"),
                 "input": summarize(state, shape_only=shape_only), "started_at": started.isoformat()}

        def record(status: str, output: Any) -> dict:
            row = {**entry, "status": status, "output": output, "finished_at": datetime.now().astimezone().isoformat()}
            ticket_id = ticket_of(state)
            engine = configurable.get("engine")
            if engine is not None and ticket_id:
                try:
                    store.record_trace(engine, ticket_id, row)
                except Exception as error:  # tracing never breaks the flow
                    logger.warning("rfp trace write failed node=%s error=%s", name, type(error).__name__)
            return row

        try:
            output = fn(state, config) if wants_config else fn(state)
        except GraphInterrupt as pause:
            record("interrupted", {"interrupt": summarize([i.value for i in pause.args[0]] if pause.args else None)})
            raise
        except Exception as error:
            record("error", {"error": type(error).__name__})
            raise
        row = record("ok", summarize(output))
        if in_state and isinstance(output, dict):
            output = {**output, "trace": [row]}
        return output

    wrapper.__name__ = name
    return wrapper
