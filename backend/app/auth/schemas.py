"""Pydantic request/response schemas for the auth API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from ..language.config import SUPPORTED_LANGUAGE_CODES, normalize_language_code
from .phone import normalize_phone

_OTP_PATTERN = r"^\d{6}$"


def _validate_preferred_language(value: str) -> str:
    normalized = normalize_language_code(value)
    if normalized not in SUPPORTED_LANGUAGE_CODES:
        raise ValueError(f"Unsupported language code: {value!r}")
    return normalized


def _validate_phone(value: str) -> str:
    return normalize_phone(value)


def _validate_password_strength(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters")
    # bcrypt silently ignores/truncates bytes beyond 72 (and some builds
    # raise instead) -- reject upfront with a clear message rather than
    # either surprise-truncating or 500ing at hash time.
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes")
    return value


class SignupEmailRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str = Field(..., min_length=1, max_length=200)
    preferred_language: str

    _validate_password = field_validator("password")(_validate_password_strength)
    _validate_language = field_validator("preferred_language")(_validate_preferred_language)


class LoginEmailRequest(BaseModel):
    email: EmailStr
    password: str


class PhoneStartRequest(BaseModel):
    phone: str

    _validate_phone = field_validator("phone")(_validate_phone)


class SignupPhoneVerifyRequest(BaseModel):
    phone: str
    otp: str = Field(..., pattern=_OTP_PATTERN)
    full_name: str = Field(..., min_length=1, max_length=200)
    preferred_language: str

    _validate_phone = field_validator("phone")(_validate_phone)
    _validate_language = field_validator("preferred_language")(_validate_preferred_language)


class PhoneVerifyRequest(BaseModel):
    phone: str
    otp: str = Field(..., pattern=_OTP_PATTERN)

    _validate_phone = field_validator("phone")(_validate_phone)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

    _validate_password = field_validator("new_password")(_validate_password_strength)


class MessageResponse(BaseModel):
    message: str
    # Populated only outside production (see router.py's _dev_otp_field) --
    # there's no real SMS provider wired up yet (see sms.py's console stub),
    # so this is how a developer/tester sees the code without reading server
    # logs. Always None once DIGINYAYA_ENV=production.
    dev_otp: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserProfile(BaseModel):
    id: str
    email: Optional[str] = None
    phone: Optional[str] = None
    full_name: str
    preferred_language: str
    email_verified: bool
    phone_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}
