from json import JSONDecodeError
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from jose import JWTError, jwt
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from tinydb import Query

from ..core.dependencies import CurrentUser, Database
from ..core.security import (
    create_password_reset_token,
    get_settings,
    hash_password,
    verify_password,
)
from ..models.auth import ChangePassword, ForgotPassword, MessageResponse, ResetPassword, User
from ..services.email import reset_expiration_minutes, send_password_reset_email

router = APIRouter(prefix="/auth", tags=["password"])
logger = logging.getLogger(__name__)
NEUTRAL_MESSAGE = MessageResponse(message="Si existe una cuenta con ese correo, recibirás instrucciones.")


def reset_email_task(request: Request, email: str, tasks: BackgroundTasks) -> None:
    with request.app.state.database_lock:
        record = request.app.state.database.table("users").get(Query().email_key == email.lower())
        if record is not None and record.get("email"):
            user = User.model_validate(record)
            token = create_password_reset_token(user, reset_expiration_minutes())
            tasks.add_task(send_password_reset_email, user.email, token)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(request: Request, tasks: BackgroundTasks):
    try:
        payload = ForgotPassword.model_validate(await request.json())
    except (JSONDecodeError, UnicodeDecodeError, ValidationError):
        return NEUTRAL_MESSAGE
    try:
        await run_in_threadpool(reset_email_task, request, payload.email, tasks)
    except (OSError, JSONDecodeError, ValidationError) as exc:
        logger.error("Password recovery lookup failed: %s", type(exc).__name__)
    return NEUTRAL_MESSAGE


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPassword, database: Database):
    invalid = HTTPException(status_code=400, detail="Invalid or expired reset token")
    settings = get_settings()
    try:
        claims = jwt.decode(
            payload.token, settings.secret, algorithms=[settings.algorithm],
            options={"require_exp": True, "require_iat": True, "require_sub": True},
        )
    except JWTError as exc:
        raise invalid from exc
    subject, version = claims.get("sub"), claims.get("token_version")
    if (
        claims.get("purpose") != "password_reset"
        or not isinstance(subject, str) or type(version) is not int
    ):
        raise invalid
    users = database.table("users")
    record = users.get(Query().id == subject)
    if record is None or record.get("token_version", 0) != version:
        raise invalid
    users.update({
        "hashed_password": hash_password(payload.new_password),
        "token_version": version + 1,
    }, Query().id == subject)
    return MessageResponse(message="Password changed")


@router.post("/change-password", response_model=MessageResponse)
def change_password(payload: ChangePassword, database: Database, user: CurrentUser):
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect current password")
    database.table("users").update({
        "hashed_password": hash_password(payload.new_password),
        "token_version": user.token_version + 1,
    }, Query().id == user.id)
    return MessageResponse(message="Password changed")
