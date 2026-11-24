"""Memory evidence recorded against the real agent (docs/agent/memory/evidence/memory-cycles.json,
`python -m services.agent.memory.record_evidence`), checked without calling the models again.

    python -m pytest tests/pipelines/test_agent_memory_evidence.py
"""

import json
from pathlib import Path

import pytest

EVIDENCE = json.loads((Path(__file__).resolve().parents[2] / "docs" / "agent" / "memory" / "evidence"
                       / "memory-cycles.json").read_text(encoding="utf-8"))
SCENARIOS = {s["id"]: s for s in EVIDENCE["scenarios"]}


def test_cycle_approved_writes_memory_and_a_later_conversation_uses_it():
    s = SCENARIOS["cycle-approved"]
    propose, approve, later = s["turns"]
    assert propose["memory"]["proposal"] and "¿Quieres que recuerde esto" in propose["answer"]
    assert approve["decision"]["label"] == "approve" and approve["memory"]["decision"]["status"] == "approved"
    assert len(s["memories_after"]) == len(s["memories_before"]) + 1
    assert later["conversation_id"] != propose["conversation_id"] and later["actor"] != propose["actor"]
    assert later["recalled"] == s["memories_after"][-1:] and "Memoria del agente" in later["answer"]
    assert "coordinador" in later["answer"]


def test_cycle_rejected_leaves_memory_unchanged_and_is_recorded():
    s = SCENARIOS["cycle-rejected"]
    propose, reject, later = s["turns"]
    assert propose["memory"]["proposal"] and reject["memory"]["decision"]["status"] == "rejected"
    assert s["memories_after"] == s["memories_before"] and later["recalled"] == []
    [proposal] = s["proposals"]
    assert proposal["status"] == "rejected" and proposal["decided_by"] == "priya.nair" and proposal["resolved_at"]
    assert [e["event"] for e in s["audit"]] == ["proposed", "rejected"]


def test_briefing_examples_that_must_not_generate_a_proposal():
    s = SCENARIOS["not-memorable"]
    assert all(t["memory"]["proposal"] is None for t in s["turns"])
    assert s["memories_after"] == s["memories_before"]
    phi = s["turns"][1]
    assert phi["route"] == "receive_question > refuse_phi" and phi["memory"]["blocked_phi"]
    assert "No puedo procesar ni recordar información que identifique a un paciente" in phi["answer"]
    assert "Johnson" not in json.dumps(EVIDENCE)  # never stored anywhere, not even in the audit or the trace
    assert [p["status"] for p in s["proposals"]] == ["blocked_phi"]


@pytest.mark.parametrize("scenario, status, changes", [
    ("staff-preference-edited", "approved_edited", 1), ("ambiguous", "discarded_ambiguous", 0),
    ("topic-change", "discarded_topic_change", 0), ("approve-and-ask", "approved", 1),
])
def test_decisions_are_classified_and_only_approvals_write(scenario, status, changes):
    s = SCENARIOS[scenario]
    [proposal] = s["proposals"]
    assert proposal["status"] == status and proposal["decision_classifier"] == "model"
    assert len(s["memories_after"]) - len(s["memories_before"]) == changes
    assert s["audit"][0]["event"] == "proposed" and s["audit"][-1]["event"] == status


def test_edit_stores_the_users_wording():
    s = SCENARIOS["staff-preference-edited"]
    assert s["memories_after"][-1] == s["proposals"][0]["final_content"] != s["proposals"][0]["content"]


def test_conversation_continues_after_resolving_the_proposal():
    topic = SCENARIOS["topic-change"]["turns"][1]
    assert "resolve_pending_proposal > recall_memories" in topic["route"] and "documento de identidad" in topic["answer"]
    ask = SCENARIOS["approve-and-ask"]["turns"][1]
    assert ask["answer"].startswith("Listo, lo recordaré") and "Georgia" in ask["answer"]


def test_only_one_pending_proposal_per_conversation():
    for s in EVIDENCE["scenarios"]:
        for conversation in {t["conversation_id"] for t in s["turns"]}:
            pending = [p for p in s["proposals"] if p["conversation_id"] == conversation and p["status"] == "pending"]
            assert len(pending) <= 1


def test_poisoning_attempts_never_become_proposals():
    s = SCENARIOS["poisoning"]
    assert all(t["memory"]["proposal"] is None for t in s["turns"]) and s["proposals"] == []
    assert s["memories_after"] == s["memories_before"]


def test_every_proposal_has_an_auditable_outcome():
    for s in EVIDENCE["scenarios"]:
        for p in s["proposals"]:
            assert p["status"] != "pending" and p["resolved_at"] and p["created_at"]
            assert any(e["proposal_id"] == p["id"] for e in s["audit"])
    for memory in EVIDENCE["final_memories"]:
        assert memory["approved_by"] and memory["source_proposal_id"]
