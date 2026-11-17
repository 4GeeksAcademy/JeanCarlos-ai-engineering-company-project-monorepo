"""POST /auth/change-password (routes.auth_password.change_password)."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.api.core.dependencies import get_current_user
from services.api.core.security import create_password_reset_token, verify_password
from services.api.models.auth import ChangePassword, ResetPassword
from services.api.routes.auth_password import change_password, reset_password
from services.api.tests.conftest import PASSWORD, reload_user, token_for

NEW_PASSWORD = "ReplacementPassword123!"


def change(db, user, current=PASSWORD, new=NEW_PASSWORD):
    return change_password(ChangePassword(current_password=current, new_password=new), db, user)


def test_correct_current_password_changes_it_and_revokes_sessions(db, user_account):
    session = token_for(user_account)
    change(db, user_account)
    assert verify_password(NEW_PASSWORD, reload_user(db, user_account).hashed_password)
    with pytest.raises(HTTPException):
        get_current_user(session, db)


def test_changing_the_password_invalidates_pending_reset_links(db, user_account):
    pending_reset = create_password_reset_token(user_account, 30)
    change(db, user_account)
    with pytest.raises(HTTPException) as error:
        reset_password(ResetPassword(token=pending_reset, new_password="AnotherPassword123!"), db)
    assert error.value.status_code == 400


def test_new_password_at_the_bcrypt_limit_is_accepted(db, user_account):
    change(db, user_account, new="é" * 36)
    assert verify_password("é" * 36, reload_user(db, user_account).hashed_password)


def test_wrong_current_password_is_rejected_and_nothing_changes(db, user_account):
    with pytest.raises(HTTPException) as error:
        change(db, user_account, current="wrong-password")
    assert error.value.status_code == 400
    assert verify_password(PASSWORD, reload_user(db, user_account).hashed_password)
    assert reload_user(db, user_account).token_version == 0


@pytest.mark.parametrize("fields", [
    {"current_password": PASSWORD, "new_password": ""},
    {"current_password": PASSWORD, "new_password": "é" * 37},
    {"new_password": NEW_PASSWORD},
])
def test_empty_oversized_or_missing_fields_are_rejected(fields):
    with pytest.raises(ValidationError):
        ChangePassword(**fields)
