"""E.164 phone number normalization, defaulting to +91 (India) when the
caller omits a country code -- matches the spec: "Phone number (E.164
format, default +91)".
"""

from __future__ import annotations

import re

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_BARE_INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")  # Indian mobile numbers start 6-9, 10 digits


def normalize_phone(raw: str) -> str:
    value = raw.strip().replace(" ", "").replace("-", "")
    if value.startswith("+"):
        if not _E164_RE.match(value):
            raise ValueError("Phone number must be in E.164 format, e.g. +919876543210")
        return value
    if _BARE_INDIAN_MOBILE_RE.match(value):
        return f"+91{value}"
    raise ValueError(
        "Phone number must be in E.164 format (e.g. +919876543210) or a 10-digit Indian mobile number"
    )
