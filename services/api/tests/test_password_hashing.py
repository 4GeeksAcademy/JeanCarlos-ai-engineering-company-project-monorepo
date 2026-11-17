"""Password hashing helpers (core.security) — bcrypt through libpass."""
import pytest

from services.api.core import security

_hash = security.hash_password  # the real function, not the cached test helper


def test_hash_is_salted_bcrypt_and_verifies():
    first, second = _hash("Secret123!"), _hash("Secret123!")
    assert first.startswith("$2") and first != "Secret123!"
    # Random salt: the same password never produces the same hash.
    assert first != second
    assert security.verify_password("Secret123!", first)
    assert security.verify_password("Secret123!", second)


def test_unicode_password_at_72_bytes_verifies():
    hashed = _hash("é" * 36)
    assert security.verify_password("é" * 36, hashed)


@pytest.mark.parametrize("candidate", ["secret123!", "Secret123", "Secret123! ", ""])
def test_wrong_passwords_do_not_verify(candidate):
    assert not security.verify_password(candidate, _hash("Secret123!"))


def test_inputs_beyond_72_bytes_never_verify_instead_of_being_truncated():
    hashed = _hash("a" * 72)
    # Plain bcrypt would ignore everything after byte 72 and accept this.
    assert not security.verify_password("a" * 72 + "extra", hashed)


def test_invalid_utf8_input_is_rejected_without_raising():
    assert security.verify_password("\ud800", _hash("Secret123!")) is False
