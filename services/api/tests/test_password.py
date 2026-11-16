from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from jose import jwt
from resend.exceptions import ResendError
from tinydb import Query
from tinydb.table import Table

from services.api.core.security import create_password_reset_token, verify_password
from services.api.models.auth import User
from services.api.routes import auth_password
from services.api.services import email as email_service
from services.api.tests.conftest import PASSWORD, TEST_SECRET


NEW_PASSWORD = "ReplacementPassword123!"


def reset_token(user):
    return create_password_reset_token(User.model_validate(user), 30)


def test_forgot_password_sends_a_token_to_the_registered_email(client, user_factory, outbox):
    user_factory(email="alice@example.org")
    response = client.post("/auth/forgot-password", json={"email": "ALICE@example.org"})
    assert response.status_code == 200
    assert len(outbox) == 1
    assert outbox[0][0] == "alice@example.org"
    assert client.post("/auth/reset-password", json={"token": outbox[0][1], "new_password": NEW_PASSWORD}).status_code == 200


def test_forgot_password_is_neutral_for_unknown_and_invalid_emails(client, user_factory, outbox):
    user_factory(email="alice@example.org")
    known = client.post("/auth/forgot-password", json={"email": "alice@example.org"})
    unknown = client.post("/auth/forgot-password", json={"email": "missing@example.org"})
    invalid = client.post("/auth/forgot-password", json={"email": "not-an-email"})
    assert known.status_code == unknown.status_code == invalid.status_code == 200
    assert known.json() == unknown.json() == invalid.json()
    assert len(outbox) == 1


def test_forgot_password_is_neutral_when_storage_fails(client, monkeypatch, caplog, outbox):
    def fail_lookup(*args, **kwargs):
        raise OSError("sensitive-storage-detail")

    monkeypatch.setattr(Table, "get", fail_lookup)
    response = client.post("/auth/forgot-password", json={"email": "alice@example.org"})
    assert response.status_code == 200
    assert not outbox
    assert "Password recovery lookup failed" in caplog.text
    assert "sensitive-storage-detail" not in caplog.text


def test_forgot_password_is_neutral_when_resend_fails(client, user_factory, monkeypatch, caplog):
    user_factory(email="alice@example.org")
    monkeypatch.setattr(auth_password, "send_password_reset_email", email_service.send_password_reset_email)

    def fail_send(*args, **kwargs):
        raise ResendError(500, "server_error", "sensitive-provider-detail", "retry")

    monkeypatch.setattr(email_service.resend.Emails, "send", fail_send)
    response = client.post("/auth/forgot-password", json={"email": "alice@example.org"})
    assert response.status_code == 200
    assert "Password reset email delivery failed" in caplog.text
    assert "sensitive-provider-detail" not in caplog.text


def test_reset_password_changes_the_hash_and_revokes_sessions(client, database, user_factory, auth_headers):
    user = user_factory()
    old_headers = auth_headers(user)
    response = client.post("/auth/reset-password", json={"token": reset_token(user), "new_password": NEW_PASSWORD})
    assert response.status_code == 200
    record = database.table("users").get(Query().id == user["id"])
    assert verify_password(NEW_PASSWORD, record["hashed_password"])
    assert client.get("/auth/me", headers=old_headers).status_code == 401
    assert client.post("/auth/login", json={"email": user["email"], "password": NEW_PASSWORD}).status_code == 200


def test_reset_password_accepts_bcrypt_byte_limit(client, database, user_factory):
    user = user_factory()
    password = "é" * 36
    response = client.post("/auth/reset-password", json={"token": reset_token(user), "new_password": password})
    assert response.status_code == 200
    assert verify_password(password, database.table("users").get(Query().id == user["id"])["hashed_password"])


def test_reset_password_rejects_expired_wrong_purpose_and_tampered_tokens(client, database, user_factory, auth_headers):
    user = user_factory()
    expired = jwt.encode({
        "sub": user["id"], "iat": datetime.now(timezone.utc) - timedelta(minutes=2),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        "token_version": 0, "purpose": "password_reset",
    }, TEST_SECRET, algorithm="HS256")
    access_token = auth_headers(user)["Authorization"].split(" ", 1)[1]
    for token in (expired, access_token, reset_token(user) + "tampered"):
        assert client.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 400
    assert verify_password(PASSWORD, database.table("users").get(Query().id == user["id"])["hashed_password"])


def test_reset_token_is_single_use_even_for_concurrent_requests(client, user_factory):
    user = user_factory()
    token = reset_token(user)
    barrier = Barrier(2, timeout=10)

    def submit_reset():
        barrier.wait()
        return client.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit_reset(), range(2)))
    assert sorted(results) == [200, 400]
    assert client.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 400


def test_change_password_validates_current_password_and_revokes_sessions(client, database, user_factory, auth_headers):
    user = user_factory()
    headers = auth_headers(user)
    response = client.post("/auth/change-password", headers=headers, json={
        "current_password": PASSWORD, "new_password": NEW_PASSWORD,
    })
    assert response.status_code == 200
    assert verify_password(NEW_PASSWORD, database.table("users").get(Query().id == user["id"])["hashed_password"])
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post("/auth/login", json={"email": user["email"], "password": NEW_PASSWORD}).status_code == 200


def test_change_password_invalidates_previously_issued_reset_tokens(client, user_factory, auth_headers):
    user = user_factory()
    old_reset = reset_token(user)
    response = client.post("/auth/change-password", headers=auth_headers(user), json={
        "current_password": PASSWORD, "new_password": "é" * 36,
    })
    assert response.status_code == 200
    assert client.post("/auth/reset-password", json={"token": old_reset, "new_password": NEW_PASSWORD}).status_code == 400


def test_change_password_rejects_wrong_password_and_unauthenticated_requests(client, database, user_factory, auth_headers):
    user = user_factory()
    payload = {"current_password": "incorrect", "new_password": NEW_PASSWORD}
    assert client.post("/auth/change-password", headers=auth_headers(user), json=payload).status_code == 400
    assert client.post("/auth/change-password", json=payload).status_code == 401
    assert verify_password(PASSWORD, database.table("users").get(Query().id == user["id"])["hashed_password"])


@pytest.mark.parametrize("endpoint", ["reset-password", "change-password"])
def test_password_updates_reject_more_than_72_utf8_bytes(client, database, user_factory, auth_headers, endpoint):
    user = user_factory()
    if endpoint == "reset-password":
        payload = {"token": reset_token(user), "new_password": "é" * 37}
        headers = {}
    else:
        payload = {"current_password": PASSWORD, "new_password": "é" * 37}
        headers = auth_headers(user)
    assert client.post(f"/auth/{endpoint}", headers=headers, json=payload).status_code == 422
    assert verify_password(PASSWORD, database.table("users").get(Query().id == user["id"])["hashed_password"])
