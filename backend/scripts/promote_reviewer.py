"""Grant a user reviewer access (app.auth.orm_models.User.is_reviewer).

No admin UI exists for this on purpose -- there's no general admin role in
this app, just the one narrow capability the human-review workflow needs
(see app/routers/reviews.py). Run this once per new reviewer.

NOTE for this app's current deployment: this writes directly to the SQLite
file, which on Render's free tier lives on ephemeral disk and is wiped on
every redeploy/restart -- so a grant made this way will silently vanish the
next time anything merges to main. For a grant that needs to actually stick,
set DIGINYAYA_REVIEWER_EMAILS (comma-separated) in Render's environment
variables instead -- app.auth.deps._ensure_reviewer_allowlisted re-applies it
on every authenticated request, so it survives redeploys. This script is
still the right tool for a persistent database, or a one-off local grant.

Usage (from the backend folder):
    python -m scripts.promote_reviewer someone@example.com
    python -m scripts.promote_reviewer someone@example.com --revoke
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from app.auth.db import SessionLocal, init_auth_db  # noqa: E402
from app.auth.orm_models import User  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("email_or_phone", help="the user's email or phone, as they signed up with")
    ap.add_argument("--revoke", action="store_true", help="remove reviewer access instead of granting it")
    args = ap.parse_args()

    init_auth_db()
    db = SessionLocal()
    try:
        identifier = args.email_or_phone.strip().lower()
        user = (
            db.query(User)
            .filter((User.email == identifier) | (User.phone == args.email_or_phone.strip()))
            .first()
        )
        if user is None:
            print(f"No user found matching '{args.email_or_phone}'.")
            return 1

        user.is_reviewer = not args.revoke
        db.commit()
        verb = "Revoked" if args.revoke else "Granted"
        print(f"{verb} reviewer access for {user.full_name} ({user.email or user.phone}).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
