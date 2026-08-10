"""Integration tests (via TestClient) for GET /api/cases -- the claimant's
own case list, added as the first Phase 1 item of the post-review roadmap
(2026-08-06): there was previously no way for a claimant to find their own
cases without already knowing the exact case_id.
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


class TestMyCases:
    def test_lists_only_the_current_users_own_cases(self, client, db_session):
        token_a = _signup(client, "ownera@example.com")
        owner_a = client.get("/me", headers={"Authorization": f"Bearer {token_a}"}).json()["id"]
        token_b = _signup(client, "ownerb@example.com")
        owner_b = client.get("/me", headers={"Authorization": f"Bearer {token_b}"}).json()["id"]

        _make_case("DN-MINE-1", owner_a)
        _make_case("DN-NOT-MINE", owner_b)

        r = client.get("/api/cases", headers={"Authorization": f"Bearer {token_a}"})
        assert r.status_code == 200
        case_ids = [c["case_id"] for c in r.json()]
        assert case_ids == ["DN-MINE-1"]

    def test_newest_first(self, client, db_session):
        token = _signup(client, "owner2@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-OLD", owner_id, created_at="2026-01-01T00:00:00")
        _make_case("DN-NEW", owner_id, created_at="2026-02-01T00:00:00")

        r = client.get("/api/cases", headers={"Authorization": f"Bearer {token}"})
        case_ids = [c["case_id"] for c in r.json()]
        assert case_ids == ["DN-NEW", "DN-OLD"]

    def test_summary_shape_omits_the_respondents_view_of_the_case(self, client, db_session):
        # A list endpoint should stay a lightweight summary, not the full
        # case document (description, evidence, mediation/resolution, etc.).
        token = _signup(client, "owner3@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-SHAPE", owner_id, claim_amount=42999.0)

        r = client.get("/api/cases", headers={"Authorization": f"Bearer {token}"})
        item = r.json()[0]
        assert item["respondent"] == "QuickShop Online"
        assert item["claim_amount"] == 42999.0
        assert item["tier"] == 1
        assert "description" not in item
        assert "evidence" not in item

    def test_no_cases_returns_empty_list_not_error(self, client, db_session):
        token = _signup(client, "nocases@example.com")
        r = client.get("/api/cases", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json() == []

    def test_requires_authentication(self, client, db_session):
        r = client.get("/api/cases")
        assert r.status_code == 401
