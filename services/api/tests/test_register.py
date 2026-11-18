"""POST /users — public registration (routes.users.create_user)."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.api.core.security import verify_password
from services.api.models.auth import Role, UserCreate
from services.api.routes.users import create_user
from services.api.services import users as user_service
from services.api.tests.conftest import PASSWORD


def register(db, caller=None, **fields):
    payload = UserCreate(**{"email": "new@example.org", "password": PASSWORD, **fields})
    return create_user(payload, db, caller)


# --- Happy path ---------------------------------------------------------------------

def test_registration_stores_only_a_bcrypt_hash_and_defaults_to_user_role(db):
    response = register(db)

    stored = user_service.get_user_by_id(db, response.id)
    assert response.role == Role.user
    assert response.is_active is True
    assert stored.hashed_password != PASSWORD
    assert stored.hashed_password.startswith("$2")  # bcrypt marker
    assert verify_password(PASSWORD, stored.hashed_password)
    # The plain password must not appear anywhere in the stored document.
    assert PASSWORD not in str(db.table("users").all())


def test_registration_creates_the_linked_profile_with_contact_data(db):
    response = register(db, name="Pat", phone="+34 600 000 000", address="Calle 1")

    profile = user_service.get_profile_by_user(db, response.id)
    assert (profile.name, profile.phone, profile.address) == ("Pat", "+34 600 000 000", "Calle 1")
    # Contact data lives in Profile only, never in the User record.
    assert not {"name", "phone", "address"} & set(db.table("users").all()[0])


def test_admin_can_register_elevated_roles(db, admin_account):
    assert register(db, admin_account, role=Role.manager).role == Role.manager


# --- Edge cases -------------------------------------------------------------------------

def test_registration_without_profile_fields_still_creates_an_empty_profile(db):
    response = register(db)
    profile = user_service.get_profile_by_user(db, response.id)
    assert profile is not None and profile.name is None


def test_duplicate_email_is_rejected_case_insensitively(db, user_account):
    with pytest.raises(HTTPException) as error:
        register(db, email=user_account.email.upper())
    assert error.value.status_code == 409
    assert len(db.table("users")) == 1


def test_password_at_the_72_byte_bcrypt_limit_is_accepted(db):
    # 36 two-byte characters = exactly 72 UTF-8 bytes, the bcrypt maximum.
    response = register(db, password="é" * 36)
    stored = user_service.get_user_by_id(db, response.id)
    assert verify_password("é" * 36, stored.hashed_password)


@pytest.mark.parametrize("password", ["", "short", "é" * 37])
def test_empty_short_or_oversized_passwords_are_rejected(password):
    # 37 two-byte characters exceed bcrypt's 72-byte input and would be silently truncated.
    with pytest.raises(ValidationError):
        UserCreate(email="new@example.org", password=password)


# --- Failure modes ------------------------------------------------------------------

@pytest.mark.parametrize("role", [Role.admin, Role.manager])
def test_anonymous_or_regular_callers_cannot_self_assign_elevated_roles(db, user_account, role):
    for caller in (None, user_account):
        with pytest.raises(HTTPException) as error:
            register(db, caller, email=f"{role.value}@example.org", role=role)
        assert error.value.status_code == 403
    assert user_service.get_user_by_email(db, f"{role.value}@example.org") is None


@pytest.mark.parametrize("fields", [
    {"email": "not-an-email"},
    {"email": ""},
    {"role": "superuser"},
    {"username": "legacy-field"},
])
def test_malformed_registration_payloads_are_rejected(fields):
    with pytest.raises(ValidationError):
        UserCreate(**{"email": "new@example.org", "password": PASSWORD, **fields})
