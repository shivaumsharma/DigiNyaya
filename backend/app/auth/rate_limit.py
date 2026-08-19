"""DB-backed rate limiting.

No Redis needed at this app's scale: limits are enforced by counting rows in
the same SQLite file the rest of auth already uses, queried per request.
That's correct even across multiple worker processes (unlike an in-memory
counter), since every process reads the same DB file.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import utcnow
from .orm_models import ApiCallLog, LoginAttempt, OtpCode

OTP_REQUEST_LIMIT = 3
OTP_REQUEST_WINDOW = timedelta(minutes=15)
OTP_VERIFY_ATTEMPT_LIMIT = 5

# The spec only gives numeric limits for OTP; these login-attempt limits
# aren't specified, so these are reasonable defaults for a citizen-facing
# login endpoint -- revisit alongside real traffic data.
LOGIN_IDENTIFIER_LIMIT = 5
LOGIN_IP_LIMIT = 20
LOGIN_WINDOW = timedelta(minutes=15)

TOO_MANY_REQUESTS = "Too many attempts. Please try again later."


def enforce_otp_request_limit(db: Session, phone: str) -> None:
    since = utcnow() - OTP_REQUEST_WINDOW
    count = db.scalar(
        select(func.count())
        .select_from(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.created_at >= since)
    )
    if count and count >= OTP_REQUEST_LIMIT:
        raise HTTPException(status_code=429, detail=TOO_MANY_REQUESTS)


def enforce_otp_verify_limit(otp: OtpCode) -> None:
    if otp.attempts >= OTP_VERIFY_ATTEMPT_LIMIT:
        raise HTTPException(status_code=429, detail="Too many attempts. Request a new OTP.")


def enforce_login_rate_limit(db: Session, identifier: str, ip: str) -> None:
    since = utcnow() - LOGIN_WINDOW

    id_count = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.identifier == identifier,
            LoginAttempt.created_at >= since,
            LoginAttempt.success.is_(False),
        )
    )
    if id_count and id_count >= LOGIN_IDENTIFIER_LIMIT:
        raise HTTPException(status_code=429, detail=TOO_MANY_REQUESTS)

    ip_count = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.ip == ip,
            LoginAttempt.created_at >= since,
            LoginAttempt.success.is_(False),
        )
    )
    if ip_count and ip_count >= LOGIN_IP_LIMIT:
        raise HTTPException(status_code=429, detail=TOO_MANY_REQUESTS)


def record_login_attempt(db: Session, identifier: str, ip: str, success: bool) -> None:
    db.add(LoginAttempt(identifier=identifier, ip=ip, success=success))
    db.commit()


# ---------------------------------------------------------------------------
# LLM-cost-bearing endpoints (case creation, preliminary review, dispute-type
# classification): a real user files very few cases and re-runs the
# preliminary review a handful of times while iterating on evidence, so
# these limits are generous relative to normal use -- they exist to bound an
# unbounded client (or a bug that loops a request) from running up real
# Sarvam API cost, not to police ordinary usage. Distinct from the security
# rate limits above (login/OTP), which is why this is a separate function
# with its own log table (ApiCallLog) rather than reusing LoginAttempt.
# ---------------------------------------------------------------------------


def enforce_call_limit(db: Session, user_id: str, endpoint: str, *, limit: int, window: timedelta) -> None:
    since = utcnow() - window
    count = db.scalar(
        select(func.count())
        .select_from(ApiCallLog)
        .where(ApiCallLog.user_id == user_id, ApiCallLog.endpoint == endpoint, ApiCallLog.created_at >= since)
    )
    if count and count >= limit:
        raise HTTPException(status_code=429, detail=TOO_MANY_REQUESTS)
    db.add(ApiCallLog(user_id=user_id, endpoint=endpoint))
    db.commit()
