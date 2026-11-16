from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.core import security
from services.api.main import app
from services.api.models.auth import User
from services.api.routes import auth_password


PASSWORD = "ExamplePassword123!"
TEST_SECRET = "test-only-signing-key-with-at-least-32-bytes"


@pytest.fixture(scope="session")
def password_hash():
    return security.hash_password(PASSWORD)


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    def capture_email(email, token):
        sent.append((email, token))

    monkeypatch.setattr(auth_password, "send_password_reset_email", capture_email)
    return sent


@pytest.fixture
def client(tmp_path, monkeypatch, outbox):
    monkeypatch.setenv("SUPPLIERS_DB_PATH", str(tmp_path / "test-db.json"))
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("PASSWORD_RESET_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("RESEND_API_KEY", "test-resend-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "sender@example.org")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://backoffice.example.org")
    monkeypatch.setattr(security, "load_dotenv", lambda *args, **kwargs: False)
    security.get_settings.cache_clear()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        security.get_settings.cache_clear()


@pytest.fixture
def database(client):
    return client.app.state.database


@pytest.fixture
def user_factory(database, password_hash):
    def create_user(email="alice@example.org", role="user", password=PASSWORD, is_active=True, **profile):
        record = {
            "id": str(uuid4()),
            "email": email,
            "email_key": email.lower(),
            "role": role,
            "is_active": is_active,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hashed_password": (
                password_hash if password == PASSWORD else security.hash_password(password)
            ),
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
        token = security.create_access_token(User.model_validate(record))
        return {"Authorization": f"Bearer {token}"}

    return headers


@pytest.fixture
def admin(user_factory):
    return user_factory(email="admin@example.org", role="admin")
