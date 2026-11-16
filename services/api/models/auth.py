"""User and Profile models stored in TinyDB (never in PostgreSQL/Supabase).

User holds only credentials and account state; display name and contact data
live in Profile, linked one-to-one through ``Profile.user_id``. Other modules
reference ``User.id`` as ``user_uuid``.
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Role(str, Enum):
    admin = "admin"
    manager = "manager"
    user = "user"


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def validate_password_bytes(value: str) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Password must contain valid UTF-8 characters") from exc
    if len(encoded) > 72:
        raise ValueError("Password must not exceed 72 UTF-8 bytes")
    return value


ProfileText = Field(default=None, max_length=200)


# --- Stored records -------------------------------------------------------

class User(BaseModel):
    id: str
    email: EmailStr
    hashed_password: str
    is_active: bool = True
    role: Role = Role.user
    created_at: datetime
    token_version: int = 0


class Profile(BaseModel):
    id: str
    user_id: str
    name: str | None = None
    phone: str | None = None
    address: str | None = None


# --- Users ----------------------------------------------------------------

class UserCreate(InputModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: Role = Role.user
    name: str | None = ProfileText
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = ProfileText

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_bytes(value)


class UserUpdate(InputModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8)
    role: Role | None = None
    is_active: bool | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str | None) -> str | None:
        return validate_password_bytes(value) if value is not None else None


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    role: Role
    is_active: bool
    created_at: datetime


# --- Profiles -------------------------------------------------------------

class ProfileUpdate(InputModel):
    name: str | None = ProfileText
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = ProfileText


class ProfileResponse(BaseModel):
    id: str
    user_id: str
    name: str | None = None
    phone: str | None = None
    address: str | None = None


# --- Auth -----------------------------------------------------------------

class LoginRequest(InputModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CurrentUserResponse(BaseModel):
    id: str
    email: EmailStr
    role: Role
    profile: ProfileResponse | None = None


class MessageResponse(BaseModel):
    message: str


class ForgotPassword(InputModel):
    email: EmailStr


class ResetPassword(InputModel):
    token: str = Field(min_length=1, max_length=4096)
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_bytes(value)


class ChangePassword(InputModel):
    current_password: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_bytes(value)
