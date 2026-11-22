"""Evals of the LangGraph agent, checked against recorded traces (no live models here).

    python -m pytest tests/pipelines/test_agent_evals.py

The traces come from `python -m services.agent.record_evals` (data/eval/agent_eval_traces.json),
one full run per case in data/eval/agent-eval-cases.json. Each case is checked on:

- the trace: which nodes ran, in which order, that retrieval ran once and before generation,
  that every step succeeded and that a checkpoint exists after every node;
- the answer: anchored in the knowledge base of the HealthCore BRIEFING (fees, insurers,
  contacts), and honest when there is nothing to answer from.

These evals extend the RAG tests (tests/pipelines/test_rag.py), they do not replace them.
"""

import json
from pathlib import Path

import pytest

from services.agent.nodes import NO_INFORMATION_ANSWER
from services.agent.state import STATE_KEYS

EVAL_DIR = Path(__file__).resolve().parents[2] / "data" / "eval"
CASES = {c["id"]: c for c in json.loads((EVAL_DIR / "agent-eval-cases.json").read_text(encoding="utf-8"))["cases"]}
TRACES = {t["case_id"]: t for t in json.loads((EVAL_DIR / "agent_eval_traces.json").read_text(encoding="utf-8"))["traces"]}


def nodes_run(trace):
    return [step["node"] for step in trace["steps"]]


def step_output(trace, node):
    return next(step["output"] for step in trace["steps"] if step["node"] == node)


def test_every_case_has_a_recorded_trace_of_the_same_question():
    assert set(TRACES) == set(CASES) and len(CASES) >= 3
    for case_id, trace in TRACES.items():
        assert trace["question"] == CASES[case_id]["question"][:4000]


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_route_matches_the_expected_nodes_in_order(case_id):
    trace = TRACES[case_id]
    expected = CASES[case_id]["expect"]["route"]
    assert nodes_run(trace) == expected
    assert trace["route"] == " > ".join(expected)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_trace_invariants(case_id):
    trace = TRACES[case_id]
    run = nodes_run(trace)
    assert all(step["status"] == "ok" for step in trace["steps"])
    assert [step["step"] for step in trace["steps"]] == list(range(1, len(run) + 1))
    assert run.count("retrieve_context") <= 1  # retrieval never runs twice (no monolithic query())
    if "generate_answer" in run:
        assert run.index("retrieve_context") < run.index("generate_answer")
        assert step_output(trace, "retrieve_context")["context"], "generation only runs with retrieved context"
    # checkpointing: one checkpoint per node plus the input ones, and the last one is final
    checkpoints = trace["checkpoints"]
    assert len(checkpoints) == len(run) + 2 and checkpoints[-1]["next"] == []
    assert [c["next"] for c in checkpoints[2:-1]] == [[node] for node in run[1:]]
    # minimal state: only AgentState keys, no conversation history
    assert set(checkpoints[-1]["state_keys"]) <= STATE_KEYS


@pytest.mark.parametrize("case_id", [c for c in sorted(CASES) if "retrieved_top3" in CASES[c]["expect"]])
def test_retrieval_finds_the_expected_chunk(case_id):
    context = step_output(TRACES[case_id], "retrieve_context")["context"]
    top3 = [f"{c['source_document']}#{c['chunk_index']}" for c in context[:3]]
    assert CASES[case_id]["expect"]["retrieved_top3"] in top3


@pytest.mark.parametrize("case_id", [c for c in sorted(CASES) if "generate_answer" in CASES[c]["expect"]["route"]])
def test_answer_is_anchored_in_the_knowledge_base(case_id):
    expect = CASES[case_id]["expect"]
    trace = TRACES[case_id]
    answer = trace["answer"]
    assert trace["status"] == "answered" and answer == step_output(trace, "generate_answer")["answer"]
    lowered = answer.lower()
    for text in expect.get("answer_contains_all", []):
        assert text.lower() in lowered, f"{case_id}: missing {text!r}"
    if any_of := expect.get("answer_contains_any"):
        assert any(text.lower() in lowered for text in any_of), f"{case_id}: none of {any_of}"
    for text in expect.get("answer_not_contains", []):
        assert text.lower() not in lowered, f"{case_id}: must not say {text!r}"
    if starts := expect.get("answer_starts_with_any"):
        assert answer.lstrip().startswith(tuple(starts))
    assert "Fuente:" in answer  # traceable to a document/section


def test_off_topic_question_is_answered_honestly_without_generation():
    trace = TRACES["off-topic"]
    assert step_output(trace, "retrieve_context")["context"] == []
    assert "generate_answer" not in nodes_run(trace)
    assert trace["answer"] == NO_INFORMATION_ANSWER


def test_empty_question_never_reaches_retrieval():
    trace = TRACES["empty-question"]
    assert trace["status"] == "rejected" and trace["answer"] is None
    assert "retrieve_context" not in nodes_run(trace) and trace["error"]
