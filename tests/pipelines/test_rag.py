"""RAG knowledge base: retrieve() and query() with stubs (no live Qdrant or LLM), plus the
chunking and the idempotency of setup() against Qdrant's in-memory mode.

    python -m pytest tests/pipelines/test_rag.py
"""

import hashlib
import math
from types import SimpleNamespace

import pytest

from data.pipelines import rag as pipeline
from data.process import rag as process

CHUNK_A = {"company": "healthcore", "source_document": "appointment-policy", "section": "Política de cancelación",
           "language": "es", "chunk_index": 1, "text": "- Cancelar con más de 24 horas de anticipación: sin cargo."}
CHUNK_B = {**CHUNK_A, "section": "Reserva de citas", "chunk_index": 0, "text": "- Cita de rutina: 3 a 5 días."}
CHUNK_C = {**CHUNK_A, "source_document": "referral-process", "section": "Proceso de Referencia Interna",
           "chunk_index": 0, "text": "1. El médico crea la referencia."}


def hit(score, payload):
    """Shape of a Qdrant ScoredPoint (only what retrieve() reads)."""
    return SimpleNamespace(id="x", score=score, payload=payload, vector=None)


@pytest.fixture
def stub_search(monkeypatch):
    """In-memory stand-in for the Qdrant search; records what it was asked."""
    calls = []

    def install(hits):
        def search(vector, k):
            calls.append({"vector": vector, "k": k})
            return sorted(hits, key=lambda h: -h.score)[:k]
        monkeypatch.setattr(pipeline, "_search", search)
        monkeypatch.setattr(pipeline, "embed", lambda text: [0.1, 0.2, 0.3])
        return calls
    return install


# ---------------------------------------------------------------- retrieve()


def test_retrieve_excludes_results_below_min_score(stub_search):
    stub_search([hit(0.82, CHUNK_A), hit(0.41, CHUNK_B), hit(0.12, CHUNK_C)])

    result = pipeline.retrieve("¿cobran por cancelar?", k=5, min_score=0.40)

    assert [r["section"] for r in result] == ["Política de cancelación", "Reserva de citas"]
    assert all(r["score"] >= 0.40 for r in result)


def test_retrieve_may_return_fewer_than_k_or_nothing(stub_search):
    stub_search([hit(0.9, CHUNK_A), hit(0.3, CHUNK_B), hit(0.2, CHUNK_C)])
    assert len(pipeline.retrieve("pregunta", k=3, min_score=0.5)) == 1  # k=3 asked, 1 survives
    assert pipeline.retrieve("¿receta de paella?", k=3, min_score=0.95) == []


def test_retrieve_returns_plain_payload_dicts_not_sdk_objects(stub_search):
    calls = stub_search([hit(0.9, CHUNK_A)])
    [result] = pipeline.retrieve("pregunta", k=4, min_score=0.0)

    assert type(result) is dict
    assert {"company", "source_document", "section", "language", "chunk_index", "text"} <= set(result)
    assert result["text"] == CHUNK_A["text"]
    assert calls == [{"vector": [0.1, 0.2, 0.3], "k": 4}]  # the query was embedded, k passed through


def test_retrieve_uses_the_configured_threshold_by_default(stub_search, monkeypatch):
    stub_search([hit(0.5, CHUNK_A)])
    monkeypatch.setenv("RAG_MIN_SCORE", "0.6")
    assert pipeline.retrieve("pregunta") == []
    monkeypatch.setenv("RAG_MIN_SCORE", "0.4")
    assert len(pipeline.retrieve("pregunta")) == 1


def test_retrieve_ignores_blank_questions(stub_search):
    calls = stub_search([hit(0.9, CHUNK_A)])
    assert pipeline.retrieve("   ") == [] and calls == []


# ---------------------------------------------------------------- query() / generate_answer()


@pytest.fixture
def fake_llm(monkeypatch):
    prompts = []

    def chat(messages):
        prompts.append(messages)
        return "Si cancela con más de 24 horas no hay cargo.\nFuente: Política de Citas y Cancelaciones — Política de cancelación"
    monkeypatch.setattr(pipeline, "_chat", chat)
    return prompts


def test_query_returns_the_model_output(monkeypatch, fake_llm):
    monkeypatch.setattr(pipeline, "retrieve", lambda question: [CHUNK_A])

    answer = pipeline.query("¿Cobran por cancelar con 2 días?")

    assert answer.startswith("Si cancela con más de 24 horas no hay cargo.")
    assert len(fake_llm) == 1


def test_query_never_returns_raw_chunk_text(monkeypatch, fake_llm):
    monkeypatch.setattr(pipeline, "retrieve", lambda question: [CHUNK_A, CHUNK_B])
    answer = pipeline.query("¿Cobran por cancelar?")
    assert CHUNK_A["text"] not in answer and CHUNK_B["text"] not in answer
    # the chunks went to the model, inside the prompt
    prompt = fake_llm[0][1]["content"]
    assert CHUNK_A["text"] in prompt and "Política de Citas y Cancelaciones — Política de cancelación" in prompt


def test_query_is_retrieve_plus_generate_answer(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "retrieve", lambda question: calls.append(("retrieve", question)) or [CHUNK_A])
    monkeypatch.setattr(pipeline, "generate_answer",
                        lambda question, context: calls.append(("generate", question, context)) or "respuesta")
    assert pipeline.query("pregunta") == "respuesta"
    assert calls == [("retrieve", "pregunta"), ("generate", "pregunta", [CHUNK_A])]


def test_empty_context_still_goes_through_the_model_with_an_honest_instruction(monkeypatch, fake_llm):
    monkeypatch.setattr(pipeline, "retrieve", lambda question: [])
    pipeline.query("¿Cuál es el horario del gimnasio?")
    system, user = fake_llm[0][0]["content"], fake_llm[0][1]["content"]
    assert pipeline.NO_CONTEXT in user and "no tiene información suficiente" in user
    assert "No inventes" in system


def test_prompt_carries_the_briefing_voice_and_rules():
    system = pipeline.build_messages("¿?", [CHUNK_A])[0]["content"]
    assert "coordinadores de pacientes" in system and "mejor vendedor de servicios" in system
    assert "Tom Callahan" in system  # unlisted insurance → billing
    assert "Estados Unidos y Reino Unido" in system  # country not specified
    assert "Medicare o Medicaid NUNCA" in system  # no-show fee rule
    assert "No inventes equipos" in system
    assert "SOLO con la información del CONTEXTO" in system


def test_generation_errors_are_not_turned_into_answers(monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve", lambda question: [CHUNK_A])

    def broken(messages):
        raise RuntimeError("gateway down")
    monkeypatch.setattr(pipeline, "_chat", broken)
    with pytest.raises(RuntimeError):
        pipeline.query("pregunta")


# ---------------------------------------------------------------- chunking and setup()


def test_every_briefing_document_gives_at_least_3_self_contained_chunks():
    chunks = process.load_chunks()
    per_document = {}
    for chunk in chunks:
        per_document.setdefault(chunk.source_document, []).append(chunk)
        assert chunk.text.rstrip().endswith((".", ")")), f"cut chunk: {chunk.text[-40:]!r}"
        assert chunk.section and chunk.payload()["company"] == "healthcore"
    assert set(per_document) == {"insurance-coverage", "appointment-policy", "referral-process", "new-patient-checklist"}
    assert all(len(items) >= 3 for items in per_document.values())


def test_the_no_show_fee_and_its_medicare_exception_stay_in_one_chunk():
    [chunk] = [c for c in process.load_chunks() if "cargo de 50" in c.text]
    assert "Medicare o Medicaid" in chunk.text and chunk.section == "Política de cancelación"


def test_chunks_cover_the_whole_source_text():
    for file_name in process.SOURCE_DOCUMENTS:
        source = (process.KNOWLEDGE_BASE_DIR / file_name).read_text(encoding="utf-8")
        words = [w for w in source.split() if not w.startswith("#")]
        chunked = " ".join(c.text for c in process.load_chunks() if c.source_document == process.SOURCE_DOCUMENTS[file_name])
        title_words = source.splitlines()[0].lstrip("# ").split()
        assert " ".join(chunked.split()) == " ".join(words[len(title_words):])


def fake_embed(text):
    """Deterministic 16-dim vector, so setup() can run without the embedding model."""
    digest = hashlib.sha256(process.normalize_text(text).encode()).digest()
    vector = [b - 127.5 for b in digest[:16]]
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector]


@pytest.mark.filterwarnings("ignore:Payload indexes have no effect")
def test_setup_is_idempotent_and_stores_the_briefing_payload(monkeypatch):
    from qdrant_client import QdrantClient

    client = QdrantClient(":memory:")
    monkeypatch.setattr(process, "embed", fake_embed)

    first = process.setup(client=client)
    second = process.setup(client=client)

    assert first["points"] == second["points"] == len(process.load_chunks())
    assert second["stale_points_deleted"] == 0
    records, _ = client.scroll(process.COLLECTION, limit=100, with_payload=True)
    payload = records[0].payload
    assert set(payload) == {"company", "source_document", "section", "language", "chunk_index", "text"}
    assert payload["company"] == "healthcore" and payload["language"] == "es"


@pytest.mark.filterwarnings("ignore:Payload indexes have no effect")
def test_setup_deletes_points_of_chunks_that_no_longer_exist(monkeypatch, tmp_path):
    from qdrant_client import QdrantClient

    client = QdrantClient(":memory:")
    monkeypatch.setattr(process, "embed", fake_embed)
    process.setup(client=client)
    for file_name in process.SOURCE_DOCUMENTS:  # same corpus, one document shortened
        text = (process.KNOWLEDGE_BASE_DIR / file_name).read_text(encoding="utf-8")
        if file_name == "healthcore-referral-process.es.md":
            text = text.split("\n\nSi un paciente")[0] + "\n"
        (tmp_path / file_name).write_text(text, encoding="utf-8")

    summary = process.setup(tmp_path, client=client)

    assert summary["stale_points_deleted"] == 2 and summary["points"] == len(process.load_chunks(tmp_path))


def test_embedding_and_generation_models_must_differ(monkeypatch):
    process.settings.cache_clear()
    for name, value in {"LLM_API_URL": "https://gateway/v1", "LLM_API_KEY": "k",
                        "EMBEDDING_MODEL": "same", "GENERATION_MODEL": "same"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    try:
        with pytest.raises(process.RagConfigError, match="must be different"):
            process.settings()
    finally:
        process.settings.cache_clear()
