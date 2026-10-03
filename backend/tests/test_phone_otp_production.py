"""In production there is no SMS provider that delivers codes, so phone OTP
endpoints must refuse (503) instead of issuing a code and echoing it back.
"""

from __future__ import annotations

import pytest

EDGE_HTTPS = {"X-Diginyaya-Edge-Https": "1"}


@pytest.mark.parametrize("path", ["/auth/signup/phone/start", "/auth/login/phone/start"])
def test_phone_start_is_refused_in_production_and_never_echoes_otp(client, monkeypatch, path):
    monkeypatch.setenv("DIGINYAYA_ENV", "production")

    r = client.post(path, json={"phone": "9876000202"}, headers=EDGE_HTTPS)

    assert r.status_code == 503
    assert "dev_otp" not in r.text
    assert "email" in r.json()["detail"].lower()


def test_phone_start_still_echoes_otp_in_development(client):
    r = client.post("/auth/login/phone/start", json={"phone": "9876000203"})

    assert r.status_code == 200
    assert r.json()["dev_otp"]
