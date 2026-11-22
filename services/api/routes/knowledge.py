"""POST /knowledge/query: patient coordinators ask the HealthCore knowledge base (RAG, Hito 7).

The router only validates and delegates: retrieval and generation live in
data/pipelines/rag.py (query()). The response carries the model's answer only; retrieved
chunks and similarity scores are logged server-side by the pipeline, never returned.
"""

import logging

from fastapi import APIRouter, HTTPException

from data.pipelines.rag import query

from ..core.dependencies import CurrentUser
from ..schemas.knowledge import KnowledgeAnswer, KnowledgeQuery

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
logger = logging.getLogger("api.knowledge")


@router.post("/query", response_model=KnowledgeAnswer, responses={503: {"description": "Knowledge base unavailable"}})
def knowledge_query(body: KnowledgeQuery, _user: CurrentUser) -> KnowledgeAnswer:
    try:
        answer = query(body.question)
    except Exception as error:  # Qdrant, model gateway or configuration: never an empty answer
        logger.error("Knowledge query failed: %s: %s", type(error).__name__, error)
        raise HTTPException(status_code=503, detail="El asistente no está disponible en este momento.") from error
    return KnowledgeAnswer(answer=answer)
