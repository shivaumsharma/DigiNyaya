"""Integration tests (via TestClient) for GET /api/cases/{id}/mediation/audio
and GET /api/cases/{id}/resolution/audio -- read the mediation proposal or
resolution order aloud via Sarvam Bulbul, in whichever language the case
response is already localized to.

app.language.tts.synthesize_speech is mocked throughout (a live call would
hit the real Sarvam API); these tests are about the endpoint's own
wiring -- ownership, narration-text assembly, 404/503 handling, rate
limiting -- not the TTS call itself (see tests/test_tts.py for that).
"""
from __future__ import annotations

from unittest.mock import patch

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
        "source_language": "en-IN",
        "mediation": None,
        "resolution": None,
        "created_at": "2026-01-01T00:00:00",
    }
    case.update(overrides)
    db.init_db()
    db.save_case(case)
    return case


_MEDIATION = {
    "type": "refund",
    "amount": 5000.0,
    "amount_display": "Rs. 5,000",
    "compliance_days": 7,
    "headline": "Settlement proposed: full refund",
    "explanation": "The evidence supports the claimant's account.",
    "rationale": ["The invoice matches the claimed amount.", "No response was filed."],
    "based_on": [],
    "engine": "llm",
}

_RESOLUTION = {
    "header": "DigiNyaya Resolution",
    "subheader": "Consumer Dispute",
    "case_id": "DN-AUDIO-1",
    "date": "2026-01-05",
    "parties": {"claimant": "Ananya Sharma", "respondent": "QuickShop Online"},
    "basis": "Consumer Protection Act, 2019",
    "claim_amount_display": "Rs. 5,000",
    "findings": ["The order was not delivered as promised."],
    "order": ["QuickShop Online shall pay Ananya Sharma Rs. 5,000."],
    "cited_precedents": [],
    "relief_amount": 5000.0,
    "relief_amount_display": "Rs. 5,000",
    "compliance_days": 7,
    "compliance_deadline": "2026-01-12",
    "via_mediation": True,
    "engine": "llm",
    "footer": "Ordered by DigiNyaya.",
}


class TestMediationAudio:
    def test_requires_authentication(self, client, db_session):
        r = client.get("/api/cases/DN-NOPE/mediation/audio")
        assert r.status_code == 401

    def test_cannot_access_someone_elses_case(self, client, db_session):
        owner_token = _signup(client, "audioowner1@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-AUDIO-OTHER1", owner_id, mediation=_MEDIATION)

        other_token = _signup(client, "audiostranger1@example.com")
        r = client.get("/api/cases/DN-AUDIO-OTHER1/mediation/audio", headers={"Authorization": f"Bearer {other_token}"})
        assert r.status_code == 404

    def test_no_mediation_yet_404s(self, client, db_session):
        token = _signup(client, "audioowner2@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-AUDIO-NOMED", owner_id, mediation=None)

        r = client.get("/api/cases/DN-AUDIO-NOMED/mediation/audio", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404

    def test_returns_audio_bytes_on_success(self, client, db_session):
        token = _signup(client, "audioowner3@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-AUDIO-MED1", owner_id, mediation=_MEDIATION)

        with patch("app.main.synthesize_speech", return_value=b"fake-mp3-bytes") as mock_tts:
            r = client.get("/api/cases/DN-AUDIO-MED1/mediation/audio", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/mpeg"
        assert r.content == b"fake-mp3-bytes"
        mock_tts.assert_called_once()
        narrated_text = mock_tts.call_args.args[0]
        assert "full refund" in narrated_text
        assert "invoice matches the claimed amount" in narrated_text

    def test_narration_unavailable_returns_503(self, client, db_session):
        token = _signup(client, "audioowner4@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-AUDIO-MED2", owner_id, mediation=_MEDIATION)

        with patch("app.main.synthesize_speech", return_value=None):
            r = client.get("/api/cases/DN-AUDIO-MED2/mediation/audio", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 503


class TestResolutionAudio:
    def test_no_resolution_yet_404s(self, client, db_session):
        token = _signup(client, "audioowner5@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-AUDIO-NORES", owner_id, resolution=None)

        r = client.get("/api/cases/DN-AUDIO-NORES/resolution/audio", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404

    def test_returns_audio_bytes_on_success(self, client, db_session):
        token = _signup(client, "audioowner6@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-AUDIO-RES1", owner_id, resolution=_RESOLUTION)

        with patch("app.main.synthesize_speech", return_value=b"fake-mp3-bytes") as mock_tts:
            r = client.get("/api/cases/DN-AUDIO-RES1/resolution/audio", headers={"Authorization": f"Bearer {token}"})

        assert r.status_code == 200
        assert r.content == b"fake-mp3-bytes"
        narrated_text = mock_tts.call_args.args[0]
        assert "not delivered as promised" in narrated_text
        assert "shall pay Ananya Sharma" in narrated_text
        assert "Ordered by DigiNyaya" in narrated_text

    def test_language_passed_through_matches_lang_query_param(self, client, db_session):
        token = _signup(client, "audioowner7@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-AUDIO-RES2", owner_id, resolution=_RESOLUTION, source_language="en-IN")

        with patch("app.main.synthesize_speech", return_value=b"fake-mp3-bytes") as mock_tts:
            r = client.get(
                "/api/cases/DN-AUDIO-RES2/resolution/audio?lang=hi-IN",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200
        assert mock_tts.call_args.args[1] == "hi-IN"
