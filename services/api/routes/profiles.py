"""Profile routes under /profiles. Every route requires a valid Bearer token."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..core.dependencies import CurrentUser, Database, forbidden, require_staff
from ..models.auth import Profile, ProfileResponse, ProfileUpdate, Role, User
from ..services import users as user_service

router = APIRouter(prefix="/profiles", tags=["profiles"])
Staff = Annotated[User, Depends(require_staff)]


def profile_response(profile: Profile) -> ProfileResponse:
    return ProfileResponse.model_validate(profile.model_dump())


def profile_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Profile not found")


@router.get("/me", response_model=ProfileResponse)
def get_my_profile(database: Database, user: CurrentUser):
    profile = user_service.get_profile_by_user(database, user.id)
    if profile is None:
        raise profile_not_found()
    return profile_response(profile)


@router.put("/me", response_model=ProfileResponse)
def update_my_profile(payload: ProfileUpdate, database: Database, user: CurrentUser):
    return profile_response(user_service.update_profile_for_user(database, user.id, payload))


@router.get("", response_model=list[ProfileResponse])
def list_profiles(database: Database, _staff: Staff):
    return [profile_response(profile) for profile in user_service.list_profiles(database)]


@router.get("/{profile_id}", response_model=ProfileResponse)
def get_profile(profile_id: str, database: Database, user: CurrentUser):
    profile = user_service.get_profile_by_id(database, profile_id)
    if profile is None:
        raise profile_not_found()
    if profile.user_id != user.id and user.role not in {Role.admin, Role.manager}:
        raise forbidden("Not permitted to access this profile")
    return profile_response(profile)


@router.put("/{profile_id}", response_model=ProfileResponse)
def update_profile(profile_id: str, payload: ProfileUpdate, database: Database, user: CurrentUser):
    """Only the profile owner may modify it."""
    profile = user_service.get_profile_by_id(database, profile_id)
    if profile is None:
        raise profile_not_found()
    if profile.user_id != user.id:
        raise forbidden("Only the profile owner can modify it")
    return profile_response(user_service.update_profile_for_user(database, user.id, payload))
