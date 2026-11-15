from contextlib import asynccontextmanager
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .database import open_database
from .core.security import get_settings
from .routes import auth, auth_password, profiles, suppliers, users
from .services.email import reset_expiration_minutes


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


app.include_router(auth.router)
app.include_router(auth_password.router)
app.include_router(users.router)
app.include_router(profiles.router)
app.include_router(suppliers.router)
