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
    def create_user(username="alice", role="user", email=None, password=PASSWORD):
        record = {
            "id": str(uuid4()),
            "username": username,
            "username_key": username.lower(),
            "email": email,
            "email_key": email.lower() if email else None,
            "role": role,
            "hashed_password": (
                password_hash if password == PASSWORD else security.hash_password(password)
            ),
            "token_version": 0,
        }
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
    return user_factory(username="admin", role="admin", email="admin@example.org")
