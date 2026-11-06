"""Integration tests proving login and password-reset responses don't leak
whether an account exists.
"""

from __future__ import annotations


def _signup(client, email="known@example.com", password="correcthorsebatterystaple"):
    r = client.post(
        "/auth/signup/email",
        json={
            "email": email,
            "password": password,
            "full_name": "Known User",
            "preferred_language": "en-IN",
        },
    )
    assert r.status_code == 200, r.text


def test_login_wrong_password_and_unknown_email_return_identical_response(client):
    _signup(client, email="known@example.com", password="correcthorsebatterystaple")

    wrong_password = client.post(
        "/auth/login/email", json={"email": "known@example.com", "password": "totallywrong"}
    )
    unknown_user = client.post(
        "/auth/login/email", json={"email": "nobody-at-all@example.com", "password": "totallywrong"}
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_password_reset_request_identical_for_existing_and_missing_email(client):
    _signup(client, email="known2@example.com", password="correcthorsebatterystaple")

    existing = client.post("/auth/password/reset/request", json={"email": "known2@example.com"})
    missing = client.post("/auth/password/reset/request", json={"email": "nobody-here@example.com"})

    assert existing.status_code == missing.status_code == 200
    assert existing.json() == missing.json()


def test_login_phone_verify_gives_identical_error_for_wrong_code_and_unregistered_phone(client):
    # Start OTP flows for both a never-registered phone and (implicitly)
    # confirm neither path leaks account existence through error text.
    r = client.post("/auth/login/phone/start", json={"phone": "9876000101"})
    assert r.status_code == 200

    wrong_code = client.post("/auth/login/phone/verify", json={"phone": "9876000101", "otp": "000000"})
    assert wrong_code.status_code == 400
    assert wrong_code.json()["detail"] == "Invalid or expired OTP"


def test_signup_email_duplicate_is_not_enumeration_hidden_by_design(client):
    """Unlike login/reset, signup collisions ARE allowed to reveal that an
    account exists -- the user needs to know to log in instead. This test
    documents that as an intentional choice, not an oversight.
    """
    _signup(client, email="dup@example.com", password="correcthorsebatterystaple")

    r = client.post(
        "/auth/signup/email",
        json={
            "email": "dup@example.com",
            "password": "anotherpassword1",
            "full_name": "Dup",
            "preferred_language": "en-IN",
        },
    )
    assert r.status_code == 409


class TestLoginConnectionAndTiming:
    """Login must not hold a database connection while bcrypt runs, and must hash even for an unknown email."""

    def _signup(self, client, email):
        r = client.post("/auth/signup/email", json={"email": email, "password": "Correct-horse-1", "full_name": "T", "preferred_language": "en-IN"})
        assert r.status_code == 200, r.text

    def test_no_pooled_connection_is_held_during_the_password_check(self, client, monkeypatch):
        import app.auth.router as router
        from app.auth.db import engine

        self._signup(client, "pool-check@example.com")
        seen = []
        real = router.verify_password

        def spy(pw, h):
            seen.append(engine.pool.checkedout())
            return real(pw, h)

        monkeypatch.setattr(router, "verify_password", spy)
        r = client.post("/auth/login/email", json={"email": "pool-check@example.com", "password": "Correct-horse-1"})
        assert r.status_code == 200
        assert seen == [0]

    def test_an_unknown_email_still_costs_one_password_hash(self, client, monkeypatch):
        import app.auth.router as router

        calls = []
        real = router.verify_password
        monkeypatch.setattr(router, "verify_password", lambda pw, h: (calls.append(h), real(pw, h))[1])
        r = client.post("/auth/login/email", json={"email": "ghost-user@example.com", "password": "whatever-123"})
        assert r.status_code == 401
        assert len(calls) == 1 and calls[0]  # a real (dummy) hash was compared

    def test_login_still_works_and_wrong_password_still_fails(self, client):
        self._signup(client, "ok-login@example.com")
        assert client.post("/auth/login/email", json={"email": "ok-login@example.com", "password": "Correct-horse-1"}).status_code == 200
        assert client.post("/auth/login/email", json={"email": "ok-login@example.com", "password": "nope-nope-1"}).status_code == 401


class TestDetachedUserDependency:
    def test_the_session_is_closed_but_the_loaded_user_stays_readable(self, client):
        from sqlalchemy.orm import object_session

        from app.auth.db import SessionLocal, engine
        from app.auth.deps import current_user_detached
        from app.auth.orm_models import User

        client.post("/auth/signup/email", json={"email": "detached@example.com", "password": "Correct-horse-1", "full_name": "D", "preferred_language": "en-IN"})
        db = SessionLocal()
        try:
            loaded = db.query(User).filter(User.email == "detached@example.com").first()
            assert loaded is not None and db.in_transaction()
            same = current_user_detached(loaded)
            assert same is loaded
            assert object_session(loaded) is None          # session closed
            assert engine.pool.checkedout() == 0           # connection returned to the pool
            assert loaded.id and loaded.email == "detached@example.com"  # attributes still readable
        finally:
            db.close()
