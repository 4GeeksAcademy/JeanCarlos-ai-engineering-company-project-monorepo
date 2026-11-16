"""Authentication routes under /auth (stateless JWT, no sessions or cookies)."""
from json import JSONDecodeError
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from ..core.dependencies import CurrentUser, Database
from ..core.security import create_access_token, hash_password, verify_password
from ..models.auth import CurrentUserResponse, LoginRequest, ProfileResponse, Token
from ..services import users as user_service

router = APIRouter(prefix="/auth", tags=["authentication"])

# Burned on unknown emails so the response time does not reveal which exist.
_DUMMY_HASH = hash_password("timing-equalisation-password")

LOGIN_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": LoginRequest.model_json_schema()},
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string", "description": "Account email"},
                        "password": {"type": "string", "format": "password"},
                    },
                }
            },
        },
    }
}


async def login_credentials(request: Request) -> LoginRequest:
    """Accept JSON {email, password} or the OAuth2 form used by /docs (username = email)."""
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("application/json"):
            data = await request.json()
        else:
            form = await request.form()
            data = {"email": form.get("username"), "password": form.get("password")}
        return LoginRequest.model_validate(data)
    except (JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError, ValueError):
        raise RequestValidationError([{"loc": ("body",), "msg": "Invalid request", "type": "value_error"}])


@router.post("/login", response_model=Token, openapi_extra=LOGIN_BODY)
def login(credentials: Annotated[LoginRequest, Depends(login_credentials)], database: Database):
    user = user_service.get_user_by_email(database, credentials.email)
    if user is None:
        verify_password(credentials.password, _DUMMY_HASH)
        valid = False
    else:
        valid = verify_password(credentials.password, user.hashed_password) and user.is_active
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=create_access_token(user))


@router.get("/me", response_model=CurrentUserResponse)
def current_user(user: CurrentUser, database: Database):
    profile = user_service.get_profile_by_user(database, user.id)
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        profile=ProfileResponse.model_validate(profile.model_dump()) if profile else None,
    )
