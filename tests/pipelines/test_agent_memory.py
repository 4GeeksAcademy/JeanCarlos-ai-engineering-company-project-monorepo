"""Agent memory (Hito 8 Parte 1): PHI guard, store invariants, consolidation, the two model
calls (stubbed) and the full proposal → decision → memory flow through the graph.

    python -m pytest tests/pipelines/test_agent_memory.py
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.pipelines import rag
from services.agent import planner
from services.agent.memory import llm as memory_llm
from services.agent.memory import store as memory_store
from services.agent.memory.phi_guard import find_phi, redact_for_audit
from services.agent.memory.store import MemoryPolicyError, MemoryStore, Proposal
from services.agent.runner import AgentRuntime
from tests.memory_fixtures import fake_embed

MEMORY_DIR = Path(__file__).resolve().parents[2] / "services" / "agent" / "memory"
MANCHESTER = ("En la clínica de Manchester el proceso de referidos internos ahora pasa primero por el coordinador "
              "antes que por el especialista — cambió el trimestre pasado.")
AUSTIN = ("Esa alerta de no-show elevado en la clínica de Austin fue porque hubo un cierre de carretera esa semana, "
          "no un problema real del programa de recordatorios.")
DIANE = "El reporte semanal para Diane Foster debe incluir vacantes por rol, no solo por clínica — eso lo pidió hace dos semanas."


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite", embed=fake_embed)


def proposal(content=MANCHESTER, kind="clinic_correction", **scope):
    return Proposal(kind=kind, content=content, reason="test", **scope)


# ---------------------------------------------------------------- PHI guard (BRIEFING: never stored)


@pytest.mark.parametrize("text", [
    "El paciente Johnson canceló su cita de mañana, apúntalo.",  # BRIEFING example
    "Patient Smith had a failed referral",  # BRIEFING example
    "La Sra. García tiene diabetes tipo 2", "MRN 448812 pendiente", "NHS number 943 476 5919",
    "SSN 123-45-6789", "Su fecha de nacimiento es 03/04/1980", "Glucosa en 180 mg/dL", "Código E11.9",
    "Número de seguro del afiliado XG4482211", "Diagnosticado con hepatitis C", "Llamar al 555-123-4567",
])
def test_phi_guard_blocks_patient_information_under_hipaa_and_uk_gdpr(text):
    assert find_phi(text)


@pytest.mark.parametrize("text", [MANCHESTER, AUSTIN, DIANE,
                                  "El sistema de referidos falla los lunes en la mañana por el batch nocturno.",
                                  "Los pacientes de Medicare no pagan cargo por no-show.",
                                  "¿Cuál es la tasa de no-show de esta semana?"])
def test_phi_guard_lets_operational_facts_through(text):
    assert find_phi(text) == []


def test_audit_redaction_keeps_categories_never_the_content():
    redacted = redact_for_audit("El paciente Johnson canceló su cita")
    assert "Johnson" not in redacted and "patient_name" in redacted


# ---------------------------------------------------------------- store invariants


def test_one_pending_proposal_per_conversation(store):
    store.propose(proposal(), conversation_id="c1", proposed_to="priya", source_message=MANCHESTER)
    with pytest.raises(MemoryPolicyError, match="pending"):
        store.propose(proposal(AUSTIN, "incident_pattern"), conversation_id="c1", proposed_to="priya", source_message=AUSTIN)
    store.propose(proposal(AUSTIN, "incident_pattern"), conversation_id="c2", proposed_to="priya", source_message=AUSTIN)


def test_proposals_with_phi_or_unknown_kind_are_refused(store):
    with pytest.raises(MemoryPolicyError, match="phi"):
        store.propose(proposal("El paciente Johnson canceló"), conversation_id="c", proposed_to="p", source_message="x")
    with pytest.raises(MemoryPolicyError, match="kind"):
        store.propose(proposal(kind="clinical_note"), conversation_id="c", proposed_to="p", source_message="x")
    assert store.list_memories() == []


def test_approval_writes_memory_with_who_authorized_it_and_an_audit_trail(store):
    pending = store.propose(proposal(scope_clinic="Manchester"), conversation_id="c1", proposed_to="priya",
                            source_message=MANCHESTER)
    assert store.list_memories() == []  # proposing never writes
    closed = store.resolve(pending["id"], label="approve", confidence=0.95, classifier="model", decided_by="priya",
                           decision_message="Sí, recuérdalo")
    [memory] = store.list_memories()
    assert closed["status"] == "approved" and closed["memory_id"] == memory["id"]
    assert memory["approved_by"] == "priya" and memory["source_proposal_id"] == pending["id"]
    events = [e["event"] for e in store.audit_trail("c1")]
    assert events == ["proposed", "memory_written", "approved"]
    assert closed["decision_message"] == "Sí, recuérdalo" and closed["resolved_at"]


@pytest.mark.parametrize("label, status", [("reject", "rejected"), ("ambiguous", "discarded_ambiguous"),
                                           ("unrelated", "discarded_topic_change")])
def test_anything_but_approval_leaves_memory_unchanged_but_recorded(store, label, status):
    pending = store.propose(proposal(), conversation_id="c1", proposed_to="priya", source_message=MANCHESTER)
    closed = store.resolve(pending["id"], label=label, confidence=0.9, classifier="model", decided_by="priya",
                           decision_message="…")
    assert closed["status"] == status and closed["memory_id"] is None
    assert store.list_memories() == []
    assert [e["event"] for e in store.audit_trail("c1")] == ["proposed", status]


def test_an_edit_that_adds_patient_data_is_blocked(store):
    pending = store.propose(proposal(), conversation_id="c1", proposed_to="priya", source_message=MANCHESTER)
    closed = store.resolve(pending["id"], label="edit", confidence=0.9, classifier="model", decided_by="priya",
                           decision_message="sí pero añade que el paciente Johnson espera", edited_content=
                           "En Manchester el paciente Johnson espera la referencia")
    assert closed["status"] == "blocked_phi" and store.list_memories() == []
    assert "Johnson" not in json.dumps(store.audit_trail()) and "Johnson" not in json.dumps(store.proposals())


def test_unanswered_proposal_expires_and_is_never_approved(store):
    pending = store.propose(proposal(), conversation_id="c1", proposed_to="priya", source_message=MANCHESTER)
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
    store._conn.execute("update memory_proposals set created_at = ? where id = ?", (old, pending["id"]))
    assert store.pending("c1") is None
    assert store.get_proposal(pending["id"])["status"] == "expired" and store.list_memories() == []


def _approve(store, content, kind="clinic_correction", conversation="c", **scope):
    pending = store.propose(proposal(content, kind, **scope), conversation_id=conversation, proposed_to="priya",
                            source_message=content)
    return store.resolve(pending["id"], label="approve", confidence=1, classifier="model", decided_by="priya",
                         decision_message="sí")


def test_near_duplicates_of_the_same_scope_are_superseded(store):
    _approve(store, MANCHESTER, conversation="a", scope_clinic="Manchester")
    _approve(store, MANCHESTER.replace("trimestre pasado", "trimestre anterior"), conversation="b", scope_clinic="Manchester")
    assert len(store.list_memories()) == 1 and len(store.list_memories("superseded")) == 1


def test_consolidation_rechecks_phi_expires_unused_and_caps(store, monkeypatch):
    _approve(store, MANCHESTER, conversation="a")
    _approve(store, AUSTIN, "incident_pattern", conversation="b")
    _approve(store, DIANE, "staff_preference", conversation="c", scope_staff="Diane Foster")
    # a row that bypassed the guard (e.g. older data): the re-check archives it
    store._conn.execute("update agent_memories set content = 'El paciente Johnson llega tarde' where kind = 'staff_preference'")
    counts = store.consolidate()
    assert counts["phi_recheck"] == 1
    # incident patterns expire after 90 days without use
    counts = store.consolidate(now=datetime.now(timezone.utc) + timedelta(days=100))
    assert counts["expired"] == 1 and [m["kind"] for m in store.list_memories()] == ["clinic_correction"]
    monkeypatch.setattr(memory_store, "MAX_ACTIVE_MEMORIES", 0)
    assert store.consolidate()["capacity"] == 1 and store.list_memories() == []
    assert {e["details"]["reason"] for e in store.audit_trail() if e["event"] == "archived"} == {"phi_recheck", "expired", "capacity"}


def test_recall_returns_relevant_memories_and_preferences_only_to_their_owner(store):
    _approve(store, MANCHESTER, conversation="a", scope_clinic="Manchester")
    _approve(store, DIANE, "staff_preference", conversation="b", scope_staff="Diane Foster")
    found = store.recall("¿Cómo funciona el proceso de referidos internos en Manchester?", min_score=0.3)
    assert [m["content"] for m in found] == [MANCHESTER]
    assert store.recall("¿qué incluye el reporte semanal de vacantes por rol?", actor="marcus", min_score=0.3) == []
    assert store.recall("reporte semanal para Diane Foster con vacantes", actor="marcus", min_score=0.3)


def test_memory_never_touches_the_knowledge_base():
    """No code of the memory package names the RAG collection or imports a Qdrant client (the
    docstrings may explain why); the store is its own SQLite file."""
    import ast

    for path in MEMORY_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body
                      and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and id(n) not in docstrings]
        imports = [a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
        imports += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any("healthcore_knowledge" in s for s in strings), path.name
        assert not any("qdrant" in i.lower() for i in imports), path.name
    assert memory_store.memory_database().name == "agent_memory.sqlite"


# ---------------------------------------------------------------- the two model calls (wrappers)


def fake_model(monkeypatch, content):
    chat = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]))
    monkeypatch.setattr(memory_llm, "llm_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=chat)))
    monkeypatch.setattr(memory_llm, "settings", lambda: SimpleNamespace(generation_model="m"))


def real_wrappers(monkeypatch):
    import importlib
    module = importlib.reload(memory_llm)
    return module


def test_self_evaluation_normalizes_empty_scopes_and_proposes(monkeypatch):
    module = real_wrappers(monkeypatch)
    fake_model(monkeypatch, json.dumps({"memorable": True, "kind": "incident_pattern", "scope_country": "",
                                        "content": "El sistema de referidos falla los lunes en la mañana por el batch nocturno.",
                                        "reason": "patrón", "patient_data_requested": False}))
    result = module.self_evaluate("El sistema de referidos falla los lunes en la mañana por el batch nocturno.", "ok", [])
    assert result["outcome"] == "propose" and result["scope_country"] is None


@pytest.mark.parametrize("content, message, reason", [
    ("HealthCore acepta el seguro Humana en Texas", "Corrección: HealthCore acepta el seguro Humana en Texas", "documented policy"),
    ("La clínica de Dallas cierra los domingos y el lunes abre a las 6", "Dallas abre a las 7:30 los sábados", "not grounded"),
])
def test_self_evaluation_drops_policy_changes_and_ungrounded_proposals(monkeypatch, content, message, reason):
    module = real_wrappers(monkeypatch)
    fake_model(monkeypatch, json.dumps({"memorable": True, "kind": "clinic_correction", "content": content, "reason": "x"}))
    result = module.self_evaluate(message, "ok", [])
    assert result["outcome"] == "none" and reason in result["reason"]


def test_self_evaluation_blocks_patient_data_explicitly(monkeypatch):
    module = real_wrappers(monkeypatch)
    fake_model(monkeypatch, json.dumps({"memorable": False, "patient_data_requested": True, "reason": "paciente"}))
    assert module.self_evaluate("apunta lo de la cita de mañana", "ok", [])["outcome"] == "blocked_phi"


def test_self_evaluation_failure_proposes_nothing(monkeypatch):
    module = real_wrappers(monkeypatch)
    monkeypatch.setattr(module, "llm_client", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    assert module.self_evaluate(MANCHESTER, "ok", [])["outcome"] == "none"


@pytest.mark.parametrize("model_output, message, label", [
    ({"label": "approve", "confidence": 0.95, "evidence": "Sí, recuérdalo"}, "Sí, recuérdalo", "approve"),
    ({"label": "approve", "confidence": 0.6, "evidence": "ok"}, "ok", "ambiguous"),  # below threshold
    ({"label": "approve", "confidence": 0.99, "evidence": "sí, guárdalo"}, "gracias", "ambiguous"),  # evidence not in message
    ({"label": "reject", "confidence": 0.9, "evidence": "no"}, "no", "reject"),
    ({"label": "edit", "confidence": 0.9, "evidence": "sí pero", "edited_content": None}, "sí pero", "ambiguous"),
    ({"label": "unrelated", "confidence": 0.9, "evidence": "¿Qué debo traer?"}, "¿Qué debo traer?", "unrelated"),
])
def test_decision_classifier_never_approves_by_default(monkeypatch, model_output, message, label):
    module = real_wrappers(monkeypatch)
    fake_model(monkeypatch, json.dumps(model_output))
    decision = module.classify_decision({"content": MANCHESTER}, message)
    assert decision["label"] == label
    if label == "unrelated":
        assert decision["remaining_question"] == message


def test_decision_classifier_failure_discards(monkeypatch):
    module = real_wrappers(monkeypatch)
    monkeypatch.setattr(module, "llm_client", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    assert module.classify_decision({"content": MANCHESTER}, "sí")["label"] == "ambiguous"


# ---------------------------------------------------------------- full flow through the graph


@pytest.fixture
def agent(monkeypatch, tmp_path, isolated_memory):
    """The real graph and store; planner, RAG and the two memory calls scripted."""
    generated = []
    monkeypatch.setattr(memory_store, "RECALL_MIN_SCORE", 0.3)  # the test embedding is coarser than the real one
    monkeypatch.setattr(planner, "plan_with_model", lambda q: (_ for _ in ()).throw(RuntimeError("no model")))
    monkeypatch.setattr(rag, "retrieve", lambda q: [])
    monkeypatch.setattr(rag, "generate_answer",
                        lambda q, context: generated.append(context) or "Respuesta con memoria.\nFuente: Memoria del agente")

    def evaluation(message, answer, memories):
        if "Manchester" in message and "?" not in message:
            return {"outcome": "propose", "kind": "clinic_correction", "content": MANCHESTER, "scope_clinic": "Manchester",
                    "scope_country": "UK", "scope_staff": None, "reason": "corrección operativa"}
        return {"outcome": "none", "reason": "nada nuevo"}

    def decision(pending, message):
        label = {"sí": "approve", "no": "reject"}.get(message.split(",")[0].strip().lower(), "ambiguous")
        remaining = message.split(",", 1)[1].strip() if "," in message else None
        return {"label": label, "confidence": 0.95, "classifier": "model", "edited_content": None, "remaining_question": remaining}

    monkeypatch.setattr(memory_llm, "self_evaluate", evaluation)
    monkeypatch.setattr(memory_llm, "classify_decision", decision)
    return SimpleNamespace(runtime=AgentRuntime(tmp_path / "agent.sqlite"), store=isolated_memory, generated=generated)


def test_cycle_approved_is_recalled_in_a_later_conversation(agent):
    first = agent.runtime.run(MANCHESTER, actor="priya")
    assert first.memory["proposal"]["content"] == MANCHESTER and "¿Quieres que recuerde esto" in first.answer
    assert agent.store.list_memories() == []

    second = agent.runtime.run("sí", conversation_id=first.conversation_id, actor="priya")
    assert second.memory["decision"]["status"] == "approved" and second.answer.startswith("Listo, lo recordaré")
    trace = agent.runtime.trace(second.run_id)
    assert trace["route"] == "receive_question > resolve_pending_proposal > compose_answer > self_evaluate"

    later = agent.runtime.run("¿Cómo funciona el proceso de referidos internos en Manchester?", actor="marcus")
    later_trace = agent.runtime.trace(later.run_id)
    assert "recall_memories" in later_trace["route"] and "generate_answer" in later_trace["route"]
    memory_blocks = [c for c in agent.generated[-1] if c["source_document"] == "agent-memory"]
    assert [c["text"] for c in memory_blocks] == [MANCHESTER] and "priya" in memory_blocks[0]["section"]


def test_cycle_rejected_leaves_memory_unchanged(agent):
    first = agent.runtime.run(MANCHESTER, actor="priya")
    second = agent.runtime.run("no", conversation_id=first.conversation_id, actor="priya")
    assert second.memory["decision"]["status"] == "rejected" and second.answer == "Entendido, no lo guardo."
    assert agent.store.list_memories() == []
    [record] = agent.store.proposals(first.conversation_id)
    assert record["status"] == "rejected" and record["decided_by"] == "priya" and record["decision_message"] == "no"


def test_decision_and_a_new_question_in_the_same_message(agent):
    first = agent.runtime.run(MANCHESTER, actor="priya")
    second = agent.runtime.run("sí, ¿qué debo traer a mi primera cita?", conversation_id=first.conversation_id, actor="priya")
    route = agent.runtime.trace(second.run_id)["route"]
    assert route.startswith("receive_question > resolve_pending_proposal > recall_memories > plan_sources")
    assert second.answer.startswith("Listo, lo recordaré") and len(agent.store.list_memories()) == 1


def test_ambiguous_reply_discards_and_never_approves(agent):
    first = agent.runtime.run(MANCHESTER, actor="priya")
    second = agent.runtime.run("ok gracias", conversation_id=first.conversation_id, actor="priya")
    assert second.memory["decision"]["status"] == "discarded_ambiguous" and agent.store.list_memories() == []


def test_patient_information_is_refused_before_the_graph_and_never_stored(agent):
    result = agent.runtime.run("El paciente Johnson canceló su cita de mañana, apúntalo.", actor="priya")
    trace = agent.runtime.trace(result.run_id)
    assert trace["route"] == "receive_question > refuse_phi" and result.memory["blocked_phi"]
    assert "No puedo procesar ni recordar información que identifique a un paciente" in result.answer
    database = json.dumps(trace) + json.dumps(agent.store.proposals()) + json.dumps(agent.store.audit_trail())
    checkpoints = sqlite3.connect(agent.runtime.tracer.path).execute("select count(*) from checkpoints").fetchone()[0]
    raw = sqlite3.connect(agent.runtime.tracer.path).iterdump()
    assert "Johnson" not in database and checkpoints > 0 and not any("Johnson" in line for line in raw)
    assert agent.store.proposals()[0]["status"] == "blocked_phi"


def test_no_second_proposal_while_one_is_pending(agent, monkeypatch):
    first = agent.runtime.run(MANCHESTER, actor="priya")
    agent.store._conn.execute("update memory_proposals set status = 'pending'")  # simulate a still-open proposal
    monkeypatch.setattr(memory_llm, "classify_decision", lambda p, m: (_ for _ in ()).throw(AssertionError("not called")))
    state = {"answer": "x", "question": MANCHESTER, "conversation_id": first.conversation_id, "memories": []}
    from services.agent import nodes
    assert nodes.self_evaluate(state)["memory_proposal"]["outcome"] == "skipped"
