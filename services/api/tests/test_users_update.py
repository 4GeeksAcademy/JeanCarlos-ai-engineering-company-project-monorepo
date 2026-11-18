"""PUT /users/{id} — credential updates (routes.users.update_user)."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.api.core.dependencies import get_current_user
from services.api.core.security import verify_password
from services.api.models.auth import Role, UserUpdate
from services.api.routes.users import update_user
from services.api.tests.conftest import PASSWORD, reload_user, token_for

NEW_PASSWORD = "ReplacementPassword123!"


def put(db, caller, target, **fields):
    return update_user(target.id if hasattr(target, "id") else target, UserUpdate(**fields), db, caller)


def assert_status(code, call, *args, **kwargs):
    with pytest.raises(HTTPException) as error:
        call(*args, **kwargs)
    assert error.value.status_code == code
    return error.value


# --- Happy path ---------------------------------------------------------------------

def test_owner_changes_email_and_password_with_current_password(db, user_account):
    old_token = token_for(user_account)
    put(db, user_account, user_account, email="new@example.org", password=NEW_PASSWORD,
        current_password=PASSWORD)

    stored = reload_user(db, user_account)
    assert stored.email == "new@example.org"
    assert verify_password(NEW_PASSWORD, stored.hashed_password)
    # Changing credentials revokes every token issued before.
    assert_status(401, get_current_user, old_token, db)


def test_admin_changes_another_users_role_and_status(db, admin_account, user_account):
    assert put(db, admin_account, user_account, role=Role.manager).role == Role.manager
    assert put(db, admin_account, user_account, is_active=False).is_active is False


def test_admin_resets_a_password_without_knowing_it(db, admin_account, user_account):
    put(db, admin_account, user_account, password=NEW_PASSWORD)
    assert verify_password(NEW_PASSWORD, reload_user(db, user_account).hashed_password)


# --- Edge cases -------------------------------------------------------------------------

def test_saving_the_same_email_keeps_existing_sessions(db, user_account):
    token = token_for(user_account)
    put(db, user_account, user_account, email="ALICE@example.org", current_password=PASSWORD)
    # Only the case changed: same account key, so the token stays valid.
    assert get_current_user(token, db).id == user_account.id


def test_empty_update_changes_nothing(db, user_account):
    before = db.table("users").all()
    put(db, user_account, user_account)
    assert db.table("users").all() == before


def test_last_active_admin_cannot_be_demoted_or_deactivated(db, admin_account):
    assert_status(409, put, db, admin_account, admin_account, role=Role.user)
    assert_status(409, put, db, admin_account, admin_account, is_active=False)
    assert reload_user(db, admin_account).role == Role.admin


def test_an_admin_can_step_down_when_another_admin_exists(db, admin_account, create_account):
    create_account(email="second-admin@example.org", role=Role.admin)
    assert put(db, admin_account, admin_account, role=Role.user).role == Role.user


def test_deactivating_a_user_revokes_their_tokens(db, admin_account, user_account):
    token = token_for(user_account)
    put(db, admin_account, user_account, is_active=False)
    assert_status(401, get_current_user, token, db)


# --- Failure modes ------------------------------------------------------------------

def test_users_cannot_update_someone_elses_credentials(db, user_account, admin_account, create_account):
    other = create_account(email="bob@example.org")
    for target in (other, admin_account):
        assert_status(403, put, db, user_account, target, email="hijack@example.org")
    assert reload_user(db, other).email == "bob@example.org"


@pytest.mark.parametrize("fields", [{"role": Role.admin}, {"is_active": False}])
def test_users_cannot_change_their_own_role_or_status(db, user_account, fields):
    assert_status(403, put, db, user_account, user_account, **fields)
    assert reload_user(db, user_account).role == Role.user


@pytest.mark.parametrize("fields", [
    {"password": NEW_PASSWORD},
    {"email": "attacker@example.org"},
    {"password": NEW_PASSWORD, "current_password": "wrong-password"},
])
def test_self_service_credential_change_requires_the_current_password(db, user_account, fields):
    # Bug found by the suite: a stolen token alone could take over the account.
    error = assert_status(403, put, db, user_account, user_account, **fields)
    assert "current password" in error.detail.lower()
    stored = reload_user(db, user_account)
    assert stored.email == "alice@example.org"
    assert verify_password(PASSWORD, stored.hashed_password)


def test_email_already_used_by_another_account_is_409(db, admin_account, user_account):
    assert_status(409, put, db, admin_account, user_account, email="ADMIN@example.org")


def test_unknown_user_is_404_for_admins(db, admin_account):
    assert_status(404, put, db, admin_account, "does-not-exist", role=Role.user)


@pytest.mark.parametrize("fields", [{"role": "superuser"}, {"password": "short"}, {"email": "nope"}, {"id": "x"}])
def test_invalid_update_payloads_are_rejected(fields):
    with pytest.raises(ValidationError):
        UserUpdate(**fields)
