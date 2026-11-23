"""RAG knowledge base, phase 2: retrieval and answer generation.

    query(question) = generate_answer(question, retrieve(question))

- retrieve(): embeds the question with the same embed() used at indexing time, searches
  the `healthcore_knowledge` collection in Qdrant and keeps only the hits whose cosine
  similarity is >= min_score. It returns plain payload dicts (never Qdrant SDK objects) and
  may return fewer than k results, or none.
- generate_answer(): builds the prompt (voice and rules of the HealthCore BRIEFING, Hito 7)
  and calls the generation model (GENERATION_MODEL, a chat model, never the embedding
  model). With no context it still asks the model, instructed to say the knowledge base
  has no information: the answer is always model-generated, never a raw chunk.
- query(): the only function external consumers (the API) call. A later LangGraph agent
  calls retrieve() and generate_answer() as separate steps.

The minimum similarity (RAG_MIN_SCORE, default MIN_SCORE) is tuned and justified in
docs/rag/rag-design.md.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from data.process.rag import COLLECTION, embed, llm_client, qdrant_client, settings

MIN_SCORE = 0.30  # tuned on data/eval/test-queries.json; see docs/rag/rag-design.md
DEFAULT_K = 5
TEMPERATURE = 0.1  # near-deterministic: answers must repeat the documented figures exactly

DOCUMENT_TITLES = {
    "insurance-coverage": "Cobertura de Seguros Aceptados",
    "appointment-policy": "Política de Citas y Cancelaciones",
    "referral-process": "Proceso de Referencia Interna",
    "new-patient-checklist": "Checklist para Pacientes Nuevos",
    "agent-memory": "Memoria del agente",  # approved operational facts (agent only, never the knowledge base)
}
NO_CONTEXT = "NO SE ENCONTRÓ INFORMACIÓN RELEVANTE EN LA BASE DE CONOCIMIENTO."

SYSTEM_PROMPT = """Eres el asistente de mostrador de HealthCore, la red de 12 clínicas ambulatorias \
en Estados Unidos (Texas, Florida, Georgia) y Reino Unido (Londres, Manchester). Ayudas a los \
coordinadores de pacientes del equipo de Priya Nair (Patient Experience) a responder en recepción \
y por teléfono, como lo haría el mejor vendedor de servicios de la clínica: claro, cálido, \
empático y orientado a resolver, sin prometer nada que no esté documentado.

Reglas obligatorias:
1. Responde SOLO con la información del CONTEXTO. Las cifras, tarifas, plazos, porcentajes y \
nombres de seguros deben copiarse exactamente como aparecen; nunca los redondees ni los completes.
2. No añadas pasos, consecuencias, excepciones, matices, recomendaciones ni contactos que no estén \
en el CONTEXTO (por ejemplo "después deberá llamarnos", "depende del médico" o "autoriza la cita"). \
Si algo no está documentado, no lo supongas.
3. Si el CONTEXTO no contiene la respuesta, dilo con claridad ("la base de conocimiento no tiene \
información sobre eso"). No inventes políticas, coberturas, precios ni plazos, aunque los conozcas \
de otras fuentes. Para derivar, usa solo contactos documentados: facturación (Tom Callahan) para \
seguros, coberturas y precios; Marcus Reid (Director de Operaciones Clínicas) para referencias; \
para cualquier otro tema, la supervisión de Patient Experience (Priya Nair). No inventes equipos.
4. Seguros: si preguntan por un seguro o cobertura que no aparece en el CONTEXTO, NO la confirmes: \
indica que debe verificarse con el equipo de facturación (Tom Callahan) antes de confirmar nada.
5. Si la pregunta sobre seguros o cobertura no indica el país, responde distinguiendo \
explícitamente Estados Unidos y Reino Unido.
6. Cargos por no-show o cancelación tardía: a los pacientes de Medicare o Medicaid NUNCA se les \
cobra cargo por no-show; se registra el incidente en su historial. Sigue la política al pie de la letra.
7. Nunca incluyas ni pidas datos reales de pacientes (nombres, diagnósticos, números de historia \
clínica). Si la pregunta los incluye, responde sobre la política en general.
8. Escribe en español, en un tono profesional y cercano, de 2 a 6 frases o una lista corta si hay \
pasos, sin preámbulos ("Claro, aquí tienes…"). Dirígete al coordinador; puede leerle la respuesta al paciente.
9. Los bloques "Memoria del agente" son correcciones operativas aprobadas por el staff (horarios, \
protocolos locales, patrones de incidentes, preferencias). Úsalos para esa clínica o persona, pero no \
reemplazan una política documentada: si contradicen un documento, prevalece el documento y menciona la \
discrepancia.
10. Termina con una línea "Fuente:" con el documento y la sección usados, en el formato \
"Documento — Sección" de los encabezados del CONTEXTO (sin corchetes; varias separadas por "; "). \
Si no usaste ninguno, escribe "Fuente: ninguna"."""

logger = logging.getLogger("rag.query")


def configured_min_score() -> float:
    return float(os.environ.get("RAG_MIN_SCORE", MIN_SCORE))


def _search(vector: list[float], k: int) -> list[Any]:
    """Raw Qdrant search (isolated so tests can replace it with an in-memory stub)."""
    response = qdrant_client().query_points(COLLECTION, query=vector, limit=k, with_payload=True)
    return list(response.points)


def retrieve(query: str, *, k: int = DEFAULT_K, min_score: float | None = None) -> list[dict]:
    """Top-k chunks for `query` with similarity >= min_score, best first, as payload dicts
    (company, source_document, section, language, chunk_index, text, plus `score`)."""
    threshold = configured_min_score() if min_score is None else min_score
    if not query or not query.strip():
        return []
    hits = _search(embed(query), k)
    kept = [{**dict(hit.payload or {}), "score": float(hit.score)} for hit in hits if hit.score >= threshold]
    logger.info("retrieve k=%d min_score=%.2f hits=%s kept=%d", k, threshold,
                [(h.payload.get("source_document"), h.payload.get("chunk_index"), round(h.score, 3))
                 for h in hits if h.payload], len(kept))
    return kept


def format_context(context: list[dict]) -> str:
    if not context:
        return NO_CONTEXT
    blocks = []
    for item in context:
        title = DOCUMENT_TITLES.get(item.get("source_document", ""), item.get("source_document", "?"))
        blocks.append(f"[{title} — {item.get('section', '')}]\n{item.get('text', '').strip()}")
    return "\n\n".join(blocks)


def build_messages(question: str, context: list[dict]) -> list[dict[str, str]]:
    user = (
        f"CONTEXTO:\n---\n{format_context(context)}\n---\n\n"
        f"PREGUNTA DEL COORDINADOR: {question.strip()}"
    )
    if not context:
        user += ("\n\nNo hay contexto relevante: responde que la base de conocimiento no tiene información "
                 "suficiente para esta pregunta, sin inventar datos, y sugiere consultar al equipo responsable.")
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _chat(messages: list[dict[str, str]]) -> str:
    """Call the generation model (chat completions)."""
    response = llm_client().chat.completions.create(
        model=settings().generation_model, messages=messages, temperature=TEMPERATURE,
    )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise RuntimeError("the generation model returned an empty answer")
    return content.strip()


def generate_answer(question: str, context: list[dict]) -> str:
    """Prompt + generation model. `context` is the output of retrieve() (may be empty)."""
    return _chat(build_messages(question, context))


def query(question: str) -> str:
    """Answer a coordinator's question from the knowledge base. Returns only the model's text."""
    return generate_answer(question, retrieve(question))
