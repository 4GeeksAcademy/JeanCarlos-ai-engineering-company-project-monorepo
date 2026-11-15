from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from tinydb import Query, TinyDB

from ..database import get_database
from ..models.auth import Role, User
from .security import get_settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")
Database = Annotated[TinyDB, Depends(get_database)]


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], database: Database
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.secret,
            algorithms=[settings.algorithm],
            options={"require_exp": True, "require_iat": True, "require_sub": True},
        )
    except JWTError as exc:
        raise unauthorized from exc
    subject = claims.get("sub")
    version = claims.get("token_version")
    if not isinstance(subject, str) or type(version) is not int:
        raise unauthorized
    record = database.table("users").get(Query().id == subject)
    if record is None:
        raise unauthorized
    user = User.model_validate(record)
    if user.token_version != version:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != Role.admin:
        raise HTTPException(status_code=403, detail="Administrator role required")
    return user


def require_profile_manager(user: CurrentUser) -> User:
    if user.role not in {Role.admin, Role.manager}:
        raise HTTPException(status_code=403, detail="Manager or administrator role required")
    return user
