"""POST /auth/login — credential check and token issuance (routes.auth)."""
import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from jose import jwt
from pydantic import ValidationError
from starlette.requests import Request

from services.api.models.auth import LoginRequest
from services.api.routes.auth import login, login_credentials
from services.api.services import users as user_service
from services.api.tests.conftest import PASSWORD, TEST_SECRET


def do_login(db, email, password):
    return login(LoginRequest(email=email, password=password), db)


def parse(body: bytes, content_type: str) -> LoginRequest:
    """Run the login body parser on a raw request (JSON or OAuth2 form)."""
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/auth/login",
             "headers": [(b"content-type", content_type.encode())]}
    return asyncio.run(login_credentials(Request(scope, receive)))


# --- Happy path ---------------------------------------------------------------------

def test_valid_credentials_return_a_signed_token_for_that_user(db, user_account):
    token = do_login(db, user_account.email, PASSWORD)

    claims = jwt.decode(token.access_token, TEST_SECRET, algorithms=["HS256"])
    assert token.token_type == "bearer"
    assert claims["sub"] == user_account.id  # TinyDB id, referenced elsewhere as user_uuid
    assert claims["purpose"] == "access"


def test_json_and_swagger_form_bodies_are_both_accepted():
    from_json = parse(json.dumps({"email": "a@example.org", "password": "pw"}).encode(), "application/json")
    # The /docs "Authorize" button sends the email in the OAuth2 "username" field.
    from_form = parse(b"username=a%40example.org&password=pw", "application/x-www-form-urlencoded")
    assert from_json == from_form == LoginRequest(email="a@example.org", password="pw")


# --- Edge cases -------------------------------------------------------------------------

def test_email_lookup_is_case_insensitive(db, create_account):
    create_account(email="Alice@Example.org")
    assert do_login(db, "ALICE@example.ORG", PASSWORD).access_token


def test_password_is_case_and_whitespace_sensitive(db, user_account):
    for variant in (PASSWORD.lower(), f" {PASSWORD}", f"{PASSWORD} "):
        with pytest.raises(HTTPException) as error:
            do_login(db, user_account.email, variant)
        assert error.value.status_code == 401


def test_deactivated_accounts_cannot_log_in_even_with_the_right_password(db, user_account):
    db.table("users").update({"is_active": False})
    with pytest.raises(HTTPException) as error:
        do_login(db, user_account.email, PASSWORD)
    assert error.value.status_code == 401


@pytest.mark.parametrize("body", [b"", b"{}", b'{"email": "a@example.org"}', b'{"email": "", "password": "x"}'])
def test_empty_or_incomplete_bodies_are_rejected_before_any_lookup(body):
    with pytest.raises(RequestValidationError):
        parse(body, "application/json")


# --- Failure modes ------------------------------------------------------------------

@pytest.mark.parametrize("email,password", [
    ("alice@example.org", "wrong-password"),
    ("missing@example.org", PASSWORD),
    ("alice@example.org", "a" * 73),  # over bcrypt's 72 bytes: never truncated into a match
])
def test_invalid_credentials_get_the_same_generic_401(db, user_account, email, password):
    with pytest.raises(HTTPException) as error:
        do_login(db, email, password)
    # Same message for unknown email and wrong password: no account enumeration.
    assert error.value.status_code == 401
    assert error.value.detail == "Incorrect email or password"


def test_malformed_json_is_rejected():
    with pytest.raises(RequestValidationError):
        parse(b"{not json", "application/json")


def test_login_does_not_change_the_stored_user(db, user_account):
    before = db.table("users").all()
    do_login(db, user_account.email, PASSWORD)
    assert db.table("users").all() == before
    assert user_service.get_user_by_id(db, user_account.id).token_version == 0


def test_login_payload_rejects_extra_fields():
    with pytest.raises(ValidationError):
        LoginRequest(email="a@example.org", password="x", role="admin")
