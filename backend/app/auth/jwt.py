"""JWT access tokens.

Refresh tokens are deliberately NOT JWTs -- they're opaque random strings
(security.new_opaque_token), hashed and stored in refresh_tokens.token_hash.
That makes individual revocation and rotation trivial (delete/mark a DB row)
without needing a JWT-blocklist to invalidate a token before its exp claim.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt

ACCESS_TOKEN_TTL = timedelta(minutes=60)
REFRESH_TOKEN_TTL = timedelta(days=7)
JWT_ALGORITHM = "HS256"

# Separate from DIGINYAYA_SECRET (app/security/auth.py's HMAC demo-token
# secret) so rotating one never invalidates the other. Same dev fallback
# convention: unset -> random per-process, tokens simply reset on restart.
_SECRET = os.getenv("DIGINYAYA_JWT_SECRET") or secrets.token_hex(32)


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
    }
    return jwt.encode(payload, _SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def refresh_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + REFRESH_TOKEN_TTL
