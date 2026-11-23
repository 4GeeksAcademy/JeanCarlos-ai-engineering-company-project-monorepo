"""Dedicated LangGraph `rfp_intake` graph (separate from the CX / knowledge agent graph).

    convert_document → screen_phi → classify ─ not an RFP → discard → END
                                             └ RFP → extract_metadata → orchestrate
                                                    ─ Send(worker) × departments (parallel) → synthesize → END

Workers run in parallel through LangGraph `Send`: each one receives only the shared metadata
and its own excerpts (never the whole document). Every node persists its result in PostgreSQL
before the next one starts.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import agents, document, phi, store
from .departments import DEPARTMENTS
from .models import utcnow


class IntakeState(TypedDict, total=False):
    ticket_id: str
    pdf_path: str
    markdown: str  # PHI-redacted Markdown: the only form of the document the agents see
    pages: int | None
    phi: dict[str, Any]
    classification: dict[str, Any]
    metadata: dict[str, Any]
    metrics: dict[str, Any]
    plan: dict[str, Any]
    sections: Annotated[list[dict], operator.add]  # one per worker (parallel fan-in)
    synthesis: dict[str, Any]
    status: str


class WorkerInput(TypedDict):
    ticket_id: str
    department_id: str
    task: str
    metadata: dict[str, Any]
    excerpts: list[dict]


def _engine(config: RunnableConfig):
    return config["configurable"]["engine"]


def convert_document(state: IntakeState, config: RunnableConfig) -> IntakeState:
    store.update_ticket(_engine(config), state["ticket_id"], stage="converting")
    return {"markdown": document.pdf_to_markdown(state["pdf_path"]), "pages": document.pdf_pages(state["pdf_path"])}


def screen_phi(state: IntakeState, config: RunnableConfig) -> IntakeState:
    """Before any agent: redact patient information; only the redacted text continues and is stored."""
    store.update_ticket(_engine(config), state["ticket_id"], stage="screening_phi")
    result = phi.screen(state["markdown"])
    store.update_ticket(_engine(config), state["ticket_id"], phi_detected=result.detected,
                        phi_categories=result.categories, compliance_review_required=result.detected,
                        document_markdown=result.redacted_markdown)
    return {"markdown": result.redacted_markdown,
            "phi": {"detected": result.detected, "categories": result.categories,
                    "redacted_paragraphs": result.redacted_paragraphs}}


def classify(state: IntakeState, config: RunnableConfig) -> IntakeState:
    store.update_ticket(_engine(config), state["ticket_id"], stage="classifying")
    verdict = agents.classify(state["markdown"])
    store.update_ticket(_engine(config), state["ticket_id"], classification=verdict)
    return {"classification": verdict}


def route_after_classify(state: IntakeState) -> str:
    return "discard" if state["classification"]["decision"] == "discard" else "extract_metadata"


def discard(state: IntakeState, config: RunnableConfig) -> IntakeState:
    store.update_ticket(_engine(config), state["ticket_id"], status="descartado", stage="discarded",
                        completed_at=utcnow(), ready_for_generation=False)
    return {"status": "descartado"}


def extract_metadata(state: IntakeState, config: RunnableConfig) -> IntakeState:
    engine = _engine(config)
    store.update_ticket(engine, state["ticket_id"], stage="extracting")
    metadata = agents.extract_metadata(state["markdown"])
    metrics = document.compute_metrics(state["markdown"], state.get("pages")).as_dict()
    return {"metadata": metadata, "metrics": metrics}


def orchestrate(state: IntakeState, config: RunnableConfig) -> IntakeState:
    engine = _engine(config)
    store.update_ticket(engine, state["ticket_id"], stage="orchestrating")
    plan = agents.orchestrate(document.paragraphs(state["markdown"]), state["metadata"])
    metadata = {**state["metadata"], "departments_needed": [w["department_id"] for w in plan["workstreams"]]}
    store.save_metadata(engine, state["ticket_id"], metadata, state["metrics"])
    store.update_ticket(engine, state["ticket_id"], stage="workers")
    return {"plan": plan, "metadata": metadata}


def fan_out(state: IntakeState) -> list[Send]:
    paras = {p.id: p for p in document.paragraphs(state["markdown"])}
    return [Send("department_worker", WorkerInput(
        ticket_id=state["ticket_id"], department_id=w["department_id"], task=w["task"], metadata=state["metadata"],
        excerpts=[{"id": i, "text": paras[i].text} for i in w["paragraph_ids"] if i in paras]))
        for w in state["plan"]["workstreams"]]


def department_worker(task: WorkerInput, config: RunnableConfig) -> IntakeState:
    """One department in isolation. A failed model call yields a deterministic section marked
    `fallback`, so one worker never blocks the others."""
    excerpts = [document.Paragraph(e["id"], e["text"]) for e in task["excerpts"]]
    try:
        section = agents.run_worker(task["department_id"], task["metadata"], excerpts, task["task"])
    except agents.AgentError as error:
        section = agents.fallback_worker(task["department_id"], task["metadata"], excerpts, str(error)[:80])
    store.save_section(_engine(config), task["ticket_id"], section)
    return {"sections": [section]}


def synthesize(state: IntakeState, config: RunnableConfig) -> IntakeState:
    engine = _engine(config)
    store.update_ticket(engine, state["ticket_id"], stage="synthesizing")
    order = {d: i for i, d in enumerate(DEPARTMENTS)}
    sections = sorted(state["sections"], key=lambda s: order[s["department_id"]])
    synthesis = agents.synthesize(state["metadata"], sections, state["plan"]["unmapped"], state["phi"])
    synthesis["orchestrator"] = state["plan"]["orchestrator"]
    from .handoff import build_handoff

    handoff = build_handoff(state["ticket_id"], state, synthesis)
    store.update_ticket(engine, state["ticket_id"], status="analisis_completo", stage="done", synthesis=synthesis,
                        handoff=handoff, ready_for_generation=handoff["route"] == "proposal_generation",
                        completed_at=utcnow(), error=None)
    return {"synthesis": synthesis, "status": "analisis_completo"}


NODES = {"convert_document": convert_document, "screen_phi": screen_phi, "classify": classify, "discard": discard,
         "extract_metadata": extract_metadata, "orchestrate": orchestrate, "department_worker": department_worker,
         "synthesize": synthesize}


def build_graph():
    graph = StateGraph(IntakeState)
    for name, node in NODES.items():
        graph.add_node(name, node)
    graph.add_edge(START, "convert_document")
    graph.add_edge("convert_document", "screen_phi")
    graph.add_edge("screen_phi", "classify")
    graph.add_conditional_edges("classify", route_after_classify, {"discard": "discard", "extract_metadata": "extract_metadata"})
    graph.add_edge("discard", END)
    graph.add_edge("extract_metadata", "orchestrate")
    graph.add_conditional_edges("orchestrate", fan_out, ["department_worker"])
    graph.add_edge("department_worker", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()
