"""Integration tests (via TestClient) for the human-review workflow:
app/routers/reviews.py's queue/detail/decision endpoints, plus
POST /api/cases/{id}/request-review in app/main.py.

Cases are created directly via app.db (bypassing the real pipeline, same
convention as tests/test_discrepancy_agent.py) since these tests are about
the review workflow's own logic, not agent behavior.
"""
from __future__ import annotations

from app import db
from app.auth.orm_models import User


def _signup(client, email, password="correcthorsebatterystaple", name="Test User"):
    r = client.post(
        "/auth/signup/email",
        json={"email": email, "password": password, "full_name": name, "preferred_language": "en-IN"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _promote(db_session, email):
    user = db_session.query(User).filter(User.email == email).first()
    user.is_reviewer = True
    db_session.commit()


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
        "status": "awaiting_response",
        "tier": 1,
        "tier_label": "Tier 1",
        "created_at": "2026-01-01T00:00:00",
    }
    case.update(overrides)
    db.init_db()
    db.save_case(case)
    return case


class TestReviewerGate:
    def test_non_reviewer_gets_403(self, client, db_session):
        token = _signup(client, "citizen@example.com")
        r = client.get("/api/reviews/queue", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    def test_promoted_reviewer_gets_200(self, client, db_session):
        token = _signup(client, "reviewer@example.com")
        _promote(db_session, "reviewer@example.com")
        r = client.get("/api/reviews/queue", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200


class TestReviewQueue:
    def test_escalated_case_appears_with_reason(self, client, db_session):
        owner_token = _signup(client, "owner1@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-ESCALATED", owner_id, status="escalated")

        reviewer_token = _signup(client, "reviewer1@example.com")
        _promote(db_session, "reviewer1@example.com")
        r = client.get("/api/reviews/queue", headers={"Authorization": f"Bearer {reviewer_token}"})
        assert r.status_code == 200
        ids = {c["case_id"]: c for c in r.json()}
        assert "DN-REVIEW-ESCALATED" in ids
        assert "safety-gate" in ids["DN-REVIEW-ESCALATED"]["reason"]

    def test_manually_requested_case_appears(self, client, db_session):
        owner_token = _signup(client, "owner2@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-REQUESTED", owner_id, status="resolved")

        r = client.post(
            "/api/cases/DN-REVIEW-REQUESTED/request-review",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert r.status_code == 200

        reviewer_token = _signup(client, "reviewer2@example.com")
        _promote(db_session, "reviewer2@example.com")
        r = client.get("/api/reviews/queue", headers={"Authorization": f"Bearer {reviewer_token}"})
        ids = {c["case_id"]: c for c in r.json()}
        assert "DN-REVIEW-REQUESTED" in ids
        assert "requested" in ids["DN-REVIEW-REQUESTED"]["reason"]

    def test_tier2_awaiting_signoff_appears_but_plain_resolved_case_does_not(self, client, db_session):
        owner_token = _signup(client, "owner3@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case(
            "DN-REVIEW-TIER2", owner_id, status="resolved",
            resolution={"requires_human_signoff": True, "relief_amount_display": "Rs. 5,000"},
        )
        _make_case(
            "DN-REVIEW-TIER1DONE", owner_id, status="resolved",
            resolution={"requires_human_signoff": False, "relief_amount_display": "Rs. 5,000"},
        )

        reviewer_token = _signup(client, "reviewer3@example.com")
        _promote(db_session, "reviewer3@example.com")
        r = client.get("/api/reviews/queue", headers={"Authorization": f"Bearer {reviewer_token}"})
        ids = {c["case_id"] for c in r.json()}
        assert "DN-REVIEW-TIER2" in ids
        assert "DN-REVIEW-TIER1DONE" not in ids

    def test_already_decided_case_is_excluded(self, client, db_session):
        owner_token = _signup(client, "owner4@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case(
            "DN-REVIEW-DONE", owner_id, status="resolved",
            reviewer_decision={"approved": True, "note": "fine", "reviewer_id": "x", "reviewer_name": "R"},
        )

        reviewer_token = _signup(client, "reviewer4@example.com")
        _promote(db_session, "reviewer4@example.com")
        r = client.get("/api/reviews/queue", headers={"Authorization": f"Bearer {reviewer_token}"})
        ids = {c["case_id"] for c in r.json()}
        assert "DN-REVIEW-DONE" not in ids


class TestRequestReview:
    def test_rejects_draft_case(self, client, db_session):
        owner_token = _signup(client, "owner5@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-DRAFT", owner_id, status="draft")
        r = client.post("/api/cases/DN-REVIEW-DRAFT/request-review", headers={"Authorization": f"Bearer {owner_token}"})
        assert r.status_code == 409

    def test_rejects_duplicate_request(self, client, db_session):
        owner_token = _signup(client, "owner6@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-DUP", owner_id, status="resolved")
        r1 = client.post("/api/cases/DN-REVIEW-DUP/request-review", headers={"Authorization": f"Bearer {owner_token}"})
        assert r1.status_code == 200
        r2 = client.post("/api/cases/DN-REVIEW-DUP/request-review", headers={"Authorization": f"Bearer {owner_token}"})
        assert r2.status_code == 409

    def test_non_owner_gets_404(self, client, db_session):
        owner_token = _signup(client, "owner7@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-OTHER", owner_id, status="resolved")

        stranger_token = _signup(client, "stranger@example.com")
        r = client.post("/api/cases/DN-REVIEW-OTHER/request-review", headers={"Authorization": f"Bearer {stranger_token}"})
        assert r.status_code == 404


class TestSubmitDecision:
    def test_approve_records_decision_and_resolves_case(self, client, db_session):
        owner_token = _signup(client, "owner8@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-APPROVE", owner_id, status="escalated")

        reviewer_token = _signup(client, "reviewer8@example.com", name="Reviewer Eight")
        _promote(db_session, "reviewer8@example.com")
        r = client.post(
            "/api/reviews/DN-REVIEW-APPROVE/decision",
            json={"approve": True, "note": "Reviewed and approved."},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "resolved"
        assert body["reviewer_decision"]["approved"] is True
        assert body["reviewer_decision"]["reviewer_name"] == "Reviewer Eight"

    def test_reject_with_relief_override_is_recorded(self, client, db_session):
        owner_token = _signup(client, "owner9@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-REJECT", owner_id, status="escalated")

        reviewer_token = _signup(client, "reviewer9@example.com")
        _promote(db_session, "reviewer9@example.com")
        r = client.post(
            "/api/reviews/DN-REVIEW-REJECT/decision",
            json={"approve": False, "note": "No merit shown.", "relief_amount": 0},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        assert r.status_code == 200
        assert r.json()["reviewer_decision"]["approved"] is False
        assert r.json()["reviewer_decision"]["relief_amount"] == 0

    def test_deciding_twice_conflicts(self, client, db_session):
        owner_token = _signup(client, "owner10@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-TWICE", owner_id, status="escalated")

        reviewer_token = _signup(client, "reviewer10@example.com")
        _promote(db_session, "reviewer10@example.com")
        r1 = client.post(
            "/api/reviews/DN-REVIEW-TWICE/decision",
            json={"approve": True, "note": "ok"},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        assert r1.status_code == 200
        r2 = client.post(
            "/api/reviews/DN-REVIEW-TWICE/decision",
            json={"approve": True, "note": "again"},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        assert r2.status_code == 409

    def test_deciding_case_not_awaiting_review_conflicts(self, client, db_session):
        owner_token = _signup(client, "owner11@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-NOTNEEDED", owner_id, status="ready")

        reviewer_token = _signup(client, "reviewer11@example.com")
        _promote(db_session, "reviewer11@example.com")
        r = client.post(
            "/api/reviews/DN-REVIEW-NOTNEEDED/decision",
            json={"approve": True, "note": "ok"},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        assert r.status_code == 409


class TestReviewDetail:
    def test_non_reviewer_cannot_view_detail(self, client, db_session):
        owner_token = _signup(client, "owner12@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-DETAIL", owner_id, status="escalated")
        r = client.get("/api/reviews/DN-REVIEW-DETAIL", headers={"Authorization": f"Bearer {owner_token}"})
        assert r.status_code == 403

    def test_reviewer_can_view_any_case_not_just_their_own(self, client, db_session):
        owner_token = _signup(client, "owner13@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {owner_token}"}).json()["id"]
        _make_case("DN-REVIEW-VIEWABLE", owner_id, status="escalated")

        reviewer_token = _signup(client, "reviewer13@example.com")
        _promote(db_session, "reviewer13@example.com")
        r = client.get("/api/reviews/DN-REVIEW-VIEWABLE", headers={"Authorization": f"Bearer {reviewer_token}"})
        assert r.status_code == 200
        assert r.json()["case_id"] == "DN-REVIEW-VIEWABLE"
