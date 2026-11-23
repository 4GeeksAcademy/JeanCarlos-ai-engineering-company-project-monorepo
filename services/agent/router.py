"""HTTP entry point of the agent: only invokes the compiled graph (services.agent.runner).

- POST /agent/query: runs the graph; answers {"answer", "run_id"}. Lives next to the RAG
  endpoint POST /knowledge/query, which keeps working unchanged. The caller's Bearer token
  is handed to the read-only tools (incident manager, inventory) for this run only.
- GET /agent/runs/{run_id}/trace: the persisted trace and checkpoints of a run (admins
  only: it contains retrieved sections and scores, which coordinators never see).

Errors never expose internals: a rejected question is a 422 with the graph's message, a
failure in any node is a 503 with the run id so the trace can be looked up.
"""

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse

from services.api.core.dependencies import AuthenticatedUser, oauth2_scheme, require_admin

from .runner import AgentRejected, AgentRunError, get_runtime
from .schemas import AgentAnswer, AgentQuestion

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger("api.agent")
RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@router.post("/query", response_model=AgentAnswer,
             responses={422: {"description": "Question rejected"}, 503: {"description": "Agent unavailable"}})
def agent_query(body: AgentQuestion, _user: AuthenticatedUser,
                token: Annotated[str, Depends(oauth2_scheme)]) -> Any:
    try:
        # The tools read the incident and inventory APIs with the coordinator's own token.
        result = get_runtime().run(body.question, credentials=token)
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
    return AgentAnswer(answer=result.answer, run_id=result.run_id)


@router.get("/runs/{run_id}/trace", dependencies=[Depends(require_admin)],
            responses={404: {"description": "Unknown run"}})
def agent_trace(run_id: Annotated[str, Path(max_length=36)]) -> dict[str, Any]:
    if not RUN_ID.match(run_id):
        raise HTTPException(status_code=404, detail="Corrida desconocida")
    trace = get_runtime().trace(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Corrida desconocida")
    return trace
