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
    # Invalid values such as NaN cannot themselves be serialized as JSON.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                for item in error.errors()
            ]
        },
    )


@app.exception_handler(OSError)
@app.exception_handler(JSONDecodeError)
async def storage_error(_request: Request, error: Exception):
    logger.error("Storage operation failed: %s", type(error).__name__)
    return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable"})


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, error: Exception):
    logger.error("Unexpected API error: %s", type(error).__name__)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(auth.router)
app.include_router(auth_password.router)
app.include_router(users.router)
app.include_router(profiles.router)
app.include_router(suppliers.router)
app.include_router(inventory.router)
