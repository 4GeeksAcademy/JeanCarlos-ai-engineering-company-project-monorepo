"""The agent graph: nodes, conditional edges, structural validation and compilation.

    START → receive_question ─┬─ valid ──→ retrieve_context ─┬─ chunks ───→ generate_answer ──────→ END
                              └─ invalid → reject_question → END └─ no chunks → answer_no_information → END

The graph is compiled (and validated) before any execution: compile_agent() raises
GraphStructureError on an unreachable node, a node with no way to END, a conditional route
to an undeclared node, or a node that writes keys that are not in AgentState. LangGraph's own
compile() only catches some of these (it accepts orphan and dead-end nodes).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import STATE_KEYS, AgentState
from .tracing import TraceStore

NodeFn = Callable[[AgentState], AgentState]


class GraphStructureError(ValueError):
    """The graph is not well formed; raised at compile time, never during a run."""


# ---------------------------------------------------------------- conditional edges


def route_after_receive(state: AgentState) -> str:
    return "retrieve_context" if state.get("status") == "received" else "reject_question"


def route_after_retrieve(state: AgentState) -> str:
    return "generate_answer" if state.get("context") else "answer_no_information"


NODES: dict[str, NodeFn] = {
    "receive_question": nodes.receive_question,
    "reject_question": nodes.reject_question,
    "retrieve_context": nodes.retrieve_context,
    "generate_answer": nodes.generate_answer,
    "answer_no_information": nodes.answer_no_information,
}


# ---------------------------------------------------------------- tracing


def traced(name: str, fn: NodeFn, tracer: TraceStore | None) -> Callable[..., AgentState]:
    """Wrap a node so each execution leaves a step in the trace (order, duration, output or error).
    The run id is the LangGraph thread id, shared with the checkpoints."""

    def node(state: AgentState, config: RunnableConfig) -> AgentState:
        run_id = (config.get("configurable") or {}).get("thread_id")
        started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        start = time.perf_counter()
        try:
            update = fn(state)
        except Exception as error:
            if tracer and run_id:
                tracer.record_step(run_id, name, started_at=started_at,
                                   duration_ms=(time.perf_counter() - start) * 1000,
                                   error=f"{type(error).__name__}: {error}")
            raise
        if tracer and run_id:
            tracer.record_step(run_id, name, started_at=started_at,
                               duration_ms=(time.perf_counter() - start) * 1000, output=update)
        return update

    node.__name__ = name
    return node


# ---------------------------------------------------------------- build / validate / compile


def build_graph(tracer: TraceStore | None = None, node_fns: dict[str, NodeFn] | None = None) -> StateGraph:
    graph = StateGraph(AgentState)
    for name, fn in (node_fns or NODES).items():
        graph.add_node(name, traced(name, fn, tracer))
    graph.add_edge(START, "receive_question")
    graph.add_conditional_edges("receive_question", route_after_receive,
                                {"retrieve_context": "retrieve_context", "reject_question": "reject_question"})
    graph.add_conditional_edges("retrieve_context", route_after_retrieve,
                                {"generate_answer": "generate_answer", "answer_no_information": "answer_no_information"})
    graph.add_edge("reject_question", END)
    graph.add_edge("generate_answer", END)
    graph.add_edge("answer_no_information", END)
    return graph


def validate_structure(graph: StateGraph, node_outputs: dict[str, frozenset[str]] | None = None) -> None:
    """Fail clearly on structural errors that LangGraph's compile() lets through."""
    node_outputs = nodes.NODE_OUTPUTS if node_outputs is None else node_outputs
    names = set(graph.nodes)
    edges: dict[str, set[str]] = {}
    for source, target in graph.edges:
        edges.setdefault(source, set()).add(target)
    for source, branches in graph.branches.items():
        for branch in branches.values():
            if not branch.ends:
                raise GraphStructureError(f"Conditional edge from '{source}' must declare its targets (path_map)")
            for target in branch.ends.values():
                if target != END and target not in names:
                    raise GraphStructureError(f"Conditional edge from '{source}' routes to unknown node '{target}'")
                edges.setdefault(source, set()).add(target)

    reachable, frontier = set(), [START]
    while frontier:
        for target in edges.get(frontier.pop(), ()):
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    if unreachable := sorted(names - reachable):
        raise GraphStructureError(f"Nodes not connected to START: {unreachable}")

    reaches_end = {END}
    changed = True
    while changed:
        changed = False
        for source, targets in edges.items():
            if source not in reaches_end and targets & reaches_end:
                reaches_end.add(source)
                changed = True
    if dead_ends := sorted(names - reaches_end):
        raise GraphStructureError(f"Nodes with no path to END: {dead_ends}")

    for name in sorted(names):
        if name not in node_outputs:
            raise GraphStructureError(f"Node '{name}' does not declare the state keys it writes")
        if unknown := sorted(node_outputs[name] - STATE_KEYS):
            raise GraphStructureError(f"Node '{name}' writes keys that are not in AgentState: {unknown}")


def compile_agent(checkpointer=None, tracer: TraceStore | None = None, node_fns: dict[str, NodeFn] | None = None):
    """Build, validate and compile. Any structural problem raises here, before a run."""
    graph = build_graph(tracer, node_fns)
    validate_structure(graph)
    try:
        return graph.compile(checkpointer=checkpointer)
    except ValueError as error:  # LangGraph's own checks (unknown node, missing entry point, …)
        raise GraphStructureError(str(error)) from error


# Compiled once at import (no I/O): the API fails at startup, not on the first request,
# if the structure is broken. The runtime compiles again with its checkpointer and tracer.
STRUCTURE = compile_agent()
