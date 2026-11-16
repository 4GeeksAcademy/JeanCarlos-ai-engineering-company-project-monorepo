from contextlib import asynccontextmanager
from json import JSONDecodeError
import logging
from threading import Lock
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .database import get_inventory_engine, open_database
from .core.security import get_settings
from .routes import auth, auth_password, profiles, suppliers, telemetry, users
from .routers import incidents, inventory
from .schemas.errors import ErrorResponse, ValidationErrorResponse, ValidationIssue
from .services.email import reset_expiration_minutes

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("api.timing")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    reset_expiration_minutes()
    app.state.telemetry_endpoint = telemetry.telemetry_endpoint()
    database = open_database()
    app.state.database = database
    app.state.database_lock = Lock()
    try:
        yield
    finally:
        database.close()
        if get_inventory_engine.cache_info().currsize:
            get_inventory_engine().dispose()
            get_inventory_engine.cache_clear()


app = FastAPI(title="HealthCore Supplier Directory", lifespan=lifespan)


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
app.include_router(incidents.router)
