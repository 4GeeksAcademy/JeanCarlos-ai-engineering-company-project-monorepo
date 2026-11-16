from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt
from tinydb import Query

from services.api.core import security
from services.api.core.security import create_password_reset_token
from services.api.models.auth import User
from services.api.tests.conftest import PASSWORD, TEST_SECRET


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_login_with_json_returns_a_signed_jwt_carrying_the_user_id(client, user_factory):
    user = user_factory()
    response = client.post("/auth/login", json={"email": user["email"], "password": PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    claims = jwt.decode(body["access_token"], TEST_SECRET, algorithms=["HS256"])
    assert claims["sub"] == user["id"]
    assert claims["exp"] - claims["iat"] == 30 * 60


def test_login_accepts_the_oauth2_form_used_by_swagger(client, user_factory):
    user = user_factory()
    response = client.post("/auth/login", data={"username": user["email"], "password": PASSWORD})
    assert response.status_code == 200
    assert client.get("/auth/me", headers=bearer(response.json()["access_token"])).status_code == 200


def test_login_email_is_case_insensitive(client, user_factory):
    user_factory(email="Alice@Example.org")
    response = client.post("/auth/login", json={"email": "ALICE@example.org", "password": PASSWORD})
    assert response.status_code == 200


@pytest.mark.parametrize("email,password", [
    ("alice@example.org", "wrong-password"),
    ("missing@example.org", PASSWORD),
    ("alice@example.org", "a" * 73),
])
def test_login_rejects_bad_credentials(client, user_factory, email, password):
    user_factory()
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401


def test_login_rejects_inactive_users_and_malformed_bodies(client, user_factory):
    user_factory(email="inactive@example.org", is_active=False)
    assert client.post(
        "/auth/login", json={"email": "inactive@example.org", "password": PASSWORD}
    ).status_code == 401
    assert client.post("/auth/login", json={"email": "not-an-email", "password": "x"}).status_code == 422
    assert client.post("/auth/login", content=b"{", headers={"content-type": "application/json"}).status_code == 422


def test_expiration_is_read_from_the_environment(client, monkeypatch, user_factory):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "5")
    security.get_settings.cache_clear()
    user = user_factory()
    token = client.post("/auth/login", json={"email": user["email"], "password": PASSWORD}).json()["access_token"]
    claims = jwt.decode(token, TEST_SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 5 * 60


def test_signing_key_is_required(monkeypatch):
    monkeypatch.setattr(security, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    security.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            security.get_settings()
    finally:
        security.get_settings.cache_clear()


def test_auth_me_returns_email_role_and_linked_profile(client, user_factory, auth_headers):
    user = user_factory(name="Alice", phone="+1 555 0100", address="1 Main St")
    response = client.get("/auth/me", headers=auth_headers(user))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == user["email"]
    assert body["role"] == "user"
    assert body["profile"]["name"] == "Alice"
    assert body["profile"]["phone"] == "+1 555 0100"
    assert "hashed_password" not in body


def test_auth_me_uses_the_current_role_instead_of_a_stale_token(client, database, user_factory, auth_headers):
    user = user_factory()
    headers = auth_headers(user)
    database.table("users").update({"role": "manager"}, Query().id == user["id"])
    assert client.get("/auth/me", headers=headers).json()["role"] == "manager"


def test_auth_me_rejects_missing_expired_malformed_and_foreign_tokens(client, database, user_factory, auth_headers):
    user = user_factory()
    now = datetime.now(timezone.utc)
    claims = {"sub": user["id"], "iat": now - timedelta(minutes=2), "token_version": 0, "purpose": "access"}
    expired = jwt.encode({**claims, "exp": now - timedelta(minutes=1)}, TEST_SECRET, algorithm="HS256")
    wrong_key = jwt.encode(
        {**claims, "exp": now + timedelta(minutes=5)}, "another-key-with-at-least-32-bytes!!", algorithm="HS256"
    )
    reset = create_password_reset_token(User.model_validate(user), 30)

    assert client.get("/auth/me").status_code == 401
    for token in (expired, wrong_key, reset, "malformed-token", ""):
        response = client.get("/auth/me", headers=bearer(token))
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    headers = auth_headers(user)
    database.table("users").update({"is_active": False}, Query().id == user["id"])
    assert client.get("/auth/me", headers=headers).status_code == 401
    database.table("users").remove(Query().id == user["id"])
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_full_flow_register_login_and_call_a_protected_route(client):
    created = client.post("/users", json={
        "email": "flow@example.org", "password": PASSWORD, "name": "Flow User", "phone": "123",
    })
    assert created.status_code == 201
    token = client.post(
        "/auth/login", json={"email": "flow@example.org", "password": PASSWORD}
    ).json()["access_token"]
    me = client.get("/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["id"] == created.json()["id"]
    assert me.json()["profile"]["name"] == "Flow User"
    assert client.get("/suppliers", headers=bearer(token)).status_code == 200
