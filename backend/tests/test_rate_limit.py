"""Unit tests for app/auth/rate_limit.py, exercised directly against the DB
layer (not through the API) so each limiter is tested in isolation --
hitting /auth/login/phone/verify repeatedly would trip both the OTP's own
attempt cap AND the login identifier rate limit at once, which is correct
end-to-end behaviour but makes a poor unit test for "OTP allows exactly 5
attempts".
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth.orm_models import OtpCode, User
from app.auth.rate_limit import (
    OTP_REQUEST_LIMIT,
    OTP_VERIFY_ATTEMPT_LIMIT,
    enforce_call_limit,
    enforce_otp_request_limit,
    enforce_otp_verify_limit,
)
from app.auth.security import hash_otp, new_otp_salt
from app.auth.db import utcnow
from datetime import timedelta


def _make_otp(db, phone: str) -> OtpCode:
    salt = new_otp_salt()
    otp = OtpCode(
        phone=phone,
        code_hash=hash_otp("123456", salt),
        salt=salt,
        expires_at=utcnow() + timedelta(minutes=5),
    )
    db.add(otp)
    db.commit()
    db.refresh(otp)
    return otp


def test_otp_request_limit_allows_exactly_three_then_blocks(db_session):
    phone = "+919876000001"
    for _ in range(OTP_REQUEST_LIMIT):
        enforce_otp_request_limit(db_session, phone)  # must not raise
        _make_otp(db_session, phone)  # simulate the request actually happening

    with pytest.raises(HTTPException) as exc:
        enforce_otp_request_limit(db_session, phone)
    assert exc.value.status_code == 429


def test_otp_request_limit_is_scoped_per_phone(db_session):
    phone_a, phone_b = "+919876000002", "+919876000003"
    for _ in range(OTP_REQUEST_LIMIT):
        _make_otp(db_session, phone_a)

    with pytest.raises(HTTPException):
        enforce_otp_request_limit(db_session, phone_a)

    # A different phone number has its own independent budget.
    enforce_otp_verify_limit(_make_otp(db_session, phone_b))  # must not raise


def test_otp_verify_attempt_limit_allows_exactly_five_then_blocks(db_session):
    phone = "+919876000004"
    otp = _make_otp(db_session, phone)

    for _ in range(OTP_VERIFY_ATTEMPT_LIMIT):
        enforce_otp_verify_limit(otp)  # must not raise
        otp.attempts += 1
        db_session.commit()

    with pytest.raises(HTTPException) as exc:
        enforce_otp_verify_limit(otp)
    assert exc.value.status_code == 429


def _make_user(db, email: str) -> User:
    user = User(email=email, full_name="Rate Limit Test", password_hash="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class TestEnforceCallLimit:
    """app.auth.rate_limit.enforce_call_limit -- the LLM-cost guard added
    for POST /api/cases, POST /api/cases/{id}/preliminary-review, and
    POST /api/classify-dispute-type (2026-08-06 roadmap item)."""

    def test_allows_exactly_the_limit_then_blocks(self, db_session):
        user = _make_user(db_session, "calllimit1@example.com")
        for _ in range(3):
            enforce_call_limit(db_session, user.id, "create_case", limit=3, window=timedelta(hours=1))

        with pytest.raises(HTTPException) as exc:
            enforce_call_limit(db_session, user.id, "create_case", limit=3, window=timedelta(hours=1))
        assert exc.value.status_code == 429

    def test_scoped_per_endpoint_not_globally_per_user(self, db_session):
        # Exhausting one endpoint's budget must not affect a different
        # endpoint's independent budget for the same user.
        user = _make_user(db_session, "calllimit2@example.com")
        for _ in range(2):
            enforce_call_limit(db_session, user.id, "create_case", limit=2, window=timedelta(hours=1))
        with pytest.raises(HTTPException):
            enforce_call_limit(db_session, user.id, "create_case", limit=2, window=timedelta(hours=1))

        enforce_call_limit(db_session, user.id, "preliminary_review", limit=2, window=timedelta(hours=1))  # must not raise

    def test_scoped_per_user(self, db_session):
        user_a = _make_user(db_session, "calllimit3@example.com")
        user_b = _make_user(db_session, "calllimit4@example.com")
        for _ in range(2):
            enforce_call_limit(db_session, user_a.id, "create_case", limit=2, window=timedelta(hours=1))
        with pytest.raises(HTTPException):
            enforce_call_limit(db_session, user_a.id, "create_case", limit=2, window=timedelta(hours=1))

        enforce_call_limit(db_session, user_b.id, "create_case", limit=2, window=timedelta(hours=1))  # must not raise
