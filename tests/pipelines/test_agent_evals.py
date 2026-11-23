"""Evals of the LangGraph agent, checked against recorded traces (no live models or services).

    python -m pytest tests/pipelines/test_agent_evals.py

The traces come from `python -m services.agent.record_evals` (data/eval/agent_eval_traces.json):
one full run per case of data/eval/agent-eval-cases.json against the real knowledge base,
incident manager and inventory. Each case is checked on:

- routing: which nodes ran, in which order, and which sources were used (RAG, a tool, or both);
- the trace: every source consulted once, generation only after retrieval, a checkpoint after
  every node, minimal state;
- the answer: anchored in the knowledge base of the HealthCore BRIEFING, or in the data the
  tool returned; honest fallbacks when a tool failed or found nothing.

These evals extend the RAG tests (tests/pipelines/test_rag.py), they do not replace them.
"""

import json
from pathlib import Path

import pytest

from services.agent.nodes import NO_INFORMATION_ANSWER, STATUS_LABELS
from services.agent.state import STATE_KEYS

EVAL_DIR = Path(__file__).resolve().parents[2] / "data" / "eval"
CASES = {c["id"]: c for c in json.loads((EVAL_DIR / "agent-eval-cases.json").read_text(encoding="utf-8"))["cases"]}
TRACES = {t["case_id"]: t for t in json.loads((EVAL_DIR / "agent_eval_traces.json").read_text(encoding="utf-8"))["traces"]}
TOOL_NODES = {"lookup_tickets", "lookup_stock"}
RAG_NODES = {"retrieve_context", "generate_answer", "answer_no_information"}


def nodes_run(trace):
    return [step["node"] for step in trace["steps"]]


def step_output(trace, node):
    return next(step["output"] for step in trace["steps"] if step["node"] == node)


def tool_result(trace):
    for node, key in (("lookup_tickets", "ticket_result"), ("lookup_stock", "stock_result")):
        if node in nodes_run(trace):
            return step_output(trace, node)[key]
    return None


def by(predicate):
    return [case_id for case_id in sorted(CASES) if predicate(CASES[case_id]["expect"])]


def test_every_case_has_a_recorded_trace_of_the_same_question():
    assert set(TRACES) == set(CASES) and len(CASES) >= 3
    for case_id, trace in TRACES.items():
        assert trace["question"] == CASES[case_id]["question"][:4000]
        assert trace["fault"] == CASES[case_id].get("fault")


# ---------------------------------------------------------------- routing (Part 2)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_route_matches_the_expected_nodes_in_order(case_id):
    trace = TRACES[case_id]
    expected = CASES[case_id]["expect"]["route"]
    assert nodes_run(trace) == expected
    assert trace["route"] == " > ".join(expected)


@pytest.mark.parametrize("case_id", by(lambda e: "sources" in e))
def test_trace_shows_which_sources_were_used_and_in_which_order(case_id):
    assert TRACES[case_id]["sources"] == CASES[case_id]["expect"]["sources"]


@pytest.mark.parametrize("case_id", by(lambda e: e.get("sources") == "incidents"))
def test_ticket_questions_use_the_tool_not_the_rag(case_id):
    run = nodes_run(TRACES[case_id])
    assert "lookup_tickets" in run and not RAG_NODES & set(run)


@pytest.mark.parametrize("case_id", by(lambda e: e.get("sources") == "inventory"))
def test_stock_questions_use_the_inventory_tool_not_the_rag(case_id):
    run = nodes_run(TRACES[case_id])
    assert "lookup_stock" in run and not RAG_NODES & set(run) and "lookup_tickets" not in run


@pytest.mark.parametrize("case_id", by(lambda e: e.get("sources") == "knowledge_base"))
def test_policy_questions_use_the_rag_not_a_tool(case_id):
    trace = TRACES[case_id]
    run = nodes_run(trace)
    assert "retrieve_context" in run and not TOOL_NODES & set(run)
    plan = step_output(trace, "plan_sources")["plan"]
    assert plan["knowledge_base"] and plan["ticket"] is None and plan["inventory"] is None


def test_a_question_that_needs_both_uses_the_tool_then_the_rag():
    run = nodes_run(TRACES["ticket-and-policy"])
    assert run.index("lookup_tickets") < run.index("retrieve_context") < run.index("generate_answer")


# ---------------------------------------------------------------- fallbacks


@pytest.mark.parametrize("case_id", by(lambda e: "tool_status" in e))
def test_failed_or_empty_tool_routes_to_its_fallback_and_answers_honestly(case_id):
    expect, trace = CASES[case_id]["expect"], TRACES[case_id]
    result = tool_result(trace)
    assert result["status"] == expect["tool_status"]
    if "tool_error" in expect:
        assert result["error"] == expect["tool_error"]
    fallback = "ticket_fallback" if "lookup_tickets" in nodes_run(trace) else "stock_fallback"
    assert fallback in nodes_run(trace) and trace["status"] == "answered"  # the run did not break
    assert not any(label in trace["answer"] for label in STATUS_LABELS.values())  # no invented status
    assert "en stock" not in trace["answer"]  # no invented stock


def test_the_timeout_is_the_explicit_four_seconds():
    result = tool_result(TRACES["ticket-timeout"])
    assert result["error"] == "timeout after 4 s" and 3900 <= result["duration_ms"] < 6000


# ---------------------------------------------------------------- trace invariants (Part 1)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_trace_invariants(case_id):
    trace = TRACES[case_id]
    run = nodes_run(trace)
    assert all(step["status"] == "ok" for step in trace["steps"])
    assert [step["step"] for step in trace["steps"]] == list(range(1, len(run) + 1))
    assert len(run) == len(set(run))  # every node (and source) at most once; no monolithic query()
    if "generate_answer" in run:
        assert run.index("retrieve_context") < run.index("generate_answer")
        assert step_output(trace, "retrieve_context")["context"], "generation only runs with retrieved context"
    checkpoints = trace["checkpoints"]
    assert len(checkpoints) == len(run) + 2 and checkpoints[-1]["next"] == []
    assert [c["next"] for c in checkpoints[2:-1]] == [[node] for node in run[1:]]
    assert set(checkpoints[-1]["state_keys"]) <= STATE_KEYS


# ---------------------------------------------------------------- answers


@pytest.mark.parametrize("case_id", by(lambda e: "retrieved_top3" in e))
def test_retrieval_finds_the_expected_chunk(case_id):
    context = step_output(TRACES[case_id], "retrieve_context")["context"]
    top3 = [f"{c['source_document']}#{c['chunk_index']}" for c in context[:3]]
    assert CASES[case_id]["expect"]["retrieved_top3"] in top3


@pytest.mark.parametrize("case_id", by(lambda e: "generate_answer" in e["route"]))
def test_answer_is_anchored_in_the_knowledge_base(case_id):
    expect, trace = CASES[case_id]["expect"], TRACES[case_id]
    answer = trace["answer"]
    rag_answer = step_output(trace, "generate_answer")["rag_answer"]
    assert trace["status"] == "answered" and answer.endswith(rag_answer)
    lowered = rag_answer.lower()
    for text in expect.get("answer_contains_all", []):
        assert text.lower() in lowered, f"{case_id}: missing {text!r}"
    if any_of := expect.get("answer_contains_any"):
        assert any(text.lower() in lowered for text in any_of), f"{case_id}: none of {any_of}"
    for text in expect.get("answer_not_contains", []):
        assert text.lower() not in lowered, f"{case_id}: must not say {text!r}"
    if starts := expect.get("answer_starts_with_any"):
        assert answer.lstrip().startswith(tuple(starts))
    assert "Fuente:" in rag_answer


@pytest.mark.parametrize("case_id", by(lambda e: e.get("answer_reports_tool_data")))
def test_tool_answers_repeat_exactly_what_the_service_returned(case_id):
    trace = TRACES[case_id]
    result = tool_result(trace)
    assert result["status"] == "ok"
    answer = trace["answer"]
    if "tickets" in result:
        for ticket in result["tickets"]:
            assert f"#{ticket['id']}" in answer and STATUS_LABELS[ticket["status"]] in answer
        if result["query"].get("ticket_id") is None:
            assert f"Hay {result['total']} ticket" in answer
        assert f"Fuente: gestor de incidentes ({result['endpoint']})" in answer
    else:
        for product in result["products"]:
            assert f"{product['name']} (SKU {product['sku']}" in answer and f": {product['current_stock']} " in answer
        assert "Fuente: inventario (GET /inventory/products)" in answer


@pytest.mark.parametrize("case_id", by(lambda e: "answer_starts_with_any" in e and "generate_answer" not in e["route"]))
def test_fallback_answers_start_with_the_honest_message(case_id):
    assert TRACES[case_id]["answer"].startswith(tuple(CASES[case_id]["expect"]["answer_starts_with_any"]))


def test_off_topic_question_is_answered_honestly_without_generation():
    trace = TRACES["off-topic"]
    assert step_output(trace, "retrieve_context")["context"] == []
    assert "generate_answer" not in nodes_run(trace)
    assert trace["answer"] == NO_INFORMATION_ANSWER


def test_empty_question_never_reaches_a_source():
    trace = TRACES["empty-question"]
    assert trace["status"] == "rejected" and trace["answer"] is None and trace["sources"] is None
    assert not (TOOL_NODES | RAG_NODES | {"plan_sources"}) & set(nodes_run(trace))


@pytest.mark.parametrize("case_id", by(lambda e: e.get("knowledge_question_without_ticket")))
def test_mixed_question_sends_only_the_policy_part_to_the_rag(case_id):
    plan = step_output(TRACES[case_id], "plan_sources")["plan"]
    assert plan["knowledge_question"] and plan["ticket"]
    assert str(plan["ticket"].get("ticket_id")) not in plan["knowledge_question"]
