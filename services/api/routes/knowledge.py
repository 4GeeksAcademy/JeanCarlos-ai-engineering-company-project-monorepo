"""POST /knowledge/query: staff ask the HealthCore knowledge base (RAG over policies and protocols).

Before the NIST review this route sent the question straight to data.pipelines.rag.query(): no
input screening, retrieved chunks only escaped, no output check, no rate limit. Now every layer the
Compliance assistant uses also protects the RAG (BRIEFING §3: "Indirect prompt injection si un
protocolo desactualizado o manipulado es tratado como instrucción válida"):

1. Input: explicit sanitization (services/agent/harness/sanitize.py), then patient identifiers and
   instruction-override attempts are refused with 422 before any model call (BRIEFING §4, case 1).
2. Rate limit: 10 model-backed requests per minute per user + a global ceiling (core/rate_limit.py).
3. Indirect injection: retrieved chunks pass through the same isolation as the agent — a chunk
   carrying instructions is withheld from the model and logged by source (BRIEFING §4, case 2).
4. Output: the agent's output guard (PHI, prompt leaks, secrets); a failing answer is replaced.

The response carries the model's answer only; chunks and scores never leave the server.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from data.pipelines import rag
from services.agent.harness import isolation, output_guard, prompts, rules
from services.agent.harness.sanitize import InvalidInput, sanitize_input

from ..core.dependencies import AuthenticatedUser
from ..core.rate_limit import enforce
from ..schemas.knowledge import KnowledgeAnswer, KnowledgeQuery

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
logger = logging.getLogger("api.knowledge")
security_logger = logging.getLogger("api.security")

PHI_REFUSAL = ("No puedo procesar información que identifique a un paciente (HIPAA en EE. UU., UK GDPR en Reino Unido). "
               "Reformula la pregunta sin nombres, fechas de nacimiento, números de historia ni diagnósticos de una persona.")
INSTRUCTION_REFUSAL = ("Solo respondo preguntas sobre las políticas y protocolos de HealthCore; no puedo cambiar mis reglas "
                       "ni ignorar las políticas. Reformula tu consulta como una pregunta sobre un procedimiento.")


def query(question: str) -> str:
    """Retrieval → isolation of the retrieved chunks → generation (the RAG pipeline, guarded)."""
    chunks = rag.retrieve(question)
    safe, findings = isolation.isolate_context(chunks)
    for finding in findings:  # the source, never the chunk text
        security_logger.warning("knowledge indirect injection withheld source=%s section=%s rules=%s",
                                finding["source"], finding["section"], ",".join(finding["rules"]))
    return rag.generate_answer(question, safe)


@router.post("/query", response_model=KnowledgeAnswer,
             responses={422: {"description": "Question refused"}, 429: {"description": "Rate limited"},
                        503: {"description": "Knowledge base unavailable"}})
def knowledge_query(body: KnowledgeQuery, user: AuthenticatedUser, request: Request) -> KnowledgeAnswer:
    try:
        question = sanitize_input(body.question, max_chars=1000, min_chars=3).text
    except InvalidInput as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    phi = rules.detect_patient_identifiers(question)
    if phi:
        security_logger.warning("knowledge input refused reason=patient_identifiers categories=%s user=%s",
                                ",".join(phi), user.id)
        raise HTTPException(status_code=422, detail=PHI_REFUSAL)
    override = rules.detect_instruction_override(question)
    if override:
        security_logger.warning("knowledge input refused reason=instruction_override rules=%s user=%s",
                                ",".join(override), user.id)
        raise HTTPException(status_code=422, detail=INSTRUCTION_REFUSAL)
    enforce(request, "knowledge", user.id)  # only requests that reach the model are counted
    try:
        answer = query(question)
    except Exception as error:  # Qdrant, model gateway or configuration: never an empty answer
        logger.error("Knowledge query failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="El asistente no está disponible en este momento.") from error
    check = output_guard.check_output(answer)
    if not check.ok:
        security_logger.warning("knowledge output replaced findings=%s user=%s",
                                ",".join(f"{t}:{c}" for t, c in check.findings), user.id)
        answer = prompts.OUTPUT_FALLBACK
    return KnowledgeAnswer(answer=answer)
