"""Shared fixtures.

Most suites test business logic directly (route functions, services and
dependencies) against an in-memory TinyDB. Only the route-protection guard and
the cache/timing suites go through FastAPI's TestClient (the ``client`` fixture).
"""
from datetime import datetime, timezone
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from tinydb import TinyDB
from tinydb.storages import MemoryStorage

from services.api.core import security
from services.api.main import app
from services.api.models.auth import Role, User, UserCreate
from services.api.routes import auth_password
from services.api.services import users as user_service

PASSWORD = "ExamplePassword123!"
TEST_SECRET = "test-only-signing-key-with-at-least-32-bytes"
_real_hash = security.hash_password
_hash_cache: dict[str, str] = {}


def cached_hash(password: str) -> str:
    """Real bcrypt hash, computed once per distinct password to keep the suite fast."""
    if password not in _hash_cache:
        _hash_cache[password] = _real_hash(password)
    return _hash_cache[password]


@pytest.fixture(autouse=True)
def jwt_settings(monkeypatch):
    """Deterministic auth configuration that never reads the developer's .env."""
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.delenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
    monkeypatch.setenv("PASSWORD_RESET_EXPIRE_MINUTES", "30")
    monkeypatch.setattr(security, "load_dotenv", lambda *args, **kwargs: False)
    security.get_settings.cache_clear()
    yield
    security.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    for module in (user_service, auth_password):
        monkeypatch.setattr(module, "hash_password", cached_hash)


# --- Logic-level fixtures ----------------------------------------------------------

@pytest.fixture
def db():
    database = TinyDB(storage=MemoryStorage)
    yield database
    database.close()


@pytest.fixture
def create_account(db):
    """Create a user (and its profile) through the real service layer."""
    def create(email="alice@example.org", role=Role.user, password=PASSWORD, **profile) -> User:
        user, _profile = user_service.create_user(
            db, UserCreate(email=email, password=password, role=role, **profile)
        )
        return user

    return create


@pytest.fixture
def admin_account(create_account):
    return create_account(email="admin@example.org", role=Role.admin)


@pytest.fixture
def user_account(create_account):
    return create_account(email="alice@example.org", name="Alice", phone="555-0100")


def reload_user(db, user: User) -> User:
    """Current database state of a user (role, token_version, ...)."""
    return user_service.get_user_by_id(db, user.id)


def token_for(user: User) -> str:
    return security.create_access_token(user)


def fake_request(db):
    """Minimal stand-in for the Request used by the password-recovery task."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=db, database_lock=Lock())))


# --- HTTP-level fixtures (route protection guard, cache and timing suites) -----------

@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(auth_password, "send_password_reset_email", lambda email, token: sent.append((email, token)))
    return sent


@pytest.fixture
def client(tmp_path, monkeypatch, outbox):
    monkeypatch.setenv("SUPPLIERS_DB_PATH", str(tmp_path / "test-db.json"))
    monkeypatch.setenv("RESEND_API_KEY", "test-resend-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "sender@example.org")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://backoffice.example.org")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def database(client):
    return client.app.state.database


@pytest.fixture
def user_factory(database):
    def create_user(email="alice@example.org", role="user", password=PASSWORD, is_active=True, **profile):
        record = {
            "id": str(uuid4()),
            "email": email,
            "email_key": email.lower(),
            "role": role,
            "is_active": is_active,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hashed_password": cached_hash(password),
            "token_version": 0,
        }
        database.table("profiles").insert({
            "id": str(uuid4()), "user_id": record["id"],
            "name": profile.get("name"), "phone": profile.get("phone"), "address": profile.get("address"),
        })
        database.table("users").insert(record)
        return record

    return create_user


@pytest.fixture
def auth_headers():
    def headers(record):
        return {"Authorization": f"Bearer {security.create_access_token(User.model_validate(record))}"}

    return headers


@pytest.fixture
def admin(user_factory):
    return user_factory(email="admin@example.org", role="admin")
