"""The agent graph: nodes, conditional edges, structural validation and compilation.

    START → receive_question ─ invalid → reject_question → END
                             └ valid → plan_sources → (sources the plan needs, in this order)
        lookup_tickets ─ ok ─────────────┐   (incident manager, live)
                       └ failed → ticket_fallback ┤
        lookup_stock ─── ok ─────────────┤   (inventory, live)
                       └ failed → stock_fallback ─┤
        retrieve_context ─ chunks → generate_answer ───────┤   (knowledge base, RAG)
                         └ none → answer_no_information ────┤
                                                       compose_answer → END

Every source node is followed by the same decision (next_source): the next source the plan
needs that has not run yet, or compose_answer. A question can use the RAG, a tool, or both.

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
    return "plan_sources" if state.get("status") == "received" else "reject_question"


def next_source(state: AgentState) -> str:
    """Next source the plan needs and that has not been consulted yet: tools first (live
    data), then the knowledge base; compose_answer when nothing is left."""
    plan = state.get("plan") or {}
    if plan.get("ticket") and "ticket_result" not in state:
        return "lookup_tickets"
    if plan.get("inventory") and "stock_result" not in state:
        return "lookup_stock"
    if plan.get("knowledge_base") and "context" not in state:
        return "retrieve_context"
    return "compose_answer"


def route_after_tickets(state: AgentState) -> str:
    return next_source(state) if state["ticket_result"]["status"] == "ok" else "ticket_fallback"


def route_after_stock(state: AgentState) -> str:
    return next_source(state) if state["stock_result"]["status"] == "ok" else "stock_fallback"


def route_after_retrieve(state: AgentState) -> str:
    return "generate_answer" if state.get("context") else "answer_no_information"


NODES: dict[str, NodeFn] = {
    "receive_question": nodes.receive_question,
    "reject_question": nodes.reject_question,
    "plan_sources": nodes.plan_sources,
    "lookup_tickets": nodes.lookup_tickets,
    "ticket_fallback": nodes.ticket_fallback,
    "lookup_stock": nodes.lookup_stock,
    "stock_fallback": nodes.stock_fallback,
    "retrieve_context": nodes.retrieve_context,
    "generate_answer": nodes.generate_answer,
    "answer_no_information": nodes.answer_no_information,
    "compose_answer": nodes.compose_answer,
}

# Where each source node may go next (path maps of the conditional edges).
_AFTER_PLAN = ["lookup_tickets", "lookup_stock", "retrieve_context", "compose_answer"]
_AFTER_TICKETS = ["lookup_stock", "retrieve_context", "compose_answer"]
_AFTER_STOCK = ["retrieve_context", "compose_answer"]


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
                                {"plan_sources": "plan_sources", "reject_question": "reject_question"})
    graph.add_conditional_edges("plan_sources", next_source, {n: n for n in _AFTER_PLAN})
    graph.add_conditional_edges("lookup_tickets", route_after_tickets,
                                {n: n for n in ["ticket_fallback", *_AFTER_TICKETS]})
    graph.add_conditional_edges("ticket_fallback", next_source, {n: n for n in _AFTER_TICKETS})
    graph.add_conditional_edges("lookup_stock", route_after_stock, {n: n for n in ["stock_fallback", *_AFTER_STOCK]})
    graph.add_conditional_edges("stock_fallback", next_source, {n: n for n in _AFTER_STOCK})
    graph.add_conditional_edges("retrieve_context", route_after_retrieve,
                                {"generate_answer": "generate_answer", "answer_no_information": "answer_no_information"})
    graph.add_edge("generate_answer", "compose_answer")
    graph.add_edge("answer_no_information", "compose_answer")
    graph.add_edge("reject_question", END)
    graph.add_edge("compose_answer", END)
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
