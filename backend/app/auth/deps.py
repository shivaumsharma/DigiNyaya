"""FastAPI dependencies shared across auth endpoints: current-user
extraction and a per-router HTTPS-only guard for production.
"""

from __future__ import annotations

import os

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .jwt import decode_access_token
from .orm_models import User


def _is_production() -> bool:
    return os.getenv("DIGINYAYA_ENV", "development").strip().lower() == "production"


def require_https(request: Request) -> None:
    """Reject plaintext HTTP in production. Scoped to the auth router only
    (not applied globally), so it doesn't change behaviour for endpoints
    outside it.

    Checks X-Forwarded-Proto too since production deployments typically
    terminate TLS at a reverse proxy/load balancer in front of the app, so
    request.url.scheme alone would see plain http even when the client
    connection was https.
    """
    if not _is_production():
        return
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    if scheme != "https":
        raise HTTPException(status_code=400, detail="HTTPS required")


def current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Extract & verify the bearer access token -> the User row.

    This is the identity behind case ownership (see app/main.py's case
    endpoints) -- the old Aadhaar-demo HMAC token scheme that used to gate
    case filing has been retired (see app/security/auth.py).
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    payload = decode_access_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = db.get(User, payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def current_reviewer(user: User = Depends(current_user)) -> User:
    """Same identity as current_user, plus the is_reviewer gate for the
    human-review endpoints (app/routers/reviews.py). No general admin role
    exists -- is_reviewer is deliberately the one narrow capability those
    endpoints need, bootstrapped via scripts/promote_reviewer.py, never
    settable through the API.
    """
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="Reviewer access required")
    return user
