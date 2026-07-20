"""Integration tests (via TestClient) for refresh-token rotation and reuse
(theft) detection.
"""

from __future__ import annotations


def _signup(client, email="rotation@example.com", password="correcthorsebatterystaple"):
    r = client.post(
        "/auth/signup/email",
        json={
            "email": email,
            "password": password,
            "full_name": "Rotation Test",
            "preferred_language": "en-IN",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_refresh_rotates_the_token(client):
    _signup(client)
    old_cookie = client.cookies.get("refresh_token")
    assert old_cookie

    r = client.post("/auth/refresh")
    assert r.status_code == 200, r.text
    new_cookie = client.cookies.get("refresh_token")
    assert new_cookie
    assert new_cookie != old_cookie


def test_refresh_issues_a_usable_new_access_token(client):
    _signup(client)
    r = client.post("/auth/refresh")
    access = r.json()["access_token"]

    me = client.get("/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200


def test_reusing_a_rotated_out_refresh_token_is_detected_and_revokes_the_family(client):
    _signup(client)
    old_cookie = client.cookies.get("refresh_token")

    r = client.post("/auth/refresh")
    assert r.status_code == 200
    new_cookie = client.cookies.get("refresh_token")

    # Replay the OLD (already-rotated-out) token -- this is the theft signal.
    client.cookies.set("refresh_token", old_cookie)
    r = client.post("/auth/refresh")
    assert r.status_code == 401
    assert "revoked" in r.json()["detail"].lower()

    # The reuse must have poisoned the WHOLE family: the legitimately-issued
    # new token is now also revoked, forcing a fresh login.
    client.cookies.set("refresh_token", new_cookie)
    r = client.post("/auth/refresh")
    assert r.status_code == 401


def test_refresh_without_a_cookie_is_rejected(client):
    r = client.post("/auth/refresh")
    assert r.status_code == 401


def test_logout_invalidates_the_refresh_token_server_side(client):
    tokens = _signup(client)
    access = tokens["access_token"]
    issued_refresh_cookie = client.cookies.get("refresh_token")

    r = client.post("/auth/logout", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200

    # Simulate a client (or attacker) that captured the token before logout
    # and ignored the Set-Cookie deletion -- replaying the exact same raw
    # token must still fail, proving revocation happened server-side and
    # doesn't merely rely on the client honouring delete_cookie.
    client.cookies.set("refresh_token", issued_refresh_cookie)
    r = client.post("/auth/refresh")
    assert r.status_code == 401
