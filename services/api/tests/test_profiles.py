"""/profiles — GET/PUT /profiles/me and the by-id routes (ownership rules)."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.api.core.dependencies import require_staff
from services.api.models.auth import ProfileUpdate
from services.api.routes.profiles import (
    get_my_profile, get_profile, list_profiles, update_my_profile, update_profile,
)
from services.api.services import users as user_service


def profile_of(db, user):
    return user_service.get_profile_by_user(db, user.id)


def assert_status(code, call, *args):
    with pytest.raises(HTTPException) as error:
        call(*args)
    assert error.value.status_code == code


# --- GET/PUT /profiles/me -------------------------------------------------------------------

def test_get_me_returns_only_the_callers_profile(db, user_account, admin_account):
    assert get_my_profile(db, user_account).user_id == user_account.id
    assert get_my_profile(db, admin_account).user_id == admin_account.id


def test_put_me_updates_only_the_fields_sent(db, user_account):
    update_my_profile(ProfileUpdate(address="2 Side St"), db, user_account)
    stored = profile_of(db, user_account)
    # name/phone were not sent, so they keep their previous values.
    assert (stored.name, stored.phone, stored.address) == ("Alice", "555-0100", "2 Side St")


def test_put_me_can_clear_a_field_explicitly(db, user_account):
    update_my_profile(ProfileUpdate(phone=None), db, user_account)
    assert profile_of(db, user_account).phone is None


def test_legacy_user_without_profile_gets_404_then_put_creates_it(db, user_account):
    db.table("profiles").truncate()
    assert_status(404, get_my_profile, db, user_account)
    update_my_profile(ProfileUpdate(name="New"), db, user_account)
    assert profile_of(db, user_account).name == "New"
    assert len(db.table("profiles")) == 1


@pytest.mark.parametrize("fields", [{"user_id": "someone-else"}, {"email": "x@example.org"}, {"name": "x" * 201}])
def test_profile_payload_cannot_change_owner_or_credentials(fields):
    with pytest.raises(ValidationError):
        ProfileUpdate(**fields)


# --- GET /profiles, GET/PUT /profiles/{id} ----------------------------------------------------

def test_staff_list_all_profiles_but_users_cannot(db, admin_account, user_account):
    assert len(list_profiles(db, require_staff(admin_account))) == 2
    assert_status(403, require_staff, user_account)


def test_owner_and_staff_can_read_a_profile_by_id(db, admin_account, user_account):
    profile_id = profile_of(db, user_account).id
    assert get_profile(profile_id, db, user_account).name == "Alice"
    assert get_profile(profile_id, db, admin_account).name == "Alice"


def test_reading_another_users_profile_is_403(db, user_account, create_account):
    other = create_account(email="bob@example.org", name="Bob")
    assert_status(403, get_profile, profile_of(db, other).id, db, user_account)


def test_only_the_owner_modifies_a_profile_even_admins_get_403(db, admin_account, user_account):
    profile_id = profile_of(db, user_account).id
    assert_status(403, update_profile, profile_id, ProfileUpdate(name="Hacked"), db, admin_account)
    update_profile(profile_id, ProfileUpdate(name="Alice B"), db, user_account)
    assert profile_of(db, user_account).name == "Alice B"


def test_unknown_profile_id_is_404(db, user_account):
    assert_status(404, get_profile, "does-not-exist", db, user_account)
    assert_status(404, update_profile, "does-not-exist", ProfileUpdate(name="x"), db, user_account)
