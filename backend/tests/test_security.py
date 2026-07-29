"""Unit tests for app/auth/security.py: password hashing and OTP hashing."""

from __future__ import annotations

from app.auth.security import (
    generate_otp,
    hash_otp,
    hash_password,
    hash_token,
    new_otp_salt,
    normalize_email,
    verify_otp,
    verify_password,
)


def test_password_hash_is_not_plaintext():
    h = hash_password("correcthorsebatterystaple")
    assert h != "correcthorsebatterystaple"
    assert h.startswith("$2b$")  # bcrypt hash prefix


def test_password_verify_roundtrip():
    h = hash_password("correcthorsebatterystaple")
    assert verify_password("correcthorsebatterystaple", h) is True
    assert verify_password("wrongpassword", h) is False


def test_password_verify_handles_none_and_garbage_hash():
    assert verify_password("anything", None) is False
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_password_hash_uses_cost_factor_12():
    h = hash_password("correcthorsebatterystaple")
    # bcrypt hash format: $2b$<cost>$<22-char-salt><31-char-hash>
    cost = int(h.split("$")[2])
    assert cost == 12


def test_generate_otp_is_six_digits():
    for _ in range(20):
        code = generate_otp()
        assert len(code) == 6
        assert code.isdigit()


def test_otp_hash_roundtrip():
    code = generate_otp()
    salt = new_otp_salt()
    h = hash_otp(code, salt)
    assert verify_otp(code, salt, h) is True
    assert verify_otp("000000" if code != "000000" else "111111", salt, h) is False


def test_otp_hash_is_salted_differently_each_time():
    code = "123456"
    salt1, salt2 = new_otp_salt(), new_otp_salt()
    assert salt1 != salt2
    assert hash_otp(code, salt1) != hash_otp(code, salt2)


def test_normalize_email_lowercases_and_strips():
    assert normalize_email("  Test.User@Example.COM  ") == "test.user@example.com"


def test_hash_token_is_deterministic_and_not_reversible_looking():
    token = "some-opaque-refresh-token-value"
    h1 = hash_token(token)
    h2 = hash_token(token)
    assert h1 == h2
    assert h1 != token
    assert len(h1) == 64  # sha256 hex digest
