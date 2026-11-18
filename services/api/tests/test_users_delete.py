"""DELETE /users/{id} — account removal with its profile."""
import pytest
from fastapi import HTTPException

from services.api.core.dependencies import get_current_user
from services.api.models.auth import Role
from services.api.routes.users import delete_user
from services.api.services import users as user_service
from services.api.tests.conftest import token_for


def assert_status(code, *args):
    with pytest.raises(HTTPException) as error:
        delete_user(*args)
    assert error.value.status_code == code


def test_admin_deletes_a_user_together_with_profile_and_sessions(db, admin_account, user_account):
    token = token_for(user_account)
    delete_user(user_account.id, db, admin_account)

    assert user_service.get_user_by_id(db, user_account.id) is None
    assert user_service.get_profile_by_user(db, user_account.id) is None
    with pytest.raises(HTTPException):
        get_current_user(token, db)


def test_users_can_delete_their_own_account(db, user_account, admin_account):
    delete_user(user_account.id, db, user_account)
    assert user_service.get_user_by_id(db, user_account.id) is None
    assert user_service.get_user_by_id(db, admin_account.id) is not None


def test_last_active_admin_cannot_be_deleted(db, admin_account):
    assert_status(409, admin_account.id, db, admin_account)
    assert user_service.get_user_by_id(db, admin_account.id) is not None


def test_an_inactive_admin_does_not_count_as_a_remaining_admin(db, admin_account, create_account):
    other = create_account(email="dormant-admin@example.org", role=Role.admin)
    db.table("users").update({"is_active": False}, doc_ids=[db.table("users").all()[-1].doc_id])
    # The only *active* admin would disappear, so the deletion must be refused.
    assert user_service.get_user_by_id(db, other.id).is_active is False
    assert_status(409, admin_account.id, db, admin_account)


def test_users_cannot_delete_other_accounts(db, user_account, admin_account, create_account):
    other = create_account(email="bob@example.org")
    for target in (other.id, admin_account.id):
        assert_status(403, target, db, user_account)
    assert user_service.get_user_by_id(db, other.id) is not None


def test_unknown_user_is_404(db, admin_account):
    assert_status(404, "does-not-exist", db, admin_account)
