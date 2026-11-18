"""GET /users and GET /users/{id} — who may read which accounts."""
import pytest
from fastapi import HTTPException

from services.api.core.dependencies import require_staff
from services.api.models.auth import Role
from services.api.routes.users import get_user, list_users


# --- GET /users ---------------------------------------------------------------------

def test_staff_list_every_account(db, admin_account, user_account, create_account):
    manager = create_account(email="manager@example.org", role=Role.manager)
    for caller in (admin_account, manager):
        listed = list_users(db, require_staff(caller))
        assert {user.id for user in listed} == {admin_account.id, user_account.id, manager.id}


def test_listing_never_exposes_password_hashes(db, admin_account):
    assert "hashed_password" not in list_users(db, admin_account)[0].model_dump()


def test_empty_database_lists_nothing(db, admin_account):
    db.table("users").truncate()
    assert list_users(db, admin_account) == []


def test_regular_users_cannot_list_accounts(user_account):
    with pytest.raises(HTTPException) as error:
        require_staff(user_account)
    assert error.value.status_code == 403


# --- GET /users/{id} ----------------------------------------------------------------------

def test_users_can_read_their_own_account(db, user_account):
    assert get_user(user_account.id, db, user_account).email == "alice@example.org"


def test_staff_can_read_any_account(db, admin_account, user_account):
    assert get_user(user_account.id, db, admin_account).id == user_account.id


def test_unknown_id_is_404_for_staff(db, admin_account):
    with pytest.raises(HTTPException) as error:
        get_user("does-not-exist", db, admin_account)
    assert error.value.status_code == 404


def test_reading_another_users_account_is_403_not_404(db, user_account, create_account):
    other = create_account(email="bob@example.org")
    # 403 (not 404) for both existing and missing ids, so ids cannot be probed.
    for target in (other.id, "does-not-exist"):
        with pytest.raises(HTTPException) as error:
            get_user(target, db, user_account)
        assert error.value.status_code == 403
