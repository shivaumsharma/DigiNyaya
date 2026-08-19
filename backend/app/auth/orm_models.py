"""SQLAlchemy ORM models for authentication.

Note on citext: the original spec called for Postgres' citext extension on
`email` for case-insensitive uniqueness. This app runs on SQLite (see
app/auth/db.py), which has no such type. Case-insensitive uniqueness is done
instead by always storing/comparing a lowercased `email`, with a unique
index on the lowercased column -- callers must lowercase before querying
(see security.py's `normalize_email`).

All timestamps are naive UTC (see db.utcnow's docstring for why).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Always lowercased at write time -- this IS the case-insensitive unique key.
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(10), default="en", server_default="en")
    # Human reviewer for escalated (safety-gate-blocked / Tier 2) cases --
    # never set via the API; bootstrapped via scripts/promote_reviewer.py.
    # No general admin role exists yet, this is deliberately narrow to just
    # the one capability the review workflow needs.
    is_reviewer: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Unicode, no fixed length assumption that would truncate/break Indic scripts.
    full_name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # Shared across every token descended from one login -- reusing an
    # already-rotated-out token revokes every token in its family (theft signal).
    family_id: Mapped[str] = mapped_column(String(36), index=True, default=_uuid)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class LoginAttempt(Base):
    """Not in the original spec's schema -- added to satisfy "rate limit login
    attempts per IP and per identifier separately", which needs somewhere to
    count failed attempts against. Rows are short-lived bookkeeping; nothing
    currently prunes them by age, which is fine at demo/early-stage volume
    but worth a periodic cleanup job before real traffic.
    """

    __tablename__ = "login_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    identifier: Mapped[str] = mapped_column(String(320), index=True)  # lowercased email or E.164 phone
    ip: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ApiCallLog(Base):
    """Generic per-user call log for rate-limiting LLM-cost-bearing endpoints
    (case creation, preliminary review, dispute-type classification) -- see
    app.auth.rate_limit.enforce_call_limit. Not a security control like
    LoginAttempt/OtpCode; these calls just spend real Sarvam API credits, so
    an unbounded client (or a bug that loops a request) needs SOME ceiling.
    One table, disambiguated by `endpoint`, mirroring AuthToken's `purpose`
    pattern rather than a table per endpoint.
    """

    __tablename__ = "api_call_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuthToken(Base):
    """Not in the original spec's schema -- email-verification links (24hr
    expiry) and password-reset links (short-lived) both need a token stored
    server-side (hashed) to be validated later. One table, disambiguated by
    `purpose`, rather than two near-identical tables.
    """

    __tablename__ = "auth_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # "verify_email" | "password_reset"
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
