from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class UserCreate(InputModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8)
    role: Role

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_bytes(value)


class UserUpdate(InputModel):
    username: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    password: str | None = Field(default=None, min_length=8)
    role: Role | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str | None) -> str | None:
        return validate_password_bytes(value) if value is not None else None


class User(BaseModel):
    id: str
    username: str
    role: Role
    hashed_password: str
    token_version: int = 0


class UserResponse(BaseModel):
    id: str
    username: str
    role: Role


class ProfileCreate(InputModel):
    user_id: str


class ProfileUpdate(InputModel):
    user_id: str


class Profile(BaseModel):
    id: str
    user_id: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
