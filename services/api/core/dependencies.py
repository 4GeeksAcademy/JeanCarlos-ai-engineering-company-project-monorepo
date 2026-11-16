"""Authentication dependencies shared by every protected route."""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import ValidationError
from tinydb import Query, TinyDB

from ..database import get_database
from ..models.auth import Role, User
from .security import get_settings

# tokenUrl powers the "Authorize" button in /docs (username field = email).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
Database = Annotated[TinyDB, Depends(get_database)]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _user_from_token(token: str, database: TinyDB) -> User:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.secret,
            algorithms=[settings.algorithm],
            options={"require_exp": True, "require_iat": True, "require_sub": True},
        )
    except JWTError as exc:
        raise _unauthorized() from exc
    subject = claims.get("sub")
    version = claims.get("token_version")
    if (
        not isinstance(subject, str)
        or type(version) is not int
        or claims.get("purpose", "access") != "access"
    ):
        raise _unauthorized()
    record = database.table("users").get(Query().id == subject)
    if record is None:
        raise _unauthorized()
    try:
        user = User.model_validate(record)
    except ValidationError as exc:
        raise _unauthorized() from exc
    if user.token_version != version or not user.is_active:
        raise _unauthorized()
    return user


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], database: Database
) -> User:
    """Decode the Bearer token and return the TinyDB user, or raise 401."""
    return _user_from_token(token, database)


def get_optional_user(
    token: Annotated[str | None, Depends(optional_oauth2_scheme)], database: Database
) -> User | None:
    """Like get_current_user, but anonymous requests return None.

    A token that is present but invalid is still rejected with 401.
    """
    if token is None:
        return None
    return _user_from_token(token, database)


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def forbidden(detail: str = "Not permitted to access this resource") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def require_roles(*roles: Role):
    def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise forbidden("Insufficient role for this resource")
        return user

    return checker


require_admin = require_roles(Role.admin)
require_staff = require_roles(Role.admin, Role.manager)
