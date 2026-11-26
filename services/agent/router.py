"""HTTP entry point of the agent: only invokes the compiled graph (services.agent.runner).

- POST /agent/query: runs the graph; answers {"answer", "run_id"}. Lives next to the RAG
  endpoint POST /knowledge/query, which keeps working unchanged. Tickets and stock are read
  through the HealthCore MCP server (services/agent/tools.py), never from the APIs directly.
- GET /agent/runs/{run_id}/trace: the persisted trace and checkpoints of a run (admins
  only: it contains retrieved sections and scores, which coordinators never see).
- GET /agent/memory: audit view of the agent memory (admins only).
- GET /agent/guardrails/summary: guardrail activations by guard, action and failure type (admins).
- /agent/chat/*: the same agent over WebSocket, token by token, with interrupt (services/agent/chat).

Errors never expose internals: a rejected question is a 422 with the graph's message, a
failure in any node is a 503 with the run id so the trace can be looked up.
"""

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from services.api.core.dependencies import AuthenticatedUser, require_admin
from services.api.core.rate_limit import enforce

from .runner import AgentRejected, AgentRunError, get_runtime
from .schemas import AgentAnswer, AgentQuestion

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger("api.agent")
RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@router.post("/query", response_model=AgentAnswer,
             responses={422: {"description": "Question rejected"}, 429: {"description": "Rate limited"},
                        503: {"description": "Agent unavailable"}})
def agent_query(body: AgentQuestion, user: AuthenticatedUser, request: Request) -> Any:
    enforce(request, "agent", user.id)  # model-backed: per-user window + global ceiling (NIST Protect)
    try:
        result = get_runtime().run(body.question, conversation_id=body.conversation_id, actor=user.username)
    except AgentRejected as rejected:
        return JSONResponse(status_code=422, content={"detail": str(rejected), "run_id": rejected.run_id})
    except AgentRunError as error:
        logger.error("Agent run %s failed in %s: %s: %s", error.run_id, error.node,
                     type(error.cause).__name__, error.cause)
        return JSONResponse(status_code=503, content={
            "detail": "El asistente no pudo responder en este momento. Inténtalo de nuevo en unos minutos.",
            "run_id": error.run_id})
    except Exception as error:  # runtime could not start (state database, configuration)
        logger.error("Agent unavailable: %s: %s", type(error).__name__, error)
        raise HTTPException(status_code=503, detail="El asistente no está disponible en este momento.") from error
    return AgentAnswer(answer=result.answer, run_id=result.run_id, conversation_id=result.conversation_id,
                       memory=result.memory or {})


@router.get("/runs/{run_id}/trace", dependencies=[Depends(require_admin)],
            responses={404: {"description": "Unknown run"}})
def agent_trace(run_id: Annotated[str, Path(max_length=36)]) -> dict[str, Any]:
    if not RUN_ID.match(run_id):
        raise HTTPException(status_code=404, detail="Corrida desconocida")
    trace = get_runtime().trace(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Corrida desconocida")
    return trace


@router.get("/memory", dependencies=[Depends(require_admin)])
def agent_memory() -> dict[str, Any]:
    """Audit view (admins): active memories, every proposal and how it ended, and the event log.
    Read only: memory is written only by an approved proposal inside a conversation."""
    from .memory.store import get_store

    store = get_store()
    return {"memories": store.list_memories(), "proposals": store.proposals()[-100:],
            "events": store.audit_trail()[-200:]}


@router.get("/guardrails/summary", dependencies=[Depends(require_admin)])
def guardrails_summary(since: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
    """How many times each guardrail blocked, redirected or sanitized (admins). `since` (ISO
    timestamp) scopes it to a test session; `conversation_id` to one conversation."""
    from .harness.events import get_events

    return get_events().summary(since=since, conversation_id=conversation_id)
