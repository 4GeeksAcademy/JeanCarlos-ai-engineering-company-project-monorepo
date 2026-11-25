"""Part 3 — approval, arbitration and closing (LangGraph, checkpointed).

Two graphs, one checkpointed thread each (see checkpoint.py for the thread ids):

Department branch — thread rfp-{ticket_id}:{department} (one per active department):

    START ─┬─ (directive) → revise ─┐
           └──────────────────────→ prepare_packet → await_approval ⏸ → record_decision ─┬ approve → await_closure ⏸ ─┬ close → END
                                        ↑           (interrupt: named approver)          ├ request_changes → revise     └ reopen (arbiter) → revise
                                        └───────────── revise ←──────────────────────────┘ reject → END

  `await_approval` is the human-in-the-loop point: it calls `interrupt()` with the approval
  packet; the checkpointer has saved the branch before it, and `Command(resume=decision)` resumes
  exactly there. Each department has its OWN thread, so a pause only stops that department: the
  others are approved, revised or rejected in parallel. `revise` re-runs the Part 2
  generator → evaluators loop of that one section with the human (or arbiter) feedback.

Ticket coordinator — thread rfp-{ticket_id}:

    open_approval → arbitrate ─┬ blocking trigger (rounds left) → dispatch (forced request_changes)
                               ├ trigger needing its fixed arbiter → arbiter_decision ⏸ ─→ dispatch / arbitrate / close_branches
                               ├ no trigger, not all approved → dispatch → await_departments ⏸ ─┬ all approved → arbitrate
                               │                                                                 └ a rejection → close_branches
                               └ no trigger, all approved → synthesize → close_branches → close → END

  `arbitrate` is the arbitration node: it fires on the BRIEFING §7 triggers found in structured
  state (approval.detect_triggers) and resolves with the fixed arbiter — a deterministic rule
  (phi-detected, baa-dpa-mismatch) or the named human (capacity-vs-population: Tom Callahan; a
  blocking trigger whose rounds are used up: Claire Whitfield). `round` counts the cycles in
  which it sent sections back; at MAX_ARBITRATION_ROUNDS no further cycle is started.
  `synthesize` runs only when every required department approved and no trigger is open.

Every node is wrapped by tracing.traced: agent, input, output and timestamps go to the state
(`trace`) and to the rfp_trace table.
"""

from __future__ import annotations

import contextvars
import logging
import operator
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import agents, approval, checkpoint, final_document, response_graph, store
from .handoff import RESPONSE_CONTRACT
from .models import utcnow
from .tracing import traced

logger = logging.getLogger("api.rfp_intake")


class NotReadyForApproval(RuntimeError):
    """The ticket is not in the Part 2 queue (or its handoff is not rfp_response.handoff/v1)."""


def _engine(config: RunnableConfig):
    return config["configurable"]["engine"]


def _now() -> str:
    return utcnow().isoformat()


# ================================================================ department branch


class BranchState(TypedDict, total=False):
    ticket_id: str
    department_id: str
    directive: dict | None  # a revision to make before the next approval: {source, by, trigger, feedback}
    packet: dict  # what the approver sees (approval.build_packet)
    decision: dict | None  # the validated human decision
    error: str | None  # why the last answer was not accepted (the branch asks again)
    outcome: str
    trace: Annotated[list, operator.add]


def start_branch(state: BranchState) -> str:
    return "revise" if state.get("directive") else "prepare_packet"


def revise(state: BranchState, config: RunnableConfig) -> BranchState:
    """Back to the Part 2 generator of THIS section (same ticket, same row), with the feedback."""
    engine = _engine(config)
    ticket_id, department_id, directive = state["ticket_id"], state["department_id"], state["directive"]
    ticket, sections = store.generation_input(engine, ticket_id)
    ctx = next(c for c in response_graph.build_contexts(ticket, sections, response_graph.max_iterations())
               if c["department_id"] == department_id)
    agreed = approval.agreed_population(ticket, None)
    original = (ctx["metadata"] or {}).get("covered_population")
    if agreed and original and agreed != original:
        # the arbiter's ruling replaces the RFP figure for the generator (handoff + resolved conflict) and the
        # compliance evaluator (the old figure is a HC-FIG-01 violation), so the Part 2 loop itself enforces it
        unit = (ctx["metadata"] or {}).get("population_unit") or "people"
        ruling = f"the arbiter (Tom Callahan) set the covered population to {agreed} {unit}"
        ctx["metadata"] = {**ctx["metadata"], "covered_population": agreed}
        ctx["conflicts"] = [*ctx["conflicts"], {"field": "covered_population", "reported": {"rfp": original},
                                                "metadata": agreed, "arbiter": "revenue",
                                                "resolution": f"capacity-vs-population: {ruling}; {original} no longer "
                                                              f"applies anywhere in the section"}]
        ctx["allowed_figures"] = sorted({*ctx["allowed_figures"], str(agreed)} - {str(original)})
        ctx["forbidden_figures"] = {str(original): ruling}
    current = next(s for s in sections if s.department_id == department_id)
    store.update_section(engine, ticket_id, department_id, approval_status="pending", approver=None, approved_at=None,
                         confirmed_capacity=None)
    result = response_graph.section_graph().invoke({"ctx": ctx, "iteration": 0, "feedback": directive["feedback"],
                                                    "draft": current.draft_content}, config=config)
    return {"directive": None, "decision": None, "error": None, "outcome": f"revised:{result['status']}"}


def prepare_packet(state: BranchState, config: RunnableConfig) -> BranchState:
    engine = _engine(config)
    ticket, metadata, sections = store.approval_input(engine, state["ticket_id"])
    section = next(s for s in sections if s.department_id == state["department_id"])
    packet = approval.build_packet(ticket, metadata, section, approval.detect_triggers(ticket, metadata, sections),
                                   section.revision_rounds)
    store.update_section(engine, state["ticket_id"], state["department_id"], approval_packet=packet,
                         approval_status="pending")
    return {"packet": packet, "error": None}


def await_approval(state: BranchState) -> BranchState:
    """HITL: pause this branch only, until its named approver answers."""
    packet = state["packet"]
    answer = interrupt({"kind": "department_approval", "department_id": state["department_id"],
                        "approver": packet["approver"], "allowed_actions": packet["allowed_actions"],
                        "last_error": state.get("error")})
    if isinstance(answer, dict) and answer.get("action") == "cancel" and answer.get("internal") == "coordinator":
        return {"decision": {"action": "cancel", "reason": answer.get("reason")}, "error": None}
    try:  # validated again inside the graph: nothing unvalidated reaches record_decision
        decision = approval.validate_decision(packet, answer)
    except approval.InvalidDecision as error:
        return {"decision": None, "error": str(error)}
    return {"decision": decision.model_dump(), "error": None}


def after_await(state: BranchState) -> str:
    return "record_decision" if state.get("decision") else "await_approval"


def record_decision(state: BranchState, config: RunnableConfig) -> BranchState:
    engine = _engine(config)
    ticket_id, department_id, decision = state["ticket_id"], state["department_id"], state["decision"]
    action = decision["action"]
    section = store.section_row(engine, ticket_id, department_id)
    entry = {"action": action, "by": decision.get("approver", "coordinator"), "comment": decision.get("comment"),
             "confirmed_capacity": decision.get("confirmed_capacity"), "revision_round": section.revision_rounds,
             "at": _now()}
    if action == "approve":
        store.add_decision(engine, ticket_id, department_id, entry, approval_status="approved",
                           approver=decision["approver"], approved_at=utcnow(), approval_comment=decision.get("comment"),
                           confirmed_capacity=decision.get("confirmed_capacity"))
        return {"outcome": "approved"}
    if action == "request_changes":
        if section.revision_rounds >= approval.max_revision_rounds():  # the limit, enforced here too
            raise RuntimeError("revision limit reached: request_changes is not allowed")
        store.add_decision(engine, ticket_id, department_id, entry, approval_status="request_changes",
                           approval_comment=decision["comment"], revision_rounds=section.revision_rounds + 1)
        return {"outcome": "request_changes",
                "directive": {"source": "request_changes", "by": decision["approver"],
                              "feedback": f"[request_changes — {decision['approver']}] {decision['comment']}"}}
    if action == "reject":
        store.add_decision(engine, ticket_id, department_id, entry, approval_status="rejected",
                           approver=decision["approver"], approval_comment=decision["comment"])
        return {"outcome": "rejected"}
    store.add_decision(engine, ticket_id, department_id, {**entry, "comment": decision.get("reason")})
    return {"outcome": "cancelled"}


def after_record(state: BranchState) -> str:
    return {"approved": "await_closure", "request_changes": "revise"}.get(state["outcome"], END)


def await_closure(state: BranchState, config: RunnableConfig) -> BranchState:
    """Approved: the branch waits (paused) for the ticket to close — or for the arbiter to reopen it."""
    answer = interrupt({"kind": "await_closure", "department_id": state["department_id"]})
    if isinstance(answer, dict) and answer.get("action") == "reopen":
        directive = {k: answer.get(k) for k in ("source", "by", "trigger", "feedback")}
        store.add_decision(_engine(config), state["ticket_id"], state["department_id"],
                           {"action": "reopen", "by": directive["by"], "comment": directive["feedback"],
                            "trigger": directive["trigger"], "at": _now()})
        return {"outcome": "reopened", "directive": directive}
    if isinstance(answer, dict) and answer.get("action") == "close":
        return {"outcome": "closed"}
    return {"outcome": "approved"}  # anything else is ignored: the branch keeps waiting


def after_closure(state: BranchState) -> str:
    return {"reopened": "revise", "closed": END}.get(state["outcome"], "await_closure")


def _approver_agent(state: dict) -> str:
    return f"human:{approval.approver_of(state['department_id'])}"


def build_branch_graph(saver):
    builder = StateGraph(BranchState)
    nodes = {
        "revise": (lambda s: f"agent:generator:{s['department_id']}", revise),
        "prepare_packet": ("coordinator:approval_packet", prepare_packet),
        "await_approval": (_approver_agent, await_approval),
        "record_decision": ("coordinator:record_decision", record_decision),
        "await_closure": ("coordinator:await_closure", await_closure),
    }
    for name, (agent, fn) in nodes.items():
        builder.add_node(name, traced(agent, fn, node=name, in_state=True))
    builder.add_conditional_edges(START, start_branch, ["revise", "prepare_packet"])
    builder.add_edge("revise", "prepare_packet")
    builder.add_edge("prepare_packet", "await_approval")
    builder.add_conditional_edges("await_approval", after_await, ["record_decision", "await_approval"])
    builder.add_conditional_edges("record_decision", after_record, ["await_closure", "revise", END])
    builder.add_conditional_edges("await_closure", after_closure, ["revise", "await_closure", END])
    return builder.compile(checkpointer=saver)


# ================================================================ ticket coordinator


class TicketState(TypedDict, total=False):
    ticket_id: str
    departments: list[str]  # required departments (compliance always)
    round: int  # arbitration rounds used
    directives: dict[str, dict]  # department → revision ordered by the arbitration
    pending_arbitration: dict | None  # the decision asked to a named arbiter
    arbiter_error: str | None
    approvals: dict[str, str]
    dispatched: dict[str, str]
    outcome: str | None  # completed / rejected / discarded
    trace: Annotated[list, operator.add]


def open_approval(state: TicketState, config: RunnableConfig) -> TicketState:
    ticket, _metadata, _sections = store.approval_input(_engine(config), state["ticket_id"])
    handoff = ticket.response_handoff or {}
    if handoff.get("contract") != RESPONSE_CONTRACT:
        raise NotReadyForApproval("the ticket has no rfp_response.handoff/v1")
    departments = approval.required_departments([s["department_id"] for s in handoff["sections"]])
    return {"departments": departments, "round": 0, "directives": {}, "pending_arbitration": None, "outcome": None}


def _approvals(engine, ticket_id: str, departments: list[str]) -> dict[str, str]:
    _ticket, _metadata, sections = store.approval_input(engine, ticket_id)
    status = {s.department_id: s.approval_status for s in sections}
    return {d: status.get(d, "pending") for d in departments}


def arbitrate(state: TicketState, config: RunnableConfig) -> TicketState:
    engine = _engine(config)
    ticket_id, departments = state["ticket_id"], state["departments"]
    ticket, metadata, sections = store.approval_input(engine, ticket_id)
    triggers = approval.detect_triggers(ticket, metadata, [s for s in sections if s.department_id in departments])
    approvals = _approvals(engine, ticket_id, departments)
    current, limit = state.get("round", 0), approval.max_arbitration_rounds()
    base = {"directives": {}, "pending_arbitration": None, "approvals": approvals, "arbiter_error": None}
    if not triggers:
        return base
    blocking = [t for t in triggers if t["by_rule"]]  # resolved by the arbiter's fixed rule
    if blocking and current < limit:  # deterministic rule of the fixed arbiter: send the sections back
        directives: dict[str, dict] = {}
        entries = []
        for trigger in blocking:
            for department_id in trigger["departments"]:
                previous = directives.get(department_id, {}).get("feedback", "")
                directives[department_id] = {"source": "arbitration", "by": trigger["arbiter"],
                                             "trigger": trigger["trigger"],
                                             "feedback": "\n".join(filter(None, [previous, approval.feedback_for(
                                                 trigger, department_id)]))}
            entries.append({"round": current + 1, "trigger": trigger["trigger"], "arbiter": trigger["arbiter"],
                            "decided_by": "rule", "rule": trigger["rule"], "resolution": "request_changes",
                            "departments": trigger["departments"], "evidence": trigger["evidence"], "at": _now()})
        store.add_arbitration(engine, ticket_id, entries, approval_round=current + 1, stage="arbitration")
        logger.info("rfp_approval arbitration ticket_id=%s round=%s triggers=%s", ticket_id, current + 1,
                    [t["trigger"] for t in blocking])
        return {**base, "directives": directives, "round": current + 1}
    trigger = blocking[0] if blocking else triggers[0]
    if blocking:  # rounds used up: the named arbiter rules, without another revision cycle
        options = ["discard", "request_new_run"] if trigger["trigger"] == "phi-detected" else ["request_new_run"]
    else:  # capacity-vs-population: Tom Callahan chooses
        options = (["reduce_population", "add_sites", "request_new_run"] if current < limit
                   else ["reduce_population", "request_new_run"])
    pending = {"trigger": trigger["trigger"], "arbiter": trigger["arbiter"], "rule": trigger["rule"],
               "departments": trigger["departments"], "evidence": trigger["evidence"], "options": options,
               "round": current, "max_rounds": limit, "binding": current >= limit, "asked_at": _now()}
    store.update_ticket(engine, ticket_id, pending_arbitration=pending, stage="arbitration")
    return {**base, "pending_arbitration": pending}


def after_arbitrate(state: TicketState) -> str:
    if state.get("pending_arbitration"):
        return "arbiter_decision"
    if state.get("directives"):
        return "dispatch"
    if all(v == "approved" for v in state["approvals"].values()):
        return "synthesize"
    return "dispatch"


def arbiter_decision(state: TicketState, config: RunnableConfig) -> TicketState:
    """HITL for the named arbiter of a trigger (BRIEFING §7) — a person, never a model vote."""
    pending = state["pending_arbitration"]
    answer = interrupt({"kind": "arbitration", **pending, "last_error": state.get("arbiter_error")})
    try:
        decision = approval.validate_arbiter_decision(pending, answer)
    except approval.InvalidDecision as error:
        return {"arbiter_error": str(error)}
    engine = _engine(config)
    current = state.get("round", 0)
    evidence = pending["evidence"]
    entry = {"round": current + 1, "trigger": pending["trigger"], "arbiter": decision.arbiter, "decided_by": "human",
             "rule": pending["rule"], "resolution": decision.resolution, "comment": decision.comment,
             "departments": pending["departments"], "evidence": evidence, "at": _now()}
    directives: dict[str, dict] = {}
    unit = evidence.get("population_unit") or "people"
    if decision.resolution == "reduce_population":
        entry["agreed_population"] = decision.covered_population
        if not pending.get("binding"):
            original = str(evidence["covered_population"])
            feedback = (f"[arbitration capacity-vs-population — {decision.arbiter}] Clinical capacity is "
                        f"{evidence['confirmed_capacity']} {unit}. The contract now covers {decision.covered_population} "
                        f"{unit}: replace every mention of {original} {unit} with {decision.covered_population} {unit}.")
            _ticket, _metadata, sections = store.approval_input(engine, state["ticket_id"])
            quoting = {s.department_id for s in sections if original in agents.numbers_in(s.draft_content or "")}
            for department_id in sorted({"revenue"} | quoting & set(state["departments"])):  # Revenue + who quotes it
                directives[department_id] = {"source": "arbitration", "by": decision.arbiter,
                                             "trigger": pending["trigger"], "feedback": feedback}
    elif decision.resolution == "add_sites":
        directives["clinical"] = {"source": "arbitration", "by": decision.arbiter, "trigger": pending["trigger"],
                                  "feedback": f"[arbitration capacity-vs-population — {decision.arbiter}] The confirmed "
                                              f"capacity ({evidence['confirmed_capacity']}) does not cover "
                                              f"{evidence['covered_population']} {unit}. Add HealthCore sites in the "
                                              f"client's country so the plan covers the full population."}
    outcome = {"discard": "discarded", "request_new_run": "rejected"}.get(decision.resolution)
    new_round = current + 1 if directives else current
    store.add_arbitration(engine, state["ticket_id"], [entry], pending_arbitration=None, approval_round=new_round,
                          stage="arbitration" if directives else "awaiting_approval")
    return {"pending_arbitration": None, "arbiter_error": None, "directives": directives, "round": new_round,
            "outcome": outcome}


def after_arbiter(state: TicketState) -> str:
    if state.get("arbiter_error"):
        return "arbiter_decision"
    if state.get("outcome"):
        return "close_branches"
    return "dispatch" if state.get("directives") else "arbitrate"


# ---------------------------------------------------------------- running the department branches


def _graphs(engine) -> tuple[Any, Any]:
    saver = checkpoint.checkpointer(engine)
    key = id(saver)
    if key not in _compiled:
        _compiled[key] = (build_branch_graph(saver), build_ticket_graph(saver))
    return _compiled[key]


_compiled: dict[int, tuple[Any, Any]] = {}


def branch_graph(engine):
    return _graphs(engine)[0]


def ticket_graph(engine):
    return _graphs(engine)[1]


def branch_config(engine, ticket_id: str, department_id: str) -> dict:
    return {"configurable": {"thread_id": checkpoint.department_thread(ticket_id, department_id), "engine": engine}}


def ticket_config(engine, ticket_id: str) -> dict:
    return {"configurable": {"thread_id": checkpoint.ticket_thread(ticket_id), "engine": engine}}


def paused_at(snapshot) -> str:
    if not snapshot.values:
        return "not_started"
    return snapshot.next[0] if snapshot.next else "ended"


def run_branch(engine, ticket_id: str, department_id: str, payload) -> str:
    """Start or resume ONE department thread (under its own lock); returns where it paused."""
    config = branch_config(engine, ticket_id, department_id)
    graph = branch_graph(engine)
    with checkpoint.thread_lock(engine, config["configurable"]["thread_id"]):
        graph.invoke(payload, config=config)
        return paused_at(graph.get_state(config))


def run_branches(engine, ticket_id: str, jobs: dict[str, Any]) -> dict[str, str]:
    """Several department threads in parallel. Each job runs in a clean context, so a branch is
    never mistaken for a subgraph of the coordinator: it has its own checkpoint thread."""
    if not jobs:
        return {}
    with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="rfp-branch") as pool:
        futures = {d: pool.submit(contextvars.Context().run, run_branch, engine, ticket_id, d, payload)
                   for d, payload in jobs.items()}
        return {d: future.result() for d, future in futures.items()}


def dispatch(state: TicketState, config: RunnableConfig) -> TicketState:
    engine = _engine(config)
    ticket_id, directives = state["ticket_id"], state.get("directives") or {}
    jobs: dict[str, Any] = {}
    for department_id in state["departments"]:
        where = paused_at(branch_graph(engine).get_state(branch_config(engine, ticket_id, department_id)))
        if where == "not_started":
            jobs[department_id] = {"ticket_id": ticket_id, "department_id": department_id,
                                   "directive": directives.get(department_id)}
        elif department_id in directives and where == "await_closure":
            jobs[department_id] = Command(resume={"action": "reopen", **directives[department_id]})
    dispatched = run_branches(engine, ticket_id, jobs)
    store.update_ticket(engine, ticket_id, stage="awaiting_approval")
    return {"directives": {}, "dispatched": dispatched}


def await_departments(state: TicketState, config: RunnableConfig) -> TicketState:
    """Pause the coordinator (only the coordinator) until the branches have decided."""
    engine = _engine(config)
    approvals = _approvals(engine, state["ticket_id"], state["departments"])
    if not _decided(approvals):
        interrupt({"kind": "await_departments", "pending": [d for d, v in approvals.items() if v != "approved"]})
        approvals = _approvals(engine, state["ticket_id"], state["departments"])
    rejected = any(v == "rejected" for v in approvals.values())
    return {"approvals": approvals, "outcome": "rejected" if rejected else None}


def _decided(approvals: dict[str, str]) -> bool:
    return any(v == "rejected" for v in approvals.values()) or all(v == "approved" for v in approvals.values())


def after_departments(state: TicketState) -> str:
    if state.get("outcome") == "rejected":
        return "close_branches"
    return "arbitrate" if all(v == "approved" for v in state["approvals"].values()) else "await_departments"


def synthesize(state: TicketState, config: RunnableConfig) -> TicketState:
    engine = _engine(config)
    store.update_ticket(engine, state["ticket_id"], stage="finalizing")
    ticket, metadata, sections = store.approval_input(engine, state["ticket_id"])
    document = final_document.build(ticket, metadata, sections, state["departments"], ticket.arbitration or [],
                                    utcnow())
    store.save_final_document(engine, document)
    return {"outcome": "completed"}


def close_branches(state: TicketState, config: RunnableConfig) -> TicketState:
    engine = _engine(config)
    jobs: dict[str, Any] = {}
    for department_id in state["departments"]:
        where = paused_at(branch_graph(engine).get_state(branch_config(engine, state["ticket_id"], department_id)))
        if where == "await_closure":
            jobs[department_id] = Command(resume={"action": "close"})
        elif where == "await_approval":
            jobs[department_id] = Command(resume={"action": "cancel", "internal": "coordinator",
                                                  "reason": f"ticket {state['outcome']}"})
    return {"dispatched": run_branches(engine, state["ticket_id"], jobs)}


CLOSING = {"completed": ("terminado", "completed"), "rejected": ("needs_human_review", "approval_rejected"),
           "discarded": ("descartado", "discarded_by_compliance")}


def close(state: TicketState, config: RunnableConfig) -> TicketState:
    status, stage = CLOSING[state["outcome"]]
    store.update_ticket(_engine(config), state["ticket_id"], status=status, stage=stage, pending_arbitration=None,
                        completed_at=utcnow() if state["outcome"] == "completed" else None)
    logger.info("rfp_approval closed ticket_id=%s outcome=%s", state["ticket_id"], state["outcome"])
    return {"outcome": state["outcome"]}


def _arbiter_agent(state: dict) -> str:
    return f"human:{(state.get('pending_arbitration') or {}).get('arbiter', 'arbiter')}"


def build_ticket_graph(saver):
    builder = StateGraph(TicketState)
    nodes = {
        "open_approval": ("coordinator", open_approval),
        "arbitrate": ("arbiter:rules (BRIEFING §7)", arbitrate),
        "arbiter_decision": (_arbiter_agent, arbiter_decision),
        "dispatch": ("coordinator:dispatch", dispatch),
        "await_departments": ("coordinator:await_departments", await_departments),
        "synthesize": ("agent:final_document_synthesizer", synthesize),
        "close_branches": ("coordinator:dispatch", close_branches),
        "close": ("coordinator", close),
    }
    for name, (agent, fn) in nodes.items():
        builder.add_node(name, traced(agent, fn, node=name, in_state=True))
    builder.add_edge(START, "open_approval")
    builder.add_edge("open_approval", "arbitrate")
    builder.add_conditional_edges("arbitrate", after_arbitrate, ["arbiter_decision", "dispatch", "synthesize"])
    builder.add_conditional_edges("arbiter_decision", after_arbiter,
                                  ["arbiter_decision", "close_branches", "dispatch", "arbitrate"])
    builder.add_edge("dispatch", "await_departments")
    builder.add_conditional_edges("await_departments", after_departments,
                                  ["close_branches", "arbitrate", "await_departments"])
    builder.add_edge("synthesize", "close_branches")
    builder.add_edge("close_branches", "close")
    builder.add_edge("close", END)
    return builder.compile(checkpointer=saver)
