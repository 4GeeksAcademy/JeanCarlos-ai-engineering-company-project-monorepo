"""Access tokens: generation (core.security) and validation (core.dependencies).

The expiry tests are the regression guard for AUTH-088: a refactor broke token
expiration and no test caught it.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt

from services.api.core import security
from services.api.core.dependencies import get_current_user, get_optional_user
from services.api.models.auth import User
from services.api.tests.conftest import TEST_SECRET, token_for


def claims_of(token: str) -> dict:
    return jwt.decode(token, TEST_SECRET, algorithms=["HS256"])


def forge(user: User, *, secret=TEST_SECRET, algorithm="HS256", **overrides) -> str:
    """Hand-made token so each claim can be broken independently."""
    now = datetime.now(timezone.utc)
    claims = {"sub": user.id, "iat": now, "exp": now + timedelta(minutes=5),
              "token_version": user.token_version, "purpose": "access", **overrides}
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, secret, algorithm=algorithm)


def assert_rejected(token, db):
    with pytest.raises(HTTPException) as error:
        get_current_user(token, db)
    assert error.value.status_code == 401
    # RFC 6750: clients must be told to authenticate with a Bearer token.
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


# --- Token generation ---------------------------------------------------------------

def test_token_carries_user_id_version_purpose_and_issued_at(user_account):
    claims = claims_of(token_for(user_account))
    assert claims["sub"] == user_account.id
    assert claims["token_version"] == 0
    assert claims["purpose"] == "access"
    assert abs(claims["iat"] - datetime.now(timezone.utc).timestamp()) < 5


@pytest.mark.parametrize("minutes", [1, 30, 1440])
def test_token_lifetime_matches_access_token_expire_minutes(monkeypatch, user_account, minutes):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", str(minutes))
    security.get_settings.cache_clear()
    claims = claims_of(token_for(user_account))
    # Regression guard: the window must be exactly the configured one, in seconds.
    assert claims["exp"] - claims["iat"] == minutes * 60


def test_legacy_expiry_variable_is_still_honoured(monkeypatch, user_account):
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "7")
    security.get_settings.cache_clear()
    claims = claims_of(token_for(user_account))
    assert claims["exp"] - claims["iat"] == 7 * 60


def test_unset_expiry_defaults_to_30_minutes(monkeypatch):
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES")
    security.get_settings.cache_clear()
    assert security.get_settings().access_token_expire_minutes == 30


@pytest.mark.parametrize("value", ["", "  "])
def test_blank_expiry_in_env_file_falls_back_to_the_default(monkeypatch, value):
    # A copied .env.example with an empty value must not take the API down.
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", value)
    security.get_settings.cache_clear()
    assert security.get_settings().access_token_expire_minutes == 30


@pytest.mark.parametrize("variable,value,message", [
    ("ACCESS_TOKEN_EXPIRE_MINUTES", "0", "ACCESS_TOKEN_EXPIRE_MINUTES"),
    ("ACCESS_TOKEN_EXPIRE_MINUTES", "-5", "ACCESS_TOKEN_EXPIRE_MINUTES"),
    ("ACCESS_TOKEN_EXPIRE_MINUTES", "thirty", "ACCESS_TOKEN_EXPIRE_MINUTES"),
    ("JWT_SECRET_KEY", "too-short", "JWT_SECRET_KEY"),
    ("JWT_SECRET_KEY", "", "JWT_SECRET_KEY"),
    ("JWT_ALGORITHM", "none", "JWT_ALGORITHM"),
])
def test_unsafe_or_invalid_configuration_fails_fast(monkeypatch, variable, value, message):
    monkeypatch.setenv(variable, value)
    security.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match=message):
        security.get_settings()


# --- get_current_user: happy path ------------------------------------------------------

def test_valid_token_resolves_to_the_stored_user(db, user_account):
    assert get_current_user(token_for(user_account), db).id == user_account.id


def test_role_is_read_from_the_database_not_from_the_token(db, user_account):
    token = token_for(user_account)
    db.table("users").update({"role": "manager"})
    assert get_current_user(token, db).role.value == "manager"


def test_token_still_valid_shortly_before_expiry(db, user_account):
    assert get_current_user(forge(user_account, exp=datetime.now(timezone.utc) + timedelta(seconds=30)), db)


# --- get_current_user: failure modes ---------------------------------------------------

def test_expired_token_is_rejected(db, user_account):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert_rejected(forge(user_account, iat=past - timedelta(minutes=30), exp=past), db)


def test_token_issued_with_an_expiry_in_the_past_is_rejected(monkeypatch, db, user_account):
    # Simulates the original regression: a token whose real lifetime is already over.
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1")
    security.get_settings.cache_clear()

    class Past(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) - timedelta(minutes=2)

    monkeypatch.setattr(security, "datetime", Past)
    assert_rejected(token_for(user_account), db)


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "Bearer abc"])
def test_malformed_tokens_are_rejected(db, token):
    assert_rejected(token, db)


def test_signature_from_another_key_is_rejected(db, user_account):
    assert_rejected(forge(user_account, secret="another-signing-key-of-32-bytes-at-least"), db)


def test_tampered_payload_is_rejected(db, user_account, admin_account):
    header, _payload, signature = token_for(user_account).split(".")
    # Re-use alice's signature with the admin's claims: the signature no longer matches.
    _, admin_payload, _ = token_for(admin_account).split(".")
    assert_rejected(f"{header}.{admin_payload}.{signature}", db)


@pytest.mark.parametrize("missing", ["sub", "exp", "iat", "token_version"])
def test_tokens_missing_required_claims_are_rejected(db, user_account, missing):
    assert_rejected(forge(user_account, **{missing: None}), db)


def test_password_reset_tokens_cannot_be_used_as_access_tokens(db, user_account):
    assert_rejected(security.create_password_reset_token(user_account, 30), db)


def test_tokens_are_revoked_when_credentials_change(db, user_account):
    token = token_for(user_account)
    db.table("users").update({"token_version": 1})  # what a password/email change does
    assert_rejected(token, db)


def test_tokens_of_deleted_or_deactivated_users_are_rejected(db, user_account):
    token = token_for(user_account)
    db.table("users").update({"is_active": False})
    assert_rejected(token, db)
    db.table("users").truncate()
    assert_rejected(token, db)


# --- get_optional_user (used by public registration) ------------------------------------

def test_optional_user_is_none_without_token_and_resolves_with_one(db, user_account):
    assert get_optional_user(None, db) is None
    assert get_optional_user(token_for(user_account), db).id == user_account.id


def test_optional_user_still_rejects_a_present_but_invalid_token(db):
    with pytest.raises(HTTPException) as error:
        get_optional_user("not-a-jwt", db)
    assert error.value.status_code == 401
