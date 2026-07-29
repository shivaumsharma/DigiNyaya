"""Password hashing, OTP generation/verification, and identifier/token
hashing utilities.

All secret comparisons are constant-time (bcrypt.checkpw and
hmac.compare_digest both compare digest bytes in constant time) to avoid
timing side channels on login/OTP-verify endpoints.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import bcrypt

BCRYPT_COST = 12


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_COST)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/corrupt hash -- treat as no match rather than raising into
        # a caller that might turn it into a 500 leaking hash internals.
        return False


def generate_otp() -> str:
    """6-digit numeric OTP from a CSPRNG (not random.randint)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def new_otp_salt() -> str:
    return secrets.token_hex(16)


def hash_otp(code: str, salt: str) -> str:
    """SHA-256 salted hash. Bcrypt's ~100ms cost is unnecessary for a 6-digit
    code that already expires in 5 minutes and is capped at 5 guesses --
    10^6 possibilities / 5 attempts makes brute force infeasible regardless
    of hash speed -- so SHA-256 keeps verification fast without weakening
    anything.
    """
    return hashlib.sha256((salt + code).encode("utf-8")).hexdigest()


def verify_otp(code: str, salt: str, code_hash: str) -> bool:
    candidate = hash_otp(code, salt)
    return hmac.compare_digest(candidate, code_hash)


def normalize_email(email: str) -> str:
    """The whole of citext's job in this app: always write/query email
    lowercased, with a plain unique index on the column (see orm_models.py).
    """
    return email.strip().lower()


def hash_token(token: str) -> str:
    """SHA-256 for opaque high-entropy tokens (refresh tokens, email-verify /
    password-reset links). These are 256-bit random values, not
    human-guessable passwords -- bcrypt's slow KDF buys nothing since the
    token's own entropy is the defense. Stored hashed so a DB read alone
    can't be replayed as a live token.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)
