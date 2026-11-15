from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from tinydb import Query

from ..core.dependencies import CurrentUser, Database
from ..core.security import create_access_token, hash_password, verify_password
from ..models.auth import Token, User, UserResponse

router = APIRouter(tags=["authentication"])


@router.post("/login", response_model=Token)
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], database: Database):
    record = database.table("users").get(Query().username_key == form.username.lower())
    if record is None:
        hash_password("invalid-login-password")
        valid = False
    else:
        valid = verify_password(form.password, record["hashed_password"])
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=create_access_token(User.model_validate(record)))


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser):
    return user
