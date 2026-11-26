import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from tinydb import Query

from ..core.dependencies import CurrentUser, Database
from ..core.login_throttle import LoginThrottle, fingerprint
from ..core.security import create_access_token, hash_password, verify_password
from ..models.auth import CurrentUserResponse, Token, User, UserResponse

router = APIRouter(tags=["authentication"])
security_logger = logging.getLogger("api.security")


def _throttle(request: Request) -> LoginThrottle:
    throttle = getattr(request.app.state, "login_throttle", None)
    if throttle is None:  # app started without the lifespan (some tests)
        throttle = request.app.state.login_throttle = LoginThrottle()
    return throttle


@router.post("/login", response_model=Token)
def login(request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()], database: Database):
    throttle = _throttle(request)
    wait = throttle.retry_after(form.username)
    if wait is not None:  # brute force: refused before the password is even checked (OWASP A07)
        security_logger.warning("login throttled user=%s retry_after=%ss", fingerprint(form.username), wait)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(wait)},
        )
    record = database.table("users").get(Query().username_key == form.username.lower())
    if record is None:
        hash_password("invalid-login-password")
        valid = False
    else:
        valid = verify_password(form.password, record["hashed_password"])
    if not valid:
        failures = throttle.failed(form.username)
        security_logger.warning("login failed user=%s failures_in_window=%s", fingerprint(form.username), failures)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    throttle.succeeded(form.username)
    return Token(access_token=create_access_token(User.model_validate(record)))


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser):
    return UserResponse.model_validate(user.model_dump())


@router.get("/auth/me", response_model=CurrentUserResponse)
def current_user(user: CurrentUser):
    return CurrentUserResponse.model_validate(user.model_dump())
