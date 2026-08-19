"""Filename/case-id sanitization shared by every storage provider.

Split out of local.py so s3.py (and any future backend) reuses the exact
same rules rather than re-implementing them slightly differently -- a
client-supplied filename (e.g. "../../etc/passwd" or one containing path
separators) must never be able to escape a per-case prefix/directory on ANY
backend, not just the local filesystem one.
"""

from __future__ import annotations

import re
from pathlib import Path

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_case_id(case_id: str) -> str:
    return _UNSAFE_CHARS.sub("_", case_id)


def sanitize_filename(filename: str) -> str:
    name = Path(filename).name  # drop any directory components
    name = _UNSAFE_CHARS.sub("_", name).strip("._") or "file"
    return name[:150]
