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


def _reviewer_allowlist() -> set[str]:
    raw = os.getenv("DIGINYAYA_REVIEWER_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _ensure_reviewer_allowlisted(user: User, db: Session) -> None:
    """Self-healing companion to scripts/promote_reviewer.py's one-off DB
    write. That script mutates a row directly -- fine against a persistent
    database (AWS production: RDS Postgres), but this codebase also runs
    against ephemeral/reset-prone databases in other contexts (a fresh local
    SQLite file, a CI/eval run, a still-in-parallel Render deployment on a
    free tier with an ephemeral container disk) where a one-time grant would
    silently vanish the next time the database resets. DIGINYAYA_REVIEWER_EMAILS
    (a comma-separated allowlist read from the environment, which survives a
    database reset the way a DB row can't) re-applies the grant on every
    authenticated request instead -- a no-op once already set, so the cost
    is one cheap membership check per request."""
    if user.is_reviewer:
        return
    email = (user.email or "").strip().lower()
    if email and email in _reviewer_allowlist():
        user.is_reviewer = True
        db.commit()


def require_https(request: Request) -> None:
    """Reject plaintext HTTP in production. Scoped to the auth router only
    (not applied globally), so it doesn't change behaviour for endpoints
    outside it.

    Checks X-Forwarded-Proto too since production deployments typically
    terminate TLS at a reverse proxy/load balancer in front of the app, so
    request.url.scheme alone would see plain http even when the client
    connection was https.

    Also accepts X-Diginyaya-Edge-Https: 1 -- the current production
    deployment terminates TLS at a CloudFront distribution in front of a
    SingleInstance Elastic Beanstalk environment (no ALB), and CloudFront
    was confirmed, empirically, to silently drop a custom origin header
    named X-Forwarded-Proto specifically (accepted with no error at
    creation, never arrives at the app) -- undocumented, but consistent
    across a CloudFront Function attempt (outright rejected as a
    disallowed header) and a static custom-origin-header attempt (silently
    dropped) versus an identically-configured, arbitrarily-named header
    (arrives intact). See infra/cloudfront_backend.tf.
    """
    if not _is_production():
        return
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    if scheme == "https" or request.headers.get("x-diginyaya-edge-https") == "1":
        return
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
    _ensure_reviewer_allowlisted(user, db)
    return user


def current_reviewer(user: User = Depends(current_user)) -> User:
    """Same identity as current_user, plus the is_reviewer gate for the
    human-review endpoints (app/routers/reviews.py). No general admin role
    exists -- is_reviewer is deliberately the one narrow capability those
    endpoints need, granted either via scripts/promote_reviewer.py (a direct
    DB write, fine for a persistent database) or, more durably in this app's
    current deployment, via DIGINYAYA_REVIEWER_EMAILS (see
    _ensure_reviewer_allowlisted above). Never settable through the API.
    """
    if not user.is_reviewer:
        raise HTTPException(status_code=403, detail="Reviewer access required")
    return user
