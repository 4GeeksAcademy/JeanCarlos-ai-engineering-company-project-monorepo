from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from tinydb import Query

from ..core.dependencies import Database, require_admin
from ..core.security import hash_password
from ..models.auth import Role, User, UserCreate, UserResponse, UserUpdate

router = APIRouter(
    prefix="/users", tags=["users"], dependencies=[Depends(require_admin)]
)


def find_user(database: Database, user_id: str) -> dict:
    record = database.table("users").get(Query().id == user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User not found")
    return record


def ensure_unique_username(database: Database, username: str, user_id: str = ""):
    record = database.table("users").get(Query().username_key == username.lower())
    if record is not None and record["id"] != user_id:
        raise HTTPException(status_code=409, detail="Username already exists")


def ensure_unique_email(database: Database, email: str | None, user_id: str = ""):
    if email is None:
        return
    record = database.table("users").get(Query().email_key == email.lower())
    if record is not None and record["id"] != user_id:
        raise HTTPException(status_code=409, detail="Email already exists")


def ensure_other_admin(database: Database, user: dict):
    if user["role"] == Role.admin.value:
        admins = database.table("users").search(Query().role == Role.admin.value)
        if len(admins) <= 1:
            raise HTTPException(status_code=409, detail="Cannot remove the last administrator")


@router.get("", response_model=list[UserResponse])
def list_users(database: Database):
    return database.table("users").all()


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, database: Database):
    ensure_unique_username(database, payload.username)
    ensure_unique_email(database, payload.email)
    user = User(
        id=str(uuid4()), username=payload.username, email=payload.email, role=payload.role,
        hashed_password=hash_password(payload.password),
    )
    data = user.model_dump(mode="json")
    data["username_key"] = payload.username.lower()
    data["email_key"] = payload.email.lower() if payload.email else None
    database.table("users").insert(data)
    return user


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: str, database: Database):
    return find_user(database, user_id)


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(user_id: str, payload: UserUpdate, database: Database):
    record = find_user(database, user_id)
    updates = payload.model_dump(exclude_unset=True, mode="json")
    updates = {key: value for key, value in updates.items() if value is not None or key == "email"}
    if "username" in updates:
        ensure_unique_username(database, updates["username"], user_id)
        updates["username_key"] = updates["username"].lower()
    if "role" in updates and updates["role"] != Role.admin.value:
        ensure_other_admin(database, record)
    credentials_changed = False
    if "email" in updates:
        ensure_unique_email(database, updates["email"], user_id)
        updates["email_key"] = updates["email"].lower() if updates["email"] else None
        credentials_changed = updates["email_key"] != record.get("email_key")
    if "password" in updates:
        updates["hashed_password"] = hash_password(updates.pop("password"))
        credentials_changed = True
    if credentials_changed:
        updates["token_version"] = record.get("token_version", 0) + 1
    database.table("users").update(updates, Query().id == user_id)
    return find_user(database, user_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, database: Database):
    record = find_user(database, user_id)
    ensure_other_admin(database, record)
    database.table("profiles").remove(Query().user_id == user_id)
    database.table("users").remove(Query().id == user_id)
