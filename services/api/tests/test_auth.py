from datetime import datetime, timedelta, timezone

import pytest
import jwt
from tinydb import Query

from services.api.core.security import create_password_reset_token
from services.api.models.auth import User
from services.api.tests.conftest import PASSWORD, TEST_SECRET


def test_login_accepts_valid_credentials(client, user_factory):
    user = user_factory()
    response = client.post("/login", data={"username": user["username"], "password": PASSWORD})

    assert response.status_code == 200
    token = response.json()["access_token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"] == user["id"]


def test_login_username_is_case_insensitive(client, user_factory):
    user_factory(username="Alice")
    response = client.post("/login", data={"username": "ALICE", "password": PASSWORD})
    assert response.status_code == 200


@pytest.mark.parametrize("username,password", [
    ("alice", "wrong-password"),
    ("missing", PASSWORD),
    ("alice", "a" * 73),
])
def test_login_rejects_bad_credentials(client, user_factory, username, password):
    user_factory()
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 401


def test_me_returns_the_authenticated_user(client, user_factory, auth_headers):
    user = user_factory()
    response = client.get("/me", headers=auth_headers(user))

    assert response.status_code == 200
    assert response.json()["id"] == user["id"]
    assert response.json()["role"] == "user"
    assert "hashed_password" not in response.json()


def test_me_uses_the_current_role_instead_of_a_stale_token(client, database, user_factory, auth_headers):
    user = user_factory()
    headers = auth_headers(user)
    database.table("users").update({"role": "manager"}, Query().id == user["id"])

    assert client.get("/me", headers=headers).json()["role"] == "manager"


def test_me_rejects_expired_reset_and_deleted_user_tokens(client, database, user_factory, auth_headers):
    user = user_factory()
    expired = jwt.encode({
        "sub": user["id"], "iat": datetime.now(timezone.utc) - timedelta(minutes=2),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        "token_version": 0, "purpose": "access",
    }, TEST_SECRET, algorithm="HS256")
    reset = create_password_reset_token(User.model_validate(user), 30)
    for token in (expired, reset, "invalid-token"):
        assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    headers = auth_headers(user)
    database.table("users").remove(Query().id == user["id"])
    assert client.get("/me", headers=headers).status_code == 401


# ---------------------------------------------------------------- OWASP A07: brute-force protection


def test_login_is_throttled_after_repeated_failures(client, user_factory):
    """Before the fix the 50th wrong password still got a 401 and the next right one a token."""
    user_factory(username="tom")
    for _ in range(5):
        assert client.post("/login", data={"username": "tom", "password": "wrong-password-1"}).status_code == 401
    blocked = client.post("/login", data={"username": "TOM", "password": PASSWORD})  # even the right password
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    assert "access_token" not in blocked.text
    assert client.post("/login", data={"username": "unknown-user", "password": "x" * 9}).status_code == 401  # others unaffected


def test_a_successful_login_clears_the_failures_and_the_window_expires(client, user_factory):
    from services.api.core.login_throttle import LoginThrottle

    user_factory(username="claire")
    for _ in range(4):
        client.post("/login", data={"username": "claire", "password": "wrong-password-1"})
    assert client.post("/login", data={"username": "claire", "password": PASSWORD}).status_code == 200
    assert client.post("/login", data={"username": "claire", "password": "wrong-password-1"}).status_code == 401  # count restarted

    now = [1000.0]
    throttle = LoginThrottle(max_failures=2, window_seconds=60, clock=lambda: now[0])
    throttle.failed("x"), throttle.failed("x")
    assert throttle.retry_after("x") == 61
    now[0] += 61
    assert throttle.retry_after("x") is None


def test_api_docs_are_not_published_by_default(client):
    """OWASP A05: /docs, /redoc and /openapi.json listed every route without authentication."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
