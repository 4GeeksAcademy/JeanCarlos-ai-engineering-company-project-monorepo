from contextlib import asynccontextmanager
from json import JSONDecodeError
import logging
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .database import get_inventory_engine, open_database
from .core.security import get_settings
from .routes import auth, auth_password, profiles, suppliers, users
from .routers import inventory
from .schemas.errors import ErrorResponse, ValidationErrorResponse, ValidationIssue
from .services.email import reset_expiration_minutes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    reset_expiration_minutes()
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


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, error: RequestValidationError):
    if _request.url.path in {"/login", "/auth/forgot-password", "/auth/reset-password"}:
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
