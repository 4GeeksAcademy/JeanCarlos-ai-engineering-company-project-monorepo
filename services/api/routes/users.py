"""User credentials CRUD under /users.

POST /users is public (self-registration). Every other route requires a valid
Bearer token; acting on another user's account returns 403.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ..core.dependencies import CurrentUser, Database, OptionalUser, forbidden, require_staff
from ..core.security import verify_password
from ..models.auth import Role, User, UserCreate, UserResponse, UserUpdate
from ..services import users as user_service

router = APIRouter(prefix="/users", tags=["users"])
Staff = Annotated[User, Depends(require_staff)]


def user_response(user: User) -> UserResponse:
    return UserResponse.model_validate(user.model_dump())


def get_or_404(database: Database, user_id: str) -> User:
    user = user_service.get_user_by_id(database, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def email_conflict() -> HTTPException:
    return HTTPException(status_code=409, detail="Email already exists")


def last_admin_conflict() -> HTTPException:
    return HTTPException(status_code=409, detail="Cannot remove the last administrator")


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, database: Database, caller: OptionalUser):
    """Register a user and its linked profile. Only an admin may choose a role."""
    if payload.role != Role.user and (caller is None or caller.role != Role.admin):
        raise forbidden("Only administrators can assign elevated roles")
    try:
        user, _profile = user_service.create_user(database, payload)
    except user_service.EmailAlreadyExists:
        raise email_conflict() from None
    return user_response(user)


@router.get("", response_model=list[UserResponse])
def list_users(database: Database, _staff: Staff):
    return [user_response(user) for user in user_service.list_users(database)]


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: str, database: Database, caller: CurrentUser):
    if caller.id != user_id and caller.role not in {Role.admin, Role.manager}:
        raise forbidden()
    return user_response(get_or_404(database, user_id))


@router.put("/{user_id}", response_model=UserResponse)
def update_user(user_id: str, payload: UserUpdate, database: Database, caller: CurrentUser):
    """Update credentials. Only the account owner or an admin; role/is_active admin only."""
    is_admin = caller.role == Role.admin
    if caller.id != user_id and not is_admin:
        raise forbidden()
    if not is_admin and (payload.role is not None or payload.is_active is not None):
        raise forbidden("Only administrators can change role or account status")
    target = get_or_404(database, user_id)
    email_changes = payload.email is not None and payload.email.lower() != target.email.lower()
    if caller.id == user_id and (payload.password is not None or email_changes):
        # A stolen token alone must not be enough to take over the account.
        if payload.current_password is None or not verify_password(
            payload.current_password, target.hashed_password
        ):
            raise forbidden("Confirm your current password to change your email or password")
    try:
        user = user_service.update_user(database, user_id, payload)
    except user_service.EmailAlreadyExists:
        raise email_conflict() from None
    except user_service.LastAdministrator:
        raise last_admin_conflict() from None
    return user_response(user)


@router.delete("/{user_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, database: Database, caller: CurrentUser):
    """Delete a user and its linked profile. Only the account owner or an admin."""
    if caller.id != user_id and caller.role != Role.admin:
        raise forbidden()
    get_or_404(database, user_id)
    try:
        user_service.delete_user(database, user_id)
    except user_service.LastAdministrator:
        raise last_admin_conflict() from None
