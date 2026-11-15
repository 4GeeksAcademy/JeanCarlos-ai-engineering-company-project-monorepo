import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from jose import jwt
from passlib.hash import bcrypt

from ..models.auth import User


@dataclass(frozen=True)
class Settings:
    secret: str
    algorithm: str
    access_token_expire_minutes: int


@lru_cache
def get_settings() -> Settings:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    secret = os.environ.get("JWT_SECRET_KEY", "")
    algorithm = os.environ.get("JWT_ALGORITHM", "")
    try:
        expiration = int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", ""))
    except ValueError as exc:
        raise RuntimeError("JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be a positive integer") from exc
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("JWT_SECRET_KEY must contain at least 32 UTF-8 bytes")
    if algorithm not in {"HS256", "HS384", "HS512"}:
        raise RuntimeError("JWT_ALGORITHM must be HS256, HS384, or HS512")
    if expiration <= 0:
        raise RuntimeError("JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be a positive integer")
    return Settings(secret, algorithm, expiration)


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(encoded) > 72:
        return False
    return bcrypt.verify(password, hashed_password)


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user.id,
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
            "token_version": user.token_version,
        },
        settings.secret,
        algorithm=settings.algorithm,
    )
