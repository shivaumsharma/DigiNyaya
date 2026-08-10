"""Integration tests for POST /api/cases/{id}/submit's compliance
confirmation -- added during the production-readiness review (2026-08-08)
so a case can't be filed (respondent notified, 72h clock started) without
the claimant affirmatively confirming accuracy, mirroring a real court
e-filing "verification". Enforced server-side, not just a disabled
frontend button.
"""
from __future__ import annotations

from app import db


def _signup(client, email, password="correcthorsebatterystaple", name="Test User"):
    r = client.post(
        "/auth/signup/email",
        json={"email": email, "password": password, "full_name": name, "preferred_language": "en-IN"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _make_draft_case(case_id, owner_id):
    case = {
        "case_id": case_id,
        "owner_id": owner_id,
        "dispute_type": "consumer_dispute",
        "claimant": {"name": "Ananya Sharma", "role": "claimant"},
        "respondent": {"name": "QuickShop Online", "role": "respondent"},
        "claim_amount": 5000.0,
        "description": "Paid for a laptop bag that arrived damaged.",
        "evidence": [],
        "status": "draft",
        "tier": 1,
        "tier_label": "Tier 1",
        "created_at": "2026-01-01T00:00:00",
    }
    db.init_db()
    db.save_case(case)
    return case


class TestSubmitConfirmation:
    def test_missing_confirmation_defaults_to_false_and_is_rejected(self, client, db_session):
        token = _signup(client, "submit1@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_draft_case("DN-SUBMIT-1", owner_id)

        r = client.post("/api/cases/DN-SUBMIT-1/submit", json={}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"].lower()
        assert db.get_case("DN-SUBMIT-1")["status"] == "draft"

    def test_explicit_false_is_rejected(self, client, db_session):
        token = _signup(client, "submit2@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_draft_case("DN-SUBMIT-2", owner_id)

        r = client.post(
            "/api/cases/DN-SUBMIT-2/submit",
            json={"confirmed_accurate": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert db.get_case("DN-SUBMIT-2")["status"] == "draft"

    def test_confirmed_true_files_the_case(self, client, db_session):
        token = _signup(client, "submit3@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_draft_case("DN-SUBMIT-3", owner_id)

        r = client.post(
            "/api/cases/DN-SUBMIT-3/submit",
            json={"confirmed_accurate": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "awaiting_response"
        assert db.get_case("DN-SUBMIT-3")["status"] == "awaiting_response"

    def test_confirmation_is_recorded_on_the_case_with_a_timestamp(self, client, db_session):
        token = _signup(client, "submit4@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_draft_case("DN-SUBMIT-4", owner_id)

        client.post(
            "/api/cases/DN-SUBMIT-4/submit",
            json={"confirmed_accurate": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        case = db.get_case("DN-SUBMIT-4")
        assert case["filing_confirmation"]["confirmed_accurate"] is True
        assert case["filing_confirmation"]["confirmed_at"]

    def test_confirmation_is_recorded_as_an_audit_log_event(self, client, db_session):
        token = _signup(client, "submit5@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_draft_case("DN-SUBMIT-5", owner_id)

        client.post(
            "/api/cases/DN-SUBMIT-5/submit",
            json={"confirmed_accurate": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        events = db.get_events("DN-SUBMIT-5")
        assert any(e["type"] == "filing_confirmed" for e in events)

    def test_already_filed_case_cannot_be_resubmitted_even_with_confirmation(self, client, db_session):
        token = _signup(client, "submit6@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_draft_case("DN-SUBMIT-6", owner_id)
        client.post(
            "/api/cases/DN-SUBMIT-6/submit",
            json={"confirmed_accurate": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        r = client.post(
            "/api/cases/DN-SUBMIT-6/submit",
            json={"confirmed_accurate": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 409

    def test_requires_authentication(self, client, db_session):
        r = client.post("/api/cases/DN-NOPE/submit", json={"confirmed_accurate": True})
        assert r.status_code == 401

    def test_cannot_submit_someone_elses_case(self, client, db_session):
        # ensure_owner returns a generic 404 (not 403) for a non-owned case
        # so a stranger can't even confirm the case exists -- see
        # app/security/auth.py's ensure_owner.
        owner_token = _signup(client, "submitowner@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_draft_case("DN-SUBMIT-7", owner_id)

        other_token = _signup(client, "submitother@example.com")
        r = client.post(
            "/api/cases/DN-SUBMIT-7/submit",
            json={"confirmed_accurate": True},
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert r.status_code == 404
