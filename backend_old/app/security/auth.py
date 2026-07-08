"""Lightweight session tokens + case-ownership checks + input sanitization.

Fixes the IDOR: every case is owned by the citizen who filed it, and protected
endpoints verify the bearer token's citizen_id matches the case owner. A client
can no longer read or act on an arbitrary case_id.

The signing secret is taken from DIGINYAYA_SECRET, or generated per-process if
unset (so nothing is hardcoded; tokens simply reset on restart — fine for a
demo). For production this would move to a managed secret + real OIDC/Aadhaar.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from typing import Optional

from fastapi import Header, HTTPException

_SECRET = os.getenv("DIGINYAYA_SECRET") or secrets.token_hex(32)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_TEXT = 8000


def make_token(citizen_id: str) -> str:
    sig = hmac.new(_SECRET.encode(), citizen_id.encode(), hashlib.sha256).hexdigest()
    return f"{citizen_id}.{sig}"


def verify_token(token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    citizen_id, sig = token.rsplit(".", 1)
    expected = hmac.new(_SECRET.encode(), citizen_id.encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected):
        return citizen_id
    return None


def current_citizen(authorization: Optional[str] = Header(default=None)) -> str:
    """FastAPI dependency: extract & verify the bearer token -> citizen_id."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    citizen_id = verify_token(token)
    if not citizen_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return citizen_id


def ensure_owner(case: dict, citizen_id: str) -> None:
    if case.get("owner_id") != citizen_id:
        # Generic 404 (not 403) so we don't confirm the case exists to a stranger.
        raise HTTPException(status_code=404, detail="Case not found")


def sanitize_text(text: str, *, max_len: int = MAX_TEXT) -> str:
    """Strip control characters and bound length on untrusted free text."""
    if text is None:
        return ""
    cleaned = _CONTROL_CHARS.sub("", str(text))
    return cleaned[:max_len].strip()
