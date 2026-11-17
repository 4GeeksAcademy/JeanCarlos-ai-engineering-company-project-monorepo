"""POST /auth/forgot-password and POST /auth/reset-password (routes.auth_password)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from jose import jwt
from pydantic import ValidationError

from services.api.core.dependencies import get_current_user
from services.api.core.security import create_password_reset_token, verify_password
from services.api.models.auth import ResetPassword
from services.api.routes.auth_password import (
    NEUTRAL_MESSAGE, forgot_password, reset_email_task, reset_password,
)
from services.api.tests.conftest import PASSWORD, TEST_SECRET, fake_request, reload_user, token_for

NEW_PASSWORD = "ReplacementPassword123!"


def queued_emails(tasks: BackgroundTasks) -> list[tuple]:
    return [task.args for task in tasks.tasks]


def reset(db, token, password=NEW_PASSWORD):
    return reset_password(ResetPassword(token=token, new_password=password), db)


# --- forgot-password ----------------------------------------------------------------------

def test_registered_email_queues_one_reset_email(db, user_account):
    tasks = BackgroundTasks()
    reset_email_task(fake_request(db), "ALICE@example.org", tasks)
    [(email, token)] = queued_emails(tasks)
    claims = jwt.decode(token, TEST_SECRET, algorithms=["HS256"])
    assert email == "alice@example.org"
    assert (claims["sub"], claims["purpose"]) == (user_account.id, "password_reset")


def test_unknown_email_queues_nothing(db, user_account):
    tasks = BackgroundTasks()
    reset_email_task(fake_request(db), "nobody@example.org", tasks)
    assert queued_emails(tasks) == []


@pytest.mark.parametrize("body", [b'{"email": "nobody@example.org"}', b'{"email": "not-an-email"}', b"{broken"])
def test_response_is_neutral_for_unknown_invalid_or_malformed_input(db, body):
    class Request:
        app = fake_request(db).app

        async def json(self):
            import json
            return json.loads(body)

    # Same answer whatever the input: the endpoint must not reveal which emails exist.
    assert asyncio.run(forgot_password(Request(), BackgroundTasks())) == NEUTRAL_MESSAGE


# --- reset-password -----------------------------------------------------------------------

def test_valid_reset_token_changes_the_password_and_revokes_sessions(db, user_account):
    session = token_for(user_account)
    reset(db, create_password_reset_token(user_account, 30))
    assert verify_password(NEW_PASSWORD, reload_user(db, user_account).hashed_password)
    with pytest.raises(HTTPException):
        get_current_user(session, db)


def test_reset_token_is_single_use(db, user_account):
    token = create_password_reset_token(user_account, 30)
    reset(db, token)
    with pytest.raises(HTTPException) as error:
        reset(db, token, "AnotherPassword123!")
    assert error.value.status_code == 400
    assert verify_password(NEW_PASSWORD, reload_user(db, user_account).hashed_password)


def test_password_at_the_bcrypt_limit_is_accepted(db, user_account):
    reset(db, create_password_reset_token(user_account, 30), "é" * 36)
    assert verify_password("é" * 36, reload_user(db, user_account).hashed_password)


@pytest.mark.parametrize("password", ["", "short", "é" * 37])
def test_invalid_new_passwords_are_rejected(password):
    with pytest.raises(ValidationError):
        ResetPassword(token="t", new_password=password)


def test_expired_tampered_or_access_tokens_are_rejected(db, user_account):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    expired = jwt.encode({"sub": user_account.id, "iat": past - timedelta(minutes=5), "exp": past,
                          "token_version": 0, "purpose": "password_reset"}, TEST_SECRET, algorithm="HS256")
    tampered = create_password_reset_token(user_account, 30) + "x"
    access = token_for(user_account)  # right signature, wrong purpose
    for token in (expired, tampered, access):
        with pytest.raises(HTTPException) as error:
            reset(db, token)
        assert error.value.status_code == 400
    assert verify_password(PASSWORD, reload_user(db, user_account).hashed_password)


def test_reset_token_of_a_deleted_user_is_rejected(db, user_account):
    token = create_password_reset_token(user_account, 30)
    db.table("users").truncate()
    with pytest.raises(HTTPException) as error:
        reset(db, token)
    assert error.value.status_code == 400
