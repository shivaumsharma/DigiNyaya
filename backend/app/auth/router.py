"""Auth API: phone+OTP signup/login, email+password signup/login, account
linking, refresh-token rotation with reuse detection, logout, password
reset, email verification, and /me.

This is the real identity behind case ownership (see app/main.py's case
endpoints, which depend on app.auth.deps.current_user). The old Aadhaar-demo
login has been retired -- see app/security/auth.py's module docstring.
"""

from __future__ import annotations

import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .db import get_db, utcnow
from .deps import current_user, require_https
from .jwt import ACCESS_TOKEN_TTL, create_access_token, refresh_token_expiry
from .mail import get_mail_provider
from .orm_models import AuthToken, OtpCode, RefreshToken, User
from .phone import normalize_phone
from .rate_limit import (
    enforce_login_rate_limit,
    enforce_otp_request_limit,
    enforce_otp_verify_limit,
    record_login_attempt,
)
from .schemas import (
    LoginEmailRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    PhoneStartRequest,
    PhoneVerifyRequest,
    SignupEmailRequest,
    SignupPhoneVerifyRequest,
    TokenResponse,
    UserProfile,
)
from .security import (
    generate_otp,
    hash_otp,
    hash_password,
    hash_token,
    new_opaque_token,
    new_otp_salt,
    normalize_email,
    verify_otp,
    verify_password,
)
from .sms import get_sms_provider

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_https)])
# Spec lists `GET /me` bare, not under /auth -- a second router sharing the
# same HTTPS guard so main.py only has to include_router() twice.
me_router = APIRouter(tags=["auth"], dependencies=[Depends(require_https)])

REFRESH_COOKIE = "refresh_token"
EMAIL_VERIFY_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(minutes=30)
OTP_TTL = timedelta(minutes=5)

INVALID_CREDENTIALS = "Invalid credentials"
INVALID_OTP = "Invalid or expired OTP"


def _frontend_url() -> str:
    return os.getenv("DIGINYAYA_FRONTEND_URL", "http://localhost:5173")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _dev_otp_field(code: str) -> str | None:
    """There's no real SMS provider wired up (sms.py is a console-log stub),
    so outside production the OTP-start response includes the code directly
    -- without this, testing the phone flow requires reading server logs.
    Always None in production, where a real SMS provider will actually
    deliver it.
    """
    return None if _is_production() else code


def _is_production() -> bool:
    return os.getenv("DIGINYAYA_ENV", "development").strip().lower() == "production"


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=raw_token,
        httponly=True,
        secure=_is_production(),
        # The frontend and backend are separate cross-site origins in
        # production (two onrender.com domains), so the cookie needs
        # SameSite=None to be sent at all; browsers require Secure whenever
        # SameSite=None is set, which _is_production() also guarantees.
        samesite="none" if _is_production() else "lax",
        max_age=int(timedelta(days=7).total_seconds()),
        # Scoped to /auth so this cookie is never sent to unrelated routes.
        path="/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE, path="/auth")


def _issue_session(db: Session, response: Response, user: User, family_id: str | None = None) -> TokenResponse:
    """Issue a fresh access token + a brand-new refresh token (new family if
    none given, i.e. a fresh login; same family when called from /refresh's
    rotation path).
    """
    raw_refresh = new_opaque_token()
    token_row = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw_refresh),
        expires_at=refresh_token_expiry(),
    )
    if family_id:
        token_row.family_id = family_id
    db.add(token_row)
    db.commit()

    _set_refresh_cookie(response, raw_refresh)
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
    )


def _to_profile(user: User) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.email,
        phone=user.phone,
        full_name=user.full_name,
        preferred_language=user.preferred_language,
        email_verified=user.email_verified_at is not None,
        phone_verified=user.phone_verified_at is not None,
        created_at=user.created_at,
    )


def _issue_otp(db: Session, phone: str) -> str:
    code = generate_otp()
    salt = new_otp_salt()
    db.add(
        OtpCode(
            phone=phone,
            code_hash=hash_otp(code, salt),
            salt=salt,
            expires_at=utcnow() + OTP_TTL,
        )
    )
    db.commit()
    return code


def _latest_valid_otp(db: Session, phone: str) -> OtpCode | None:
    return (
        db.query(OtpCode)
        .filter(OtpCode.phone == phone, OtpCode.consumed_at.is_(None), OtpCode.expires_at >= utcnow())
        .order_by(OtpCode.created_at.desc())
        .first()
    )


def _verify_and_consume_otp(db: Session, phone: str, code: str) -> bool:
    """Look up the latest live OTP for `phone`, enforce its attempt cap, and
    verify `code` against it. Increments attempts on a miss; marks consumed
    on a hit. Returns whether verification succeeded.
    """
    otp = _latest_valid_otp(db, phone)
    if otp is None:
        return False
    enforce_otp_verify_limit(otp)
    if not verify_otp(code, otp.salt, otp.code_hash):
        otp.attempts += 1
        db.commit()
        return False
    otp.consumed_at = utcnow()
    db.commit()
    return True


# ----------------------------- signup ----------------------------- #


@router.post("/signup/email", response_model=TokenResponse)
def signup_email(body: SignupEmailRequest, response: Response, db: Session = Depends(get_db)):
    email = normalize_email(body.email)
    if db.query(User).filter(User.email == email).first():
        # Signup collisions are conventionally NOT enumeration-hidden (unlike
        # login/reset) -- the user needs to know to log in instead.
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        preferred_language=body.preferred_language,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _send_verification_email(db, user)
    return _issue_session(db, response, user)


@router.post("/signup/phone/start", response_model=MessageResponse)
def signup_phone_start(body: PhoneStartRequest, db: Session = Depends(get_db)):
    phone = body.phone
    existing = db.query(User).filter(User.phone == phone).first()
    if existing and existing.phone_verified_at is not None:
        raise HTTPException(status_code=409, detail="An account with this phone number already exists")

    enforce_otp_request_limit(db, phone)
    code = _issue_otp(db, phone)
    get_sms_provider().send_otp(phone, code)
    return MessageResponse(message="OTP sent", dev_otp=_dev_otp_field(code))


@router.post("/signup/phone/verify", response_model=TokenResponse)
def signup_phone_verify(body: SignupPhoneVerifyRequest, response: Response, db: Session = Depends(get_db)):
    phone = body.phone
    if not _verify_and_consume_otp(db, phone, body.otp):
        raise HTTPException(status_code=400, detail=INVALID_OTP)

    existing = db.query(User).filter(User.phone == phone).first()
    if existing and existing.phone_verified_at is not None:
        raise HTTPException(status_code=409, detail="An account with this phone number already exists")

    user = existing or User(phone=phone)
    user.phone = phone
    user.phone_verified_at = utcnow()
    user.full_name = body.full_name
    user.preferred_language = body.preferred_language
    if existing is None:
        db.add(user)
    db.commit()
    db.refresh(user)

    return _issue_session(db, response, user)


# ----------------------------- login ----------------------------- #


@router.post("/login/email", response_model=TokenResponse)
def login_email(body: LoginEmailRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    email = normalize_email(body.email)
    ip = _client_ip(request)
    enforce_login_rate_limit(db, email, ip)

    user = db.query(User).filter(User.email == email).first()
    ok = user is not None and verify_password(body.password, user.password_hash)
    record_login_attempt(db, email, ip, ok)
    if not ok:
        # Same message whether the account doesn't exist or the password is
        # wrong -- no user enumeration via error text.
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)

    return _issue_session(db, response, user)


@router.post("/login/phone/start", response_model=MessageResponse)
def login_phone_start(body: PhoneStartRequest, db: Session = Depends(get_db)):
    phone = body.phone
    # Always "send" an OTP whether or not the phone is registered -- an
    # attacker probing this endpoint can't distinguish the two cases.
    enforce_otp_request_limit(db, phone)
    code = _issue_otp(db, phone)
    get_sms_provider().send_otp(phone, code)
    return MessageResponse(message="OTP sent", dev_otp=_dev_otp_field(code))


@router.post("/login/phone/verify", response_model=TokenResponse)
def login_phone_verify(body: PhoneVerifyRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    phone = body.phone
    ip = _client_ip(request)
    enforce_login_rate_limit(db, phone, ip)

    otp_ok = _verify_and_consume_otp(db, phone, body.otp)
    user = db.query(User).filter(User.phone == phone).first() if otp_ok else None
    ok = otp_ok and user is not None
    record_login_attempt(db, phone, ip, ok)
    if not ok:
        # Same generic message for "wrong code" and "no account for this
        # phone" -- don't let /verify be used to enumerate registered numbers.
        raise HTTPException(status_code=400, detail=INVALID_OTP)

    return _issue_session(db, response, user)


# ----------------------------- account linking (auth required) ----------------------------- #


@router.post("/link/phone/start", response_model=MessageResponse)
def link_phone_start(body: PhoneStartRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    phone = body.phone
    other = db.query(User).filter(User.phone == phone).first()
    if other and other.id != user.id and other.phone_verified_at is not None:
        raise HTTPException(status_code=409, detail="This phone number is already linked to another account")

    enforce_otp_request_limit(db, phone)
    code = _issue_otp(db, phone)
    get_sms_provider().send_otp(phone, code)
    return MessageResponse(message="OTP sent", dev_otp=_dev_otp_field(code))


@router.post("/link/phone/verify", response_model=UserProfile)
def link_phone_verify(body: PhoneVerifyRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    phone = body.phone
    if not _verify_and_consume_otp(db, phone, body.otp):
        raise HTTPException(status_code=400, detail=INVALID_OTP)

    other = db.query(User).filter(User.phone == phone).first()
    if other and other.id != user.id:
        raise HTTPException(status_code=409, detail="This phone number is already linked to another account")

    user.phone = phone
    user.phone_verified_at = utcnow()
    db.commit()
    db.refresh(user)
    return _to_profile(user)


# ----------------------------- session management ----------------------------- #


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Authentication required")

    token_hash = hash_token(raw_token)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if row is None:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Authentication required")

    if row.revoked_at is not None:
        # This exact token was already redeemed once -- someone is replaying
        # a stolen refresh token. Revoke the whole family so every token
        # descended from this login stops working, forcing a fresh login.
        db.query(RefreshToken).filter(
            RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)
        ).update({"revoked_at": utcnow()})
        db.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Session revoked, please log in again")

    if row.expires_at < utcnow():
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Session expired, please log in again")

    user = db.get(User, row.user_id)
    if user is None:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Authentication required")

    row.revoked_at = utcnow()
    db.commit()
    return _issue_session(db, response, user, family_id=row.family_id)


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if raw_token:
        token_hash = hash_token(raw_token)
        row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash, RefreshToken.user_id == user.id).first()
        if row and row.revoked_at is None:
            row.revoked_at = utcnow()
            db.commit()
    _clear_refresh_cookie(response)
    return MessageResponse(message="Logged out")


@me_router.get("/me", response_model=UserProfile)
def me(user: User = Depends(current_user)):
    return _to_profile(user)


# ----------------------------- password reset ----------------------------- #


@router.post("/password/reset/request", response_model=MessageResponse)
def password_reset_request(body: PasswordResetRequest, db: Session = Depends(get_db)):
    generic = MessageResponse(message="If this email exists, a reset link was sent.")
    email = normalize_email(body.email)
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        return generic  # enumeration-safe: identical response either way

    raw_token = new_opaque_token()
    db.add(
        AuthToken(
            user_id=user.id,
            purpose="password_reset",
            token_hash=hash_token(raw_token),
            expires_at=utcnow() + PASSWORD_RESET_TTL,
        )
    )
    db.commit()

    link = f"{_frontend_url()}/reset-password?token={raw_token}"
    get_mail_provider().send_password_reset_email(user.email, link)
    return generic


@router.post("/password/reset/confirm", response_model=MessageResponse)
def password_reset_confirm(body: PasswordResetConfirm, db: Session = Depends(get_db)):
    token_hash = hash_token(body.token)
    row = (
        db.query(AuthToken)
        .filter(AuthToken.token_hash == token_hash, AuthToken.purpose == "password_reset", AuthToken.consumed_at.is_(None))
        .first()
    )
    if row is None or row.expires_at < utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user.password_hash = hash_password(body.new_password)
    row.consumed_at = utcnow()
    # A password reset is a credential compromise-recovery action -- kill
    # every existing session so a leaked/old refresh token stops working too.
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).update(
        {"revoked_at": utcnow()}
    )
    db.commit()
    return MessageResponse(message="Password updated")


# ----------------------------- email verification ----------------------------- #


def _send_verification_email(db: Session, user: User) -> None:
    if not user.email:
        return
    raw_token = new_opaque_token()
    db.add(
        AuthToken(
            user_id=user.id,
            purpose="verify_email",
            token_hash=hash_token(raw_token),
            expires_at=utcnow() + EMAIL_VERIFY_TTL,
        )
    )
    db.commit()
    link = f"{_frontend_url()}/verify-email?token={raw_token}"
    get_mail_provider().send_verification_email(user.email, link)


@router.get("/verify-email", response_model=MessageResponse)
def verify_email(token: str, db: Session = Depends(get_db)):
    token_hash = hash_token(token)
    row = (
        db.query(AuthToken)
        .filter(AuthToken.token_hash == token_hash, AuthToken.purpose == "verify_email", AuthToken.consumed_at.is_(None))
        .first()
    )
    if row is None or row.expires_at < utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user.email_verified_at = utcnow()
    row.consumed_at = utcnow()
    db.commit()
    return MessageResponse(message="Email verified")
