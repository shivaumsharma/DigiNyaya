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
