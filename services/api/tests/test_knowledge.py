"""POST /knowledge/query: the router delegates to data.pipelines.rag.query (stubbed here)."""

import pytest

from services.api.routes import knowledge as knowledge_route


@pytest.fixture
def headers(user_factory, auth_headers):
    return auth_headers(user_factory(username="priya"))


def test_returns_only_the_generated_answer(client, headers, monkeypatch):
    questions = []
    monkeypatch.setattr(knowledge_route, "query", lambda question: questions.append(question) or "Sin cargo.\nFuente: X")

    response = client.post("/knowledge/query", json={"question": "  ¿Cobran por cancelar?  "}, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"answer": "Sin cargo.\nFuente: X"}  # no chunks, scores or ids
    assert questions == ["¿Cobran por cancelar?"]


def test_requires_a_session(client, monkeypatch):
    monkeypatch.setattr(knowledge_route, "query", lambda question: "x")
    assert client.post("/knowledge/query", json={"question": "¿Cobran por cancelar?"}).status_code == 401


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "  a "}, {"question": "x" * 1001},
                                  {"question": "¿hola?", "k": 10}])
def test_rejects_invalid_questions(client, headers, monkeypatch, body):
    monkeypatch.setattr(knowledge_route, "query", lambda question: pytest.fail("query() must not run"))
    assert client.post("/knowledge/query", json=body, headers=headers).status_code == 422


def test_pipeline_failure_is_a_503_not_an_empty_answer(client, headers, monkeypatch):
    def broken(question):
        raise ConnectionError("qdrant down")
    monkeypatch.setattr(knowledge_route, "query", broken)

    response = client.post("/knowledge/query", json={"question": "¿Qué debo traer a mi primera cita?"}, headers=headers)

    assert response.status_code == 503
    assert "answer" not in response.json()
