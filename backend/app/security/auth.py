"""Case-ownership checks + input sanitization.

Fixes the IDOR: every case is owned by the citizen who filed it, and protected
endpoints verify the caller's user id matches the case owner. A client can no
longer read or act on an arbitrary case_id.

The old Aadhaar-demo HMAC token scheme (make_token/verify_token/current_citizen)
that used to live here has been retired -- case ownership now comes from
app.auth.deps.current_user (the real email/phone JWT login). ensure_owner
itself is unchanged: it just compares owner_id against whatever id string the
caller passes.
"""

from __future__ import annotations

import re

from fastapi import HTTPException

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_TEXT = 8000


def ensure_owner(case: dict, owner_id: str) -> None:
    if case.get("owner_id") != owner_id:
        # Generic 404 (not 403) so we don't confirm the case exists to a stranger.
        raise HTTPException(status_code=404, detail="Case not found")


def sanitize_text(text: str, *, max_len: int = MAX_TEXT) -> str:
    """Strip control characters and bound length on untrusted free text."""
    if text is None:
        return ""
    cleaned = _CONTROL_CHARS.sub("", str(text))
    return cleaned[:max_len].strip()
