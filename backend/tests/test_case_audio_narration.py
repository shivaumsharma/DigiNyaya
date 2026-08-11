"""Integration tests (via TestClient) for GET /api/cases/{id}/mediation/audio
and GET /api/cases/{id}/resolution/audio -- read the mediation proposal or
resolution order aloud via Sarvam's Bulbul TTS. synthesize_speech itself is
mocked (see tests/test_tts.py for that call in isolation); these tests
prove the endpoint wiring: ownership, 404-when-absent, and that the
localized (not raw English) text is what actually gets narrated.
"""
from __future__ import annotations

from unittest import mock

from app import db


def _signup(client, email, password="correcthorsebatterystaple", name="Test User"):
    r = client.post(
        "/auth/signup/email",
        json={"email": email, "password": password, "full_name": name, "preferred_language": "en-IN"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _make_case(case_id, owner_id, **overrides):
    case = {
        "case_id": case_id,
        "owner_id": owner_id,
        "dispute_type": "consumer_dispute",
        "claimant": {"name": "Ananya Sharma", "role": "claimant"},
        "respondent": {"name": "QuickShop Online", "role": "respondent"},
        "claim_amount": 5000.0,
        "description": "Paid for a laptop bag that arrived damaged.",
        "evidence": [],
        "status": "resolved",
        "tier": 1,
        "tier_label": "Tier 1",
        "created_at": "2026-01-01T00:00:00",
        "source_language": "en-IN",
        "mediation": None,
        "resolution": None,
    }
    case.update(overrides)
    db.init_db()
    db.save_case(case)
    return case


_MEDIATION = {
    "type": "monetary",
    "amount": 5000.0,
    "amount_display": "Rs. 5,000",
    "compliance_days": 7,
    "headline": "Respondent to pay Rs. 5,000",
    "explanation": "The evidence supports the claim.",
    "rationale": ["Invoice matches the claimed amount."],
    "based_on": [],
    "engine": "scripted",
}

_RESOLUTION = {
    "header": "DigiNyaya Resolution",
    "subheader": "Consumer Dispute",
    "case_id": "DN-AUDIO-1",
    "date": "2026-01-05",
    "parties": {"claimant": "Ananya Sharma", "respondent": "QuickShop Online"},
    "basis": "Consumer Protection Act, 2019",
    "claim_amount_display": "Rs. 5,000",
    "findings": ["The claimant provided a valid invoice."],
    "order": ["Respondent shall pay Rs. 5,000 within 7 days."],
    "cited_precedents": [],
    "relief_amount": 5000.0,
    "relief_amount_display": "Rs. 5,000",
    "compliance_days": 7,
    "compliance_deadline": "2026-01-12",
    "via_mediation": True,
    "engine": "scripted",
    "footer": "Ordered.",
}


class TestMediationAudio:
    def test_requires_authentication(self, client, db_session):
        r = client.get("/api/cases/DN-NOPE/mediation/audio")
        assert r.status_code == 401

    def test_cannot_access_someone_elses_case(self, client, db_session):
        owner_token = _signup(client, "medowner1@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-MED-AUDIO-1", owner_id, mediation=_MEDIATION)

        other_token = _signup(client, "medother1@example.com")
        r = client.get("/api/cases/DN-MED-AUDIO-1/mediation/audio", headers={"Authorization": f"Bearer {other_token}"})
        assert r.status_code == 404

    def test_404s_when_no_mediation_proposal_yet(self, client, db_session):
        token = _signup(client, "medowner2@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-MED-AUDIO-2", owner_id, mediation=None)

        r = client.get("/api/cases/DN-MED-AUDIO-2/mediation/audio", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404

    def test_returns_audio_bytes_and_narrates_the_localized_headline(self, client, db_session):
        token = _signup(client, "medowner3@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-MED-AUDIO-3", owner_id, mediation=_MEDIATION)

        with mock.patch("app.main.synthesize_speech", return_value=b"fake-mp3") as mock_synth:
            r = client.get("/api/cases/DN-MED-AUDIO-3/mediation/audio", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "audio/mpeg"
        assert r.content == b"fake-mp3"
        text_arg, lang_arg = mock_synth.call_args.args
        assert "Respondent to pay Rs. 5,000" in text_arg
        assert lang_arg == "en-IN"

    def test_unavailable_synthesis_returns_503_not_a_crash(self, client, db_session):
        token = _signup(client, "medowner4@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-MED-AUDIO-4", owner_id, mediation=_MEDIATION)

        with mock.patch("app.main.synthesize_speech", return_value=None):
            r = client.get("/api/cases/DN-MED-AUDIO-4/mediation/audio", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 503


class TestResolutionAudio:
    def test_404s_when_no_resolution_yet(self, client, db_session):
        token = _signup(client, "resowner1@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-RES-AUDIO-1", owner_id, resolution=None)

        r = client.get("/api/cases/DN-RES-AUDIO-1/resolution/audio", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404

    def test_returns_audio_bytes_and_narrates_the_order(self, client, db_session):
        token = _signup(client, "resowner2@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-RES-AUDIO-2", owner_id, resolution=_RESOLUTION)

        with mock.patch("app.main.synthesize_speech", return_value=b"fake-mp3") as mock_synth:
            r = client.get("/api/cases/DN-RES-AUDIO-2/resolution/audio", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 200, r.text
        assert r.content == b"fake-mp3"
        text_arg, lang_arg = mock_synth.call_args.args
        assert "Respondent shall pay Rs. 5,000 within 7 days." in text_arg
        assert lang_arg == "en-IN"

    def test_honors_an_explicit_lang_override(self, client, db_session):
        token = _signup(client, "resowner3@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-RES-AUDIO-3", owner_id, resolution=_RESOLUTION, source_language="en-IN")

        with mock.patch("app.main.synthesize_speech", return_value=b"fake-mp3") as mock_synth:
            r = client.get(
                "/api/cases/DN-RES-AUDIO-3/resolution/audio?lang=hi-IN",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200
        _text_arg, lang_arg = mock_synth.call_args.args
        assert lang_arg == "hi-IN"
