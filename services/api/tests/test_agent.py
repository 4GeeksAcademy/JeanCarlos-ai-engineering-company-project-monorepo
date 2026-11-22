"""POST /agent/query and GET /agent/runs/{run_id}/trace: the router only invokes the graph.

The real compiled graph runs here, with an AgentRuntime on a temporary database and the RAG
functions (data/pipelines/rag.py) stubbed.
"""

import pytest

from data.pipelines import rag
from services.agent import runner
from services.agent.runner import AgentRuntime

QUESTION = "¿Cobran cargo por cancelación con 12 horas de anticipación?"
CHUNK = {"source_document": "appointment-policy", "chunk_index": 1, "section": "Política de cancelación",
         "text": "- Cancelar con menos de 24 horas: cargo de 50 USD (o 40 GBP en Reino Unido).", "score": 0.61}


@pytest.fixture(autouse=True)
def agent(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_runtime", AgentRuntime(tmp_path / "agent.sqlite"))
    monkeypatch.setattr(rag, "retrieve", lambda question: [CHUNK] if "cancel" in question.lower() else [])
    monkeypatch.setattr(rag, "generate_answer", lambda question, context: "Sí, 50 USD (o 40 GBP).\nFuente: X")


@pytest.fixture
def headers(user_factory, auth_headers):
    return auth_headers(user_factory(username="priya"))


@pytest.fixture
def admin_headers(user_factory, auth_headers):
    return auth_headers(user_factory(username="marcus", role="admin", email="marcus@example.org"))


def test_answers_with_the_generated_text_and_the_run_id(client, headers):
    response = client.post("/agent/query", json={"question": QUESTION}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "run_id"} and body["answer"] == "Sí, 50 USD (o 40 GBP).\nFuente: X"


def test_empty_question_is_a_clear_422_from_the_graph(client, headers):
    response = client.post("/agent/query", json={"question": "   "}, headers=headers)
    assert response.status_code == 422
    assert response.json()["detail"] == "La pregunta está vacía o es demasiado corta."


def test_node_failure_is_a_503_without_internals(client, headers, monkeypatch):
    def broken(question, context):
        raise ConnectionError("upstream https://llm.internal/v1 refused: secret-token")

    monkeypatch.setattr(rag, "generate_answer", broken)
    response = client.post("/agent/query", json={"question": QUESTION}, headers=headers)

    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"detail", "run_id"}
    assert "Traceback" not in response.text and "secret-token" not in response.text and "llm.internal" not in response.text


def test_trace_is_queryable_by_admins_only(client, headers, admin_headers):
    run_id = client.post("/agent/query", json={"question": QUESTION}, headers=headers).json()["run_id"]

    assert client.get(f"/agent/runs/{run_id}/trace", headers=headers).status_code == 403
    trace = client.get(f"/agent/runs/{run_id}/trace", headers=admin_headers).json()
    assert trace["route"] == "receive_question > retrieve_context > generate_answer"
    assert [step["node"] for step in trace["steps"]] == ["receive_question", "retrieve_context", "generate_answer"]
    assert trace["checkpoints"][-1]["next"] == []
    assert client.get("/agent/runs/00000000-0000-0000-0000-000000000000/trace", headers=admin_headers).status_code == 404


def test_requires_a_session(client):
    assert client.post("/agent/query", json={"question": QUESTION}).status_code == 401


def test_the_rag_endpoint_still_works_next_to_the_agent(client, headers, monkeypatch):
    from services.api.routes import knowledge
    monkeypatch.setattr(knowledge, "query", lambda question: "respuesta RAG")
    assert client.post("/knowledge/query", json={"question": QUESTION}, headers=headers).json() == {"answer": "respuesta RAG"}
