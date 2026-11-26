from contextlib import asynccontextmanager
from json import JSONDecodeError
import logging
import os
from threading import Lock
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .database import get_inventory_engine, open_database
from .core.security import get_settings
from . import realtime
from .core.login_throttle import LoginThrottle
from .core.rate_limit import ModelRateLimiter
from .routes import auth, auth_password, knowledge, profiles, rfp, suppliers, tasks, telemetry, users
from .routers import incidents, inventory
from services.reporting.router import router as reporting_router
from services.agent.chat.router import router as agent_chat_router
from services.agent.router import router as agent_router
from .schemas.errors import ErrorResponse, ValidationErrorResponse, ValidationIssue
from .services.email import reset_expiration_minutes

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("api.timing")

# Uvicorn only configures its own loggers; without a handler the INFO records of
# "api.*" (timing, telemetry batches) fall back to the root WARNING level and are lost.
_api_logger = logging.getLogger("api")
if not _api_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
    _api_logger.addHandler(_handler)
    _api_logger.setLevel(logging.INFO)
    _api_logger.propagate = False
# RAG pipeline (data/pipelines/rag.py): retrieved chunks and scores stay in the server log only.
_rag_logger = logging.getLogger("rag")
if not _rag_logger.handlers:
    _rag_logger.handlers = list(_api_logger.handlers)
    _rag_logger.setLevel(logging.INFO)
    _rag_logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    reset_expiration_minutes()
    app.state.telemetry_endpoint = telemetry.telemetry_endpoint()
    database = open_database()
    app.state.database = database
    app.state.database_lock = Lock()
    app.state.login_throttle = LoginThrottle()  # brute-force protection of /login (OWASP A07)
    app.state.model_rate_limiter = ModelRateLimiter()  # cost/abuse control of model-backed endpoints (NIST Protect)
    realtime.install_shutdown_hook()  # SSE streams must not block a restart (services/api/realtime.py)
    try:
        yield
    finally:
        realtime.shutdown_streams()
        database.close()
        if get_inventory_engine.cache_info().currsize:
            get_inventory_engine().dispose()
            get_inventory_engine.cache_clear()


def api_docs_enabled() -> bool:
    """Swagger/ReDoc/OpenAPI publish every route and schema without authentication (OWASP A05):
    off unless explicitly enabled for local development (API_DOCS_ENABLED=1)."""
    return os.environ.get("API_DOCS_ENABLED", "").strip().lower() in {"1", "true", "yes"}


_docs = api_docs_enabled()
app = FastAPI(title="HealthCore Supplier Directory", lifespan=lifespan, docs_url="/docs" if _docs else None,
              redoc_url="/redoc" if _docs else None, openapi_url="/openapi.json" if _docs else None)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = perf_counter()
    status_code = 500
    try:
        request_id = str(UUID(request.headers.get("x-request-id", "")))
    except ValueError:
        request_id = str(uuid4())
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        duration_ms = (perf_counter() - start) * 1000
        timing_logger.info(
            "%s %s -> %s | %.1fms request_id=%s",
            request.method,
            route_path,
            status_code,
            duration_ms,
            request_id,
        )


def incident_field(location: tuple | list) -> str:
    parts = [str(part) for part in location if part not in {"body", "query", "path"}]
    return parts[0] if parts else "body"


@app.exception_handler(incidents.IncidentInputError)
async def incident_input_error(_request: Request, error: incidents.IncidentInputError):
    return JSONResponse(
        status_code=400,
        content={"detail": error.detail, "errors": [item.as_dict() for item in error.errors]},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, error: RequestValidationError):
    if _request.url.path.startswith("/api/incidents"):
        # The incident manager reports invalid input as 400 with the offending field.
        errors = []
        for item in error.errors():
            field = incident_field(item.get("loc", ()))
            if item.get("type") == "json_invalid":
                message = "The request body is not valid JSON."
            elif item.get("type") == "missing":
                message = "This field is required."
            elif field == "incident_id":
                message = "The incident id must be a positive whole number."
            else:
                message = "This value is not valid."
            errors.append({"field": field, "message": message})
        return JSONResponse(
            status_code=400,
            content={"detail": "Some fields are missing or invalid.", "errors": errors},
        )
    if _request.url.path in {"/login", "/auth/forgot-password", "/auth/reset-password", "/telemetry/events"}:
        issues = [ValidationIssue(loc=["body"], msg="Invalid request", type="value_error")]
    else:
        issues = [
            ValidationIssue(loc=list(item["loc"]), msg=item["msg"], type=item["type"])
            for item in error.errors()
        ]
    return JSONResponse(
        status_code=422,
        content=ValidationErrorResponse(detail=issues).model_dump(mode="json"),
    )


@app.exception_handler(OSError)
@app.exception_handler(JSONDecodeError)
async def storage_error(_request: Request, error: Exception):
    logger.error("Storage operation failed: %s", type(error).__name__)
    return JSONResponse(
        status_code=503,
        content=ErrorResponse(detail="Database temporarily unavailable").model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, error: Exception):
    logger.error("Unexpected API error: %s", type(error).__name__)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(detail="Internal server error").model_dump(mode="json"),
    )


app.include_router(auth.router)
app.include_router(auth_password.router)
app.include_router(users.router)
app.include_router(profiles.router)
app.include_router(suppliers.router)
app.include_router(inventory.router)
app.include_router(telemetry.router)
app.include_router(reporting_router)
app.include_router(tasks.router)
app.include_router(incidents.router)
app.include_router(knowledge.router)
app.include_router(agent_router)
app.include_router(agent_chat_router, prefix="/agent")  # WebSocket chat of the Compliance assistant
app.include_router(rfp.router)
