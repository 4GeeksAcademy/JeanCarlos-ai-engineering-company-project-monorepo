"""Part 2 — dedicated LangGraph `rfp_response` graph: one section per department, each with its
own generator → evaluators loop. Starts from the Part 1 routing handoff, never from the PDF.

Main graph (one run per ticket):

    prepare ─ Send(section) × workstreams (parallel, independent) → finalize → END

Section subgraph (one per department, run inside the `section` node):

    generate ─┬→ evaluate_readability ─┐
              ├→ evaluate_relevance  ──┼→ judge ─ overall_pass ─────────────→ finish
              └→ evaluate_compliance ─┘        ├ fail, iteration < limit → generate (with feedback)
                                               └ fail, limit reached ────→ finish (needs_human_review)

Each evaluator writes ONLY its own key of the section state (readability / relevance /
compliance), so parallel evaluators never write the same field; the judge is the single writer of
the EvaluationResult. Sections write only their own row and return one entry to the main graph
through an append-only reducer, so a slow or failing department never blocks the others.
"""

from __future__ import annotations

import logging
import operator
import os
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import agents, evaluators, guidelines, phi, store
from .departments import DEPARTMENTS
from .document import paragraphs
from .generators import GENERATORS
from .handoff import CONTRACT as INTAKE_CONTRACT
from .handoff import build_response_handoff

logger = logging.getLogger("api.rfp_intake")


def max_iterations() -> int:
    try:
        return max(1, int(os.environ.get("RFP_MAX_ITERATIONS", guidelines.MAX_ITERATIONS)))
    except ValueError:
        return guidelines.MAX_ITERATIONS


class NotReadyForGeneration(RuntimeError):
    """The ticket is not in the Part 1 queue (or its handoff does not allow generation)."""


def _engine(config: RunnableConfig):
    return config["configurable"]["engine"]


# ================================================================ section subgraph


class SectionState(TypedDict, total=False):
    ctx: dict[str, Any]  # generator input built from the Part 1 handoff
    iteration: int
    draft: str | None  # PHI-redacted
    draft_phi: list[str]  # PHI categories found (and redacted) in the raw model output
    feedback: str | None
    generator_error: str | None
    readability: dict  # written only by evaluate_readability
    relevance: dict  # written only by evaluate_relevance
    compliance: dict  # written only by evaluate_compliance
    evaluation: dict  # written only by judge
    status: str


def generate(state: SectionState, config: RunnableConfig) -> SectionState:
    ctx = state["ctx"]
    ticket_id, department_id = ctx["ticket_id"], ctx["department_id"]
    iteration = state.get("iteration", 0) + 1
    engine = _engine(config)
    store.update_section(engine, ticket_id, department_id, generation_status="generating" if iteration == 1 else "revising",
                         iterations=iteration)
    try:
        output = GENERATORS[department_id].generate(ctx, state.get("feedback"), state.get("draft"))
    except agents.AgentError as error:
        logger.warning("rfp_response generator failed ticket_id=%s department=%s iteration=%s", ticket_id, department_id,
                       iteration)
        return {"iteration": iteration, "generator_error": str(error)[:200]}
    try:  # the raw model output is never stored nor passed on: only its redacted form
        screening = phi.screen(output["content"])
        draft, categories = screening.redacted_markdown, screening.categories
    except RuntimeError:
        draft, categories = phi.REDACTION, ["unredactable"]
    store.update_section(engine, ticket_id, department_id, draft_content=draft, generation_status="evaluating")
    store.advance_status(engine, ticket_id, "generando_borrador", "en_evaluación", stage="evaluating")
    return {"iteration": iteration, "draft": draft, "draft_phi": categories, "generator_error": None}


def after_generate(state: SectionState) -> list[str] | str:
    if state.get("generator_error"):
        return "finish"
    return ["evaluate_readability", "evaluate_relevance", "evaluate_compliance"]


def evaluate_readability(state: SectionState) -> SectionState:
    return {"readability": evaluators.evaluate_readability(state["draft"]).model_dump(by_alias=True)}


def evaluate_relevance(state: SectionState) -> SectionState:
    ctx = state["ctx"]
    result = evaluators.evaluate_relevance(DEPARTMENTS[ctx["department_id"]].name, state["draft"],
                                           ctx["workstream"]["key_aspects"])
    return {"relevance": result.model_dump(by_alias=True)}


def evaluate_compliance(state: SectionState) -> SectionState:
    ctx = state["ctx"]
    result = evaluators.evaluate_compliance(ctx["department_id"], state["draft"], country=ctx["metadata"].get("client_country"),
                                            allowed=set(ctx["allowed_figures"]), phi_categories=state.get("draft_phi"))
    return {"compliance": result.model_dump(by_alias=True)}


def judge(state: SectionState, config: RunnableConfig) -> SectionState:
    ctx = state["ctx"]
    result = evaluators.judge(ctx["ticket_id"], ctx["department_id"], state["iteration"],
                              evaluators.ReadabilityEval.model_validate(state["readability"]),
                              evaluators.RelevanceEval.model_validate(state["relevance"]),
                              evaluators.ComplianceEval.model_validate(state["compliance"])).as_dict()
    store.update_section(_engine(config), ctx["ticket_id"], ctx["department_id"], evaluation_results=result,
                         history_entry={"iteration": state["iteration"], "draft": state["draft"], "evaluation": result})
    logger.info("rfp_response evaluated ticket_id=%s department=%s iteration=%s pass=%s", ctx["ticket_id"],
                ctx["department_id"], state["iteration"], result["overall_pass"])
    return {"evaluation": result, "feedback": result["feedback_for_generator"] or None}


def after_judge(state: SectionState) -> str:
    if state["evaluation"]["overall_pass"] or state["iteration"] >= state["ctx"]["max_iterations"]:
        return "finish"
    return "generate"


def finish(state: SectionState, config: RunnableConfig) -> SectionState:
    """passed, or needs_human_review keeping the last draft + its EvaluationResult (never dropped)."""
    ctx = state["ctx"]
    evaluation = state.get("evaluation")
    status = "passed" if evaluation and evaluation["overall_pass"] else "needs_human_review"
    values: dict[str, Any] = {"generation_status": status}
    if state.get("generator_error") and not evaluation:
        values["evaluation_results"] = {"section_id": f"{ctx['ticket_id']}:{ctx['department_id']}",
                                        "department_id": ctx["department_id"], "overall_pass": False, "contains_phi": False,
                                        "generator_error": state["generator_error"],
                                        "feedback_for_generator": "The generator was unavailable: a person must draft it."}
    store.update_section(_engine(config), ctx["ticket_id"], ctx["department_id"], **values)
    return {"status": status}


def build_section_graph():
    builder = StateGraph(SectionState)
    builder.add_node("generate", generate)
    builder.add_node("evaluate_readability", evaluate_readability)
    builder.add_node("evaluate_relevance", evaluate_relevance)
    builder.add_node("evaluate_compliance", evaluate_compliance)
    builder.add_node("judge", judge)
    builder.add_node("finish", finish)
    builder.add_edge(START, "generate")
    builder.add_conditional_edges("generate", after_generate,
                                  ["evaluate_readability", "evaluate_relevance", "evaluate_compliance", "finish"])
    builder.add_edge(["evaluate_readability", "evaluate_relevance", "evaluate_compliance"], "judge")
    builder.add_conditional_edges("judge", after_judge, ["generate", "finish"])
    builder.add_edge("finish", END)
    return builder.compile()


# ================================================================ main graph


class ResponseState(TypedDict, total=False):
    ticket_id: str
    contexts: list[dict]
    sections: Annotated[list[dict], operator.add]  # one entry per department (parallel fan-in)
    status: str


def build_contexts(ticket, sections, limit: int) -> list[dict]:
    """Generator input from the Part 1 handoff (+ the synthesizer summary and the excerpts the
    orchestrator assigned to each department, from the already-redacted Markdown)."""
    handoff = ticket.handoff or {}
    if handoff.get("contract") != INTAKE_CONTRACT or handoff.get("route") != "proposal_generation":
        raise NotReadyForGeneration("the Part 1 handoff does not route this ticket to proposal generation")
    paras = {p.id: p.text for p in paragraphs(ticket.document_markdown or "")}
    rows = {s.department_id: s for s in sections}
    summary = (ticket.synthesis or {}).get("summary", "")
    contexts = []
    for workstream in handoff["workstreams"]:
        department_id = workstream["department_id"]
        row = rows.get(department_id)
        excerpts = [{"id": i, "text": paras[i]} for i in (row.excerpt_ids if row else []) if i in paras]
        conflicts = [c for c in handoff.get("conflicts", [])
                     if department_id in (c.get("reported") or {}) or c.get("arbiter") == department_id]
        ctx = {"ticket_id": ticket.ticket_id, "department_id": department_id, "metadata": handoff["metadata"],
               "currency": handoff.get("currency"), "required_clauses": handoff.get("required_clauses", []),
               "summary": summary, "workstream": workstream, "conflicts": conflicts, "excerpts": excerpts,
               "max_iterations": limit}
        generator_context = GENERATORS[department_id].company_context(ctx)
        ctx["allowed_figures"] = sorted(evaluators.allowed_figures(handoff["metadata"], workstream, summary, conflicts,
                                                                   [e["text"] for e in excerpts], generator_context,
                                                                   handoff.get("required_clauses")))
        contexts.append(ctx)
    return contexts


def prepare(state: ResponseState, config: RunnableConfig) -> ResponseState:
    ticket, sections = store.generation_input(_engine(config), state["ticket_id"])
    return {"contexts": build_contexts(ticket, sections, max_iterations())}


def fan_out(state: ResponseState) -> list[Send]:
    return [Send("section", ctx) for ctx in state["contexts"]]


_section_graph = None


def section(ctx: dict, config: RunnableConfig) -> ResponseState:
    global _section_graph
    if _section_graph is None:
        _section_graph = build_section_graph()
    result = _section_graph.invoke({"ctx": ctx, "iteration": 0}, config=config)
    return {"sections": [{"department_id": ctx["department_id"], "status": result["status"],
                          "iterations": result.get("iteration", 0)}]}


def finalize(state: ResponseState, config: RunnableConfig) -> ResponseState:
    engine = _engine(config)
    ticket, sections = store.generation_input(engine, state["ticket_id"])
    review = [s.department_id for s in sections if s.generation_status == "needs_human_review"]
    status = "needs_human_review" if review else "en_evaluación"
    handoff = build_response_handoff(ticket, sections, max_iterations())
    store.update_ticket(engine, ticket.ticket_id, status=status, stage="generated", response_handoff=handoff,
                        ready_for_approval=True)
    return {"status": status}


def build_graph():
    builder = StateGraph(ResponseState)
    builder.add_node("prepare", prepare)
    builder.add_node("section", section)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "prepare")
    builder.add_conditional_edges("prepare", fan_out, ["section"])
    builder.add_edge("section", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile()
