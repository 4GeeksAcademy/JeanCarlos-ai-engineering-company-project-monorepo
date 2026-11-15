from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from tinydb import Query

from ..core.dependencies import CurrentUser, Database, require_profile_manager
from ..models.auth import Profile, ProfileCreate, ProfileUpdate, Role, User
from .users import find_user

router = APIRouter(prefix="/profiles", tags=["profiles"])
ProfileManager = Annotated[User, Depends(require_profile_manager)]


def find_profile(database: Database, profile_id: str) -> dict:
    profile = database.table("profiles").get(Query().id == profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def ensure_profile_owner(database: Database, user_id: str, profile_id: str = ""):
    find_user(database, user_id)
    existing = database.table("profiles").get(Query().user_id == user_id)
    if existing is not None and existing["id"] != profile_id:
        raise HTTPException(status_code=409, detail="User already has a profile")


def authorize_profile(user: CurrentUser, profile: dict):
    if user.role == Role.user and profile["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Not permitted to access this profile")


@router.get("", response_model=list[Profile])
def list_profiles(database: Database, user: CurrentUser):
    profiles = database.table("profiles")
    if user.role == Role.user:
        return profiles.search(Query().user_id == user.id)
    return profiles.all()


@router.post("", response_model=Profile, status_code=status.HTTP_201_CREATED)
def create_profile(payload: ProfileCreate, database: Database, _manager: ProfileManager):
    ensure_profile_owner(database, payload.user_id)
    profile = Profile(id=str(uuid4()), user_id=payload.user_id)
    database.table("profiles").insert(profile.model_dump())
    return profile


@router.get("/{profile_id}", response_model=Profile)
def get_profile(profile_id: str, database: Database, user: CurrentUser):
    profile = find_profile(database, profile_id)
    authorize_profile(user, profile)
    return profile


@router.patch("/{profile_id}", response_model=Profile)
def update_profile(
    profile_id: str, payload: ProfileUpdate, database: Database, user: CurrentUser
):
    profile = find_profile(database, profile_id)
    authorize_profile(user, profile)
    if user.role == Role.user and payload.user_id != user.id:
        raise HTTPException(status_code=403, detail="Cannot change profile owner")
    ensure_profile_owner(database, payload.user_id, profile_id)
    database.table("profiles").update(payload.model_dump(), Query().id == profile_id)
    return find_profile(database, profile_id)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(profile_id: str, database: Database, _manager: ProfileManager):
    find_profile(database, profile_id)
    database.table("profiles").remove(Query().id == profile_id)
