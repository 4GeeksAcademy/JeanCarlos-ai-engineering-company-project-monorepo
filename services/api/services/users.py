"""Service layer for users and profiles (TinyDB tables ``users`` and ``profiles``).

Routes translate the domain errors below into HTTP responses; this module has
no FastAPI dependency so it can be reused by scripts such as bootstrap_admin.
"""
from datetime import datetime, timezone
from uuid import uuid4

from tinydb import Query, TinyDB

from ..core.security import hash_password
from ..models.auth import Profile, ProfileUpdate, Role, User, UserCreate, UserUpdate


class UserNotFound(Exception):
    pass


class ProfileNotFound(Exception):
    pass


class EmailAlreadyExists(Exception):
    pass


class LastAdministrator(Exception):
    pass


def _users(database: TinyDB):
    return database.table("users")


def _profiles(database: TinyDB):
    return database.table("profiles")


def _email_key(email: str) -> str:
    return email.strip().lower()


# --- Users ----------------------------------------------------------------

def list_users(database: TinyDB) -> list[User]:
    return [User.model_validate(record) for record in _users(database).all()]


def get_user_by_id(database: TinyDB, user_id: str) -> User | None:
    record = _users(database).get(Query().id == user_id)
    return User.model_validate(record) if record is not None else None


def get_user_by_email(database: TinyDB, email: str) -> User | None:
    record = _users(database).get(Query().email_key == _email_key(email))
    return User.model_validate(record) if record is not None else None


def _ensure_unique_email(database: TinyDB, email: str, user_id: str = "") -> None:
    existing = get_user_by_email(database, email)
    if existing is not None and existing.id != user_id:
        raise EmailAlreadyExists


def _ensure_other_admin(database: TinyDB, user: User) -> None:
    if user.role == Role.admin:
        active_admins = _users(database).search(
            (Query().role == Role.admin.value) & (Query().is_active != False)  # noqa: E712
        )
        if len(active_admins) <= 1:
            raise LastAdministrator


def create_user(database: TinyDB, payload: UserCreate) -> tuple[User, Profile]:
    """Create the credentials record and its linked profile in one operation."""
    _ensure_unique_email(database, payload.email)
    user = User(
        id=str(uuid4()),
        email=payload.email,
        hashed_password=hash_password(payload.password),
        is_active=True,
        role=payload.role,
        created_at=datetime.now(timezone.utc),
    )
    profile = Profile(
        id=str(uuid4()),
        user_id=user.id,
        name=payload.name,
        phone=payload.phone,
        address=payload.address,
    )
    data = user.model_dump(mode="json")
    data["email_key"] = _email_key(user.email)
    _users(database).insert(data)
    _profiles(database).insert(profile.model_dump(mode="json"))
    return user, profile


def update_user(database: TinyDB, user_id: str, payload: UserUpdate) -> User:
    user = get_user_by_id(database, user_id)
    if user is None:
        raise UserNotFound
    updates = payload.model_dump(exclude_unset=True, exclude_none=True, mode="json")
    credentials_changed = False
    if "email" in updates:
        _ensure_unique_email(database, updates["email"], user_id)
        updates["email_key"] = _email_key(updates["email"])
        credentials_changed = updates["email_key"] != _email_key(user.email)
    if "password" in updates:
        updates["hashed_password"] = hash_password(updates.pop("password"))
        credentials_changed = True
    demoting = "role" in updates and updates["role"] != Role.admin.value
    deactivating = updates.get("is_active") is False
    if (demoting or deactivating) and user.is_active:
        _ensure_other_admin(database, user)
    if credentials_changed or deactivating:
        # Revokes every token issued before the change.
        updates["token_version"] = user.token_version + 1
    _users(database).update(updates, Query().id == user_id)
    return get_user_by_id(database, user_id)


def delete_user(database: TinyDB, user_id: str) -> None:
    user = get_user_by_id(database, user_id)
    if user is None:
        raise UserNotFound
    if user.is_active:
        _ensure_other_admin(database, user)
    _profiles(database).remove(Query().user_id == user_id)
    _users(database).remove(Query().id == user_id)


# --- Profiles -------------------------------------------------------------

def get_profile_by_id(database: TinyDB, profile_id: str) -> Profile | None:
    record = _profiles(database).get(Query().id == profile_id)
    return Profile.model_validate(record) if record is not None else None


def get_profile_by_user(database: TinyDB, user_id: str) -> Profile | None:
    record = _profiles(database).get(Query().user_id == user_id)
    return Profile.model_validate(record) if record is not None else None


def list_profiles(database: TinyDB) -> list[Profile]:
    return [Profile.model_validate(record) for record in _profiles(database).all()]


def update_profile_for_user(database: TinyDB, user_id: str, payload: ProfileUpdate) -> Profile:
    """Update the user's profile, creating it if a legacy user has none."""
    updates = payload.model_dump(exclude_unset=True, mode="json")
    profile = get_profile_by_user(database, user_id)
    if profile is None:
        profile = Profile(id=str(uuid4()), user_id=user_id, **updates)
        _profiles(database).insert(profile.model_dump(mode="json"))
        return profile
    _profiles(database).update(updates, Query().id == profile.id)
    return get_profile_by_id(database, profile.id)
