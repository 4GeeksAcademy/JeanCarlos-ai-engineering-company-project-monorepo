"""GET /auth/me — identity of the token owner plus the linked profile."""
import pytest
from fastapi import HTTPException

from services.api.core.dependencies import get_current_user
from services.api.routes.auth import current_user
from services.api.tests.conftest import token_for


def me(db, user):
    # The route receives the user resolved by get_current_user from the token.
    return current_user(get_current_user(token_for(user), db), db)


def test_returns_email_role_and_linked_profile(db, user_account):
    response = me(db, user_account)
    assert (response.id, response.email, response.role.value) == (user_account.id, "alice@example.org", "user")
    assert (response.profile.name, response.profile.phone) == ("Alice", "555-0100")
    assert not hasattr(response, "hashed_password")


def test_users_without_profile_get_profile_none(db, user_account):
    # Legacy accounts created before profiles existed.
    db.table("profiles").truncate()
    assert me(db, user_account).profile is None


def test_each_user_only_sees_their_own_profile(db, user_account, admin_account):
    assert me(db, admin_account).profile.user_id == admin_account.id
    assert me(db, user_account).profile.user_id == user_account.id


def test_requires_a_valid_token(db):
    with pytest.raises(HTTPException) as error:
        get_current_user("expired-or-garbage", db)
    assert error.value.status_code == 401
