"""RFP approval and closing (Hito 9 Parte 3) with a scripted model and a SQLite checkpointer:
interrupt + resume per department, branches that do not block each other, thread ids namespaced
by ticket and department, the revision and arbitration limits, the BRIEFING §7 triggers resolved
by their fixed arbiter, the final document only after every approval, and the trace.

    python -m pytest tests/pipelines/test_rfp_approval.py
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session

from data.pipelines.rfp_intake import (approval, approval_graph, approval_runner, checkpoint, final_document,
                                       response_runner, runner, store)
from data.pipelines.rfp_intake.models import RfpTicket
from tests.pipelines.rfp_fixtures import FORMAL, INFORMAL, draft, engine, model  # noqa: F401

APPROVERS = {"revenue": "Tom Callahan", "clinical": "Dr. Marcus Reid", "compliance": "Claire Whitfield"}


def approve(department_id, capacity=900, comment=None):
    decision = {"action": "approve", "approver": APPROVERS[department_id]}
    if department_id == "clinical":
        decision["confirmed_capacity"] = capacity
    if comment:
        decision["comment"] = comment
    return decision


def decide(action, department_id, comment="Please make the payment terms clearer for the buyer."):
    return {"action": action, "approver": APPROVERS[department_id], "comment": comment}


def to_part3(engine, path=FORMAL):  # noqa: F811
    """Part 1 → Part 2 → open Part 3 (each department paused on its approval)."""
    ticket = store.receive(engine, path.read_bytes(), created_by="test")
    assert runner.run_intake(engine, ticket.ticket_id) == "analisis_completo"
    assert response_runner.run_generation(engine, ticket.ticket_id) in ("en_evaluación", "needs_human_review")
    approval_runner.open_approval(engine, ticket.ticket_id)
    return ticket.ticket_id


def paused(engine, ticket_id):  # noqa: F811
    return {name: t["paused_at"] for name, t in approval_runner.status(engine, ticket_id)["threads"].items()}


def ticket_of(engine, ticket_id):  # noqa: F811
    with Session(engine) as session:
        return session.get(RfpTicket, ticket_id)


def trace_of(engine, ticket_id, thread_suffix=None):  # noqa: F811
    rows = store.trace(engine, ticket_id)
    if thread_suffix is None:
        return rows
    return [r for r in rows if r["thread_id"] == f"rfp-{ticket_id}{thread_suffix}"]


def generator_calls(model, department_id):  # noqa: F811
    return [c for c in model.calls if c[0] == f"generator:{department_id}"]


# ================================================================ interrupt, checkpoint, resume


def test_each_department_pauses_on_its_own_approval_with_a_persisted_checkpoint(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    assert paused(engine, ticket_id) == {"ticket": "await_departments", "revenue": "await_approval",
                                         "clinical": "await_approval", "compliance": "await_approval"}
    ticket = ticket_of(engine, ticket_id)
    assert (ticket.status, ticket.stage, ticket.ready_for_approval) == ("esperando_aprobación", "awaiting_approval", False)
    saver = checkpoint.checkpointer(engine)
    for department_id in APPROVERS:  # the checkpoint exists BEFORE anyone answers
        saved = saver.get_tuple({"configurable": {"thread_id": f"rfp-{ticket_id}:{department_id}", "checkpoint_ns": ""}})
        assert saved is not None and saved.pending_writes is not None
    packet = store.section_row(engine, ticket_id, "compliance").approval_packet
    assert packet["approver"] == "Claire Whitfield" and packet["client"]["instrument"] == "BAA (HIPAA)"
    assert packet["evaluation"]["overall_pass"] is True and packet["allowed_actions"] == list(approval.ACTIONS)
    assert store.section_row(engine, ticket_id, "clinical").approval_packet["requires_capacity"] is True


def test_resume_survives_a_restart_and_continues_from_the_interrupt(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    calls_before = len(model.calls)
    nodes_before = [r["node"] for r in trace_of(engine, ticket_id, ":revenue")]
    # "restart the API": forget every compiled graph and checkpointer object — only the file remains
    approval_graph._compiled.clear()
    checkpoint._sqlite_saver.cache_clear()
    result = approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    assert result["paused_at"] == "await_closure"
    nodes_after = [r["node"] for r in trace_of(engine, ticket_id, ":revenue")][len(nodes_before):]
    # resumed AT await_approval: nothing before it ran again (no new packet, no model call)
    assert nodes_after == ["await_approval", "record_decision", "await_closure"]
    assert len(model.calls) == calls_before
    row = store.section_row(engine, ticket_id, "revenue")
    assert (row.approval_status, row.approver) == ("approved", "Tom Callahan") and row.approved_at is not None


def test_approving_b_while_a_is_still_interrupted(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    approval_runner.submit_decision(engine, ticket_id, "clinical", approve("clinical"))
    assert paused(engine, ticket_id) == {"ticket": "await_departments", "revenue": "await_approval",
                                         "clinical": "await_closure", "compliance": "await_approval"}
    revenue = trace_of(engine, ticket_id, ":revenue")
    clinical = trace_of(engine, ticket_id, ":clinical")
    # A (revenue) is interrupted and has not moved; B (clinical) went on after A paused
    assert (revenue[-1]["node"], revenue[-1]["status"]) == ("await_approval", "interrupted")
    assert [r["node"] for r in clinical[-3:]] == ["await_approval", "record_decision", "await_closure"]
    assert clinical[-2]["started_at"] > revenue[-1]["finished_at"]
    assert store.final_document(engine, ticket_id) is None


def test_two_departments_decide_at_the_same_time(engine, model):  # noqa: F811
    """Different threads, different locks: two simultaneous approvals both land."""
    ticket_id = to_part3(engine)
    barrier = threading.Barrier(2)

    def submit(department_id):
        barrier.wait()
        return approval_runner.submit_decision(engine, ticket_id, department_id, approve(department_id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["revenue", "clinical"]))
    assert {r["paused_at"] for r in results} == {"await_closure"}
    assert paused(engine, ticket_id)["compliance"] == "await_approval"
    assert ticket_of(engine, ticket_id).status == "esperando_aprobación"


def test_thread_ids_are_namespaced_by_ticket_and_department(engine, model):  # noqa: F811
    first, second = to_part3(engine), to_part3(engine, INFORMAL)
    threads = {t["thread_id"] for tid in (first, second) for t in approval_runner.status(engine, tid)["threads"].values()}
    assert threads == {f"rfp-{tid}{suffix}" for tid in (first, second) for suffix in ("", ":revenue", ":clinical",
                                                                                      ":compliance")}
    approval_runner.submit_decision(engine, first, "revenue", approve("revenue"))
    assert paused(engine, second)["revenue"] == "await_approval"  # the other ticket did not move
    assert store.section_row(engine, second, "revenue").approval_status == "pending"


def test_final_document_is_generated_only_after_every_approval(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    for department_id in ("revenue", "clinical"):
        approval_runner.submit_decision(engine, ticket_id, department_id, approve(department_id))
        assert store.final_document(engine, ticket_id) is None
        assert ticket_of(engine, ticket_id).status == "esperando_aprobación"
    result = approval_runner.submit_decision(engine, ticket_id, "compliance", approve("compliance"))
    assert (result["status"], result["stage"]) == ("terminado", "completed")
    assert set(paused(engine, ticket_id).values()) == {"ended"}
    document = store.final_document(engine, ticket_id)
    assert document["currency"] == "USD" and [s["department_id"] for s in document["sections"]] == list(APPROVERS)
    assert [a["approver"] for a in document["approvals"]] == list(APPROVERS.values())
    markdown = document["markdown"]
    assert markdown.startswith("# Economic Proposal — HealthCore for Meridian Manufacturing, Inc.")
    assert "HIPAA — Business Associate Agreement (BAA)" in markdown and "800 employees" in markdown
    assert "_Approved by Claire Whitfield (Chief Compliance Officer)" in markdown
    assert ticket_of(engine, ticket_id).completed_at is not None


# ================================================================ human answers are validated


@pytest.mark.parametrize("department_id, raw, message", [
    ("revenue", {"action": "approve", "approver": "Dr. Marcus Reid"}, "Only Tom Callahan"),
    ("revenue", {"action": "reject", "approver": "Tom Callahan"}, "comment"),
    ("revenue", {"action": "request_changes", "approver": "Tom Callahan",
                 "comment": "Patient: John Smith was diagnosed with diabetes, mention him."}, "patient information"),
    ("clinical", {"action": "approve", "approver": "Dr. Marcus Reid"}, "capacity"),
    ("revenue", {"action": "approve", "approver": "Tom Callahan", "extra": 1}, "Invalid decision"),
    ("revenue", {"action": "ship_it", "approver": "Tom Callahan"}, "Invalid decision"),
])
def test_invalid_answers_never_enter_the_graph(engine, model, department_id, raw, message):  # noqa: F811
    ticket_id = to_part3(engine)
    with pytest.raises(approval.InvalidDecision, match=message):
        approval_runner.submit_decision(engine, ticket_id, department_id, raw)
    assert paused(engine, ticket_id)[department_id] == "await_approval"
    assert store.section_row(engine, ticket_id, department_id).approval_status == "pending"


def test_a_decision_nobody_asked_for_is_refused(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    with pytest.raises(approval_runner.NotAwaitingDecision):
        approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    with pytest.raises(approval_runner.NotAwaitingDecision):
        approval_runner.submit_arbitration(engine, ticket_id, {"trigger": "capacity-vs-population",
                                                               "arbiter": "Tom Callahan", "resolution": "add_sites"})


# ================================================================ request_changes and the revision limit


def test_request_changes_goes_back_to_the_part2_generator_of_that_section(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    before = len(generator_calls(model, "revenue"))
    result = approval_runner.submit_decision(engine, ticket_id, "revenue", decide("request_changes", "revenue"))
    assert result["paused_at"] == "await_approval"  # revised and waiting for Tom again
    calls = generator_calls(model, "revenue")[before:]
    assert len(calls) == 1 and "[request_changes — Tom Callahan] Please make the payment terms clearer" in calls[0][2]
    assert "<borrador_anterior>" in calls[0][2]  # the previous draft, not a new run from scratch
    row = store.section_row(engine, ticket_id, "revenue")
    assert (row.revision_rounds, row.approval_status) == (1, "pending")
    assert row.evaluation_history[-1]["evaluation"]["overall_pass"] is True  # re-evaluated by Part 2's evaluators
    assert [n for n in [r["node"] for r in trace_of(engine, ticket_id, ":revenue")]].count("revise") == 1
    assert paused(engine, ticket_id)["clinical"] == "await_approval"  # the others never waited


def test_the_revision_limit_is_enforced(engine, model, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RFP_MAX_REVISION_ROUNDS", "2")
    ticket_id = to_part3(engine)
    for _ in range(2):
        approval_runner.submit_decision(engine, ticket_id, "revenue", decide("request_changes", "revenue"))
    packet = store.section_row(engine, ticket_id, "revenue").approval_packet
    assert packet["revision_rounds"] == 2 and "request_changes" not in packet["allowed_actions"]
    with pytest.raises(approval.InvalidDecision, match="revision limit"):
        approval_runner.submit_decision(engine, ticket_id, "revenue", decide("request_changes", "revenue"))
    with pytest.raises(RuntimeError, match="revision limit"):  # the graph refuses it too
        approval_graph.record_decision({"ticket_id": ticket_id, "department_id": "revenue",
                                        "decision": decide("request_changes", "revenue")},
                                       {"configurable": {"engine": engine}})
    approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    assert store.section_row(engine, ticket_id, "revenue").approval_status == "approved"


def test_a_rejection_closes_the_ticket_and_requires_a_new_run(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    result = approval_runner.submit_decision(engine, ticket_id, "compliance",
                                             decide("reject", "compliance", "The data retention terms are not acceptable."))
    assert (result["status"], result["stage"]) == ("needs_human_review", "approval_rejected")
    assert set(paused(engine, ticket_id).values()) == {"ended"}  # pending branches were cancelled, not left paused
    assert store.final_document(engine, ticket_id) is None
    assert "synthesize" not in [r["node"] for r in trace_of(engine, ticket_id, "")]
    assert store.claim_for_generation(engine, ticket_id, retry=True)  # Revenue can start a new run


# ================================================================ arbitration (BRIEFING §7)


def test_baa_dpa_mismatch_is_resolved_by_rule_for_claire_whitfield(engine, model):  # noqa: F811
    wrong = json.dumps({"content": draft("compliance", "UK"), "open_questions": []})  # DPA / UK GDPR for a US client
    right = json.dumps({"content": draft("compliance", "US"), "open_questions": []})
    model.overrides["generator:compliance"] = [wrong, wrong, wrong, right]  # Part 2 exhausts its 3 iterations
    ticket_id = to_part3(engine)
    entry = ticket_of(engine, ticket_id).arbitration[0]
    assert (entry["trigger"], entry["arbiter"], entry["decided_by"], entry["resolution"]) == (
        "baa-dpa-mismatch", "Claire Whitfield", "rule", "request_changes")
    assert entry["departments"] == ["compliance"]
    assert entry["evidence"]["problems"]["compliance"] == ["missing BAA clause under HIPAA",
                                                           "embeds a DPA / UK GDPR instrument for a US client"]
    assert "[arbitration baa-dpa-mismatch — Claire Whitfield]" in generator_calls(model, "compliance")[-1][2]
    packet = store.section_row(engine, ticket_id, "compliance").approval_packet
    assert packet["triggers"] == [] and "approve" in packet["allowed_actions"]  # fixed before Claire is asked
    nodes = [(r["node"], r["agent"]) for r in trace_of(engine, ticket_id, "")]
    assert ("arbitrate", "arbiter:rules (BRIEFING §7)") in nodes


def test_phi_detected_is_a_hard_stop(engine, model):  # noqa: F811
    leaked = draft("clinical", "US") + "\n\nPatient: John Smith was diagnosed with diabetes last year.\n"
    model.overrides["generator:clinical"] = json.dumps({"content": leaked, "open_questions": []})
    ticket_id = to_part3(engine)
    entry = ticket_of(engine, ticket_id).arbitration[0]
    assert (entry["trigger"], entry["arbiter"]) == ("phi-detected", "Claire Whitfield")
    packet = store.section_row(engine, ticket_id, "clinical").approval_packet
    assert "approve" not in packet["allowed_actions"] and "phi-detected" in packet["blocked_reasons"]["approve"]
    with pytest.raises(approval.InvalidDecision, match="phi-detected"):
        approval_runner.submit_decision(engine, ticket_id, "clinical", approve("clinical"))
    for department_id in ("revenue", "compliance"):
        approval_runner.submit_decision(engine, ticket_id, department_id, approve(department_id))
    assert store.final_document(engine, ticket_id) is None  # the synthesizer never runs
    assert "John Smith" not in json.dumps(store.trace(engine, ticket_id))
    assert "John Smith" not in (store.section_row(engine, ticket_id, "clinical").draft_content or "")
    with pytest.raises(final_document.SynthesisBlocked):
        ticket, metadata, sections = store.approval_input(engine, ticket_id)
        for s in sections:
            s.approval_status = "approved"
        final_document.build(ticket, metadata, sections, list(APPROVERS), [], ticket.updated_at)


def test_capacity_is_only_arbitrated_after_compliance_confirms(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    approval_runner.submit_decision(engine, ticket_id, "clinical", approve("clinical", capacity=500))
    approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    assert ticket_of(engine, ticket_id).pending_arbitration is None
    approval_runner.submit_decision(engine, ticket_id, "compliance", approve("compliance"))
    pending = ticket_of(engine, ticket_id).pending_arbitration
    assert pending["trigger"] == "capacity-vs-population" and pending["arbiter"] == "Tom Callahan"
    assert pending["evidence"] == {"confirmed_capacity": 500, "covered_population": 800, "population_unit": "employees"}
    assert paused(engine, ticket_id)["ticket"] == "arbiter_decision"
    with pytest.raises(approval.InvalidDecision, match="Tom Callahan"):  # the fixed arbiter, nobody else
        approval_runner.submit_arbitration(engine, ticket_id, {"trigger": "capacity-vs-population",
                                                               "arbiter": "Claire Whitfield", "resolution": "add_sites"})


def test_capacity_arbitration_reduce_population_revises_revenue(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    approval_runner.submit_decision(engine, ticket_id, "clinical", approve("clinical", capacity=500))
    for department_id in ("revenue", "compliance"):
        approval_runner.submit_decision(engine, ticket_id, department_id, approve(department_id))
    before = len(generator_calls(model, "revenue"))
    approval_runner.submit_arbitration(engine, ticket_id, {"trigger": "capacity-vs-population", "arbiter": "Tom Callahan",
                                                           "resolution": "reduce_population", "covered_population": 500})
    assert "now covers 500 employees: replace every mention of 800 employees" in generator_calls(model, "revenue")[before][2]
    assert paused(engine, ticket_id)["revenue"] == "await_approval"  # reopened: Tom approves the revised terms
    assert store.section_row(engine, ticket_id, "clinical").approval_status == "approved"  # the others keep theirs
    result = approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    assert result["status"] == "terminado"
    document = store.final_document(engine, ticket_id)
    assert "500 employees" in document["markdown"] and "capacity-vs-population | Tom Callahan | reduce_population" \
        in document["markdown"]


def test_a_section_that_ignores_the_arbiter_population_is_sent_back_by_rule(engine, model):  # noqa: F811
    """Found in the live run: after reduce_population the revised Revenue section still quoted the
    original population. The arbiter's ruling is now applied to the generator input AND checked."""
    normal = draft("revenue", "US")
    stale = normal.replace("simple price per person per month.", "simple price per person per month for 800 employees.")
    # Part 2 passes at once; after the ruling the generator keeps the old figure for the 3 iterations of the loop
    model.overrides["generator:revenue"] = [json.dumps({"content": c, "open_questions": []})
                                            for c in (normal, stale, stale, stale, normal)]
    ticket_id = to_part3(engine)
    approval_runner.submit_decision(engine, ticket_id, "clinical", approve("clinical", capacity=500))
    for department_id in ("revenue", "compliance"):
        approval_runner.submit_decision(engine, ticket_id, department_id, approve(department_id))
    approval_runner.submit_arbitration(engine, ticket_id, {"trigger": "capacity-vs-population", "arbiter": "Tom Callahan",
                                                           "resolution": "reduce_population", "covered_population": 500})
    revision = generator_calls(model, "revenue")[-3:]
    assert '"covered_population": 500' in revision[0][2] and "set the covered population to 500" in revision[0][2]
    # the Part 2 compliance evaluator enforces the ruling: 800 is a HC-FIG-01 violation fed back to the generator
    assert "800 no longer applies" in revision[1][2] and "Remove every mention of 800" in revision[1][2]
    assert store.section_row(engine, ticket_id, "revenue").generation_status == "needs_human_review"
    packet = store.section_row(engine, ticket_id, "revenue").approval_packet  # ...but still wrote "800 employees"
    assert packet["triggers"][0]["evidence"] == {"stale_population": "800", "agreed_population": 500,
                                                 "population_unit": "employees"}
    with pytest.raises(approval.InvalidDecision, match="capacity-vs-population"):  # cannot be approved as is
        approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    approval_runner.submit_decision(engine, ticket_id, "revenue",
                                    decide("request_changes", "revenue", "Use 500 employees, as Tom ruled."))
    assert store.final_document(engine, ticket_id) is None  # never synthesized with two populations
    result = approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    assert result["status"] == "terminado"
    assert "800" not in store.final_document(engine, ticket_id)["sections"][0]["content"]


def test_stale_population_is_detected_on_structured_state():
    from types import SimpleNamespace as NS
    ticket = NS(arbitration=[{"agreed_population": 2000}])
    metadata = NS(covered_population=3000, population_unit="students", client_country="UK")
    sections = [NS(department_id="revenue", draft_content="For 3,000 students the fee is [tbc].", approval_status="approved",
                   evaluation_results={}, confirmed_capacity=None),
                NS(department_id="clinical", draft_content="Sites for 2000 students.", approval_status="approved",
                   evaluation_results={}, confirmed_capacity=2000)]
    triggers = [t for t in approval.detect_triggers(ticket, metadata, sections) if t["trigger"] == "capacity-vs-population"]
    assert [(t["departments"], t["by_rule"], t["arbiter"]) for t in triggers] == [(["revenue"], True, "Tom Callahan")]
    assert "Replace every mention of 3000 students with 2000 students" in approval.feedback_for(triggers[0], "revenue")


def test_the_arbitration_limit_is_enforced(engine, model, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RFP_MAX_ARBITRATION_ROUNDS", "2")
    ticket_id = to_part3(engine)
    for department_id in ("revenue", "compliance"):
        approval_runner.submit_decision(engine, ticket_id, department_id, approve(department_id))
    approval_runner.submit_decision(engine, ticket_id, "clinical", approve("clinical", capacity=500))
    add_sites = {"trigger": "capacity-vs-population", "arbiter": "Tom Callahan", "resolution": "add_sites"}
    for round_ in (1, 2):  # Tom asks for more sites; Dr. Reid revises and still confirms 500
        assert ticket_of(engine, ticket_id).pending_arbitration["round"] == round_ - 1
        approval_runner.submit_arbitration(engine, ticket_id, add_sites)
        assert paused(engine, ticket_id)["clinical"] == "await_approval"
        approval_runner.submit_decision(engine, ticket_id, "clinical", approve("clinical", capacity=500))
    pending = ticket_of(engine, ticket_id).pending_arbitration
    assert pending["binding"] is True and pending["options"] == ["reduce_population", "request_new_run"]
    assert ticket_of(engine, ticket_id).approval_round == 2
    with pytest.raises(approval.InvalidDecision, match="Allowed resolutions"):
        approval_runner.submit_arbitration(engine, ticket_id, add_sites)
    calls = len(model.calls)
    result = approval_runner.submit_arbitration(engine, ticket_id, {**add_sites, "resolution": "reduce_population",
                                                                    "covered_population": 500})
    assert result["status"] == "terminado" and len(model.calls) == calls  # binding: no further revision cycle
    assert "500 employees" in store.final_document(engine, ticket_id)["markdown"]


# ================================================================ trace


def test_every_node_execution_is_traced_with_agent_input_output_and_timestamp(engine, model):  # noqa: F811
    ticket_id = to_part3(engine)
    approval_runner.submit_decision(engine, ticket_id, "revenue", approve("revenue"))
    rows = store.trace(engine, ticket_id)
    assert all(r["agent"] and r["node"] and r["started_at"] <= r["finished_at"] and r["status"] in
               ("ok", "interrupted", "error") for r in rows)
    agents = [r["agent"] for r in rows]
    assert {"agent:classifier", "agent:synthesizer", "agent:generator:revenue", "arbiter:rules (BRIEFING §7)",
            "human:Tom Callahan", "coordinator:dispatch"} <= set(agents)
    first = {thread: next(r["id"] for r in rows if (r["thread_id"] or "").endswith(thread)) for thread in
             (":intake", ":generation", ":revenue")}
    assert first[":intake"] < first[":generation"] < first[":revenue"]  # Part 1 → Part 2 → Part 3, in order
    decision = next(r for r in rows if r["node"] == "record_decision")
    assert decision["input"]["decision"]["approver"] == "Tom Callahan" and decision["output"]["outcome"] == "approved"
    state = approval_graph.branch_graph(engine).get_state(approval_graph.branch_config(engine, ticket_id, "revenue"))
    assert [t["node"] for t in state.values["trace"]] == ["prepare_packet", "await_approval", "record_decision"]
