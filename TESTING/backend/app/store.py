"""Backward-compatible shim. Persistence now lives in db.py (SQLite)."""

from .db import all_cases, get_case, save_case, update_case  # noqa: F401
