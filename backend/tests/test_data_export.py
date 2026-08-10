"""Integration tests for GET /api/me/data-export -- the self-service data
export added for the DPDP Act 2023 access/portability principle
(2026-08-08 production-readiness review). Scoped deliberately to export
only, not deletion -- see the endpoint's docstring in app/main.py for why.
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


class TestDataExport:
    def test_requires_authentication(self, client, db_session):
        r = client.get("/api/me/data-export")
        assert r.status_code == 401

    def test_includes_profile_fields(self, client, db_session):
        token = _signup(client, "exportprofile@example.com", name="Export Test User")
        r = client.get("/api/me/data-export", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        profile = r.json()["profile"]
        assert profile["email"] == "exportprofile@example.com"
        assert profile["full_name"] == "Export Test User"

    def test_includes_only_the_current_users_own_cases(self, client, db_session):
        token_a = _signup(client, "exporta@example.com")
        owner_a = client.get("/me", headers={"Authorization": f"Bearer {token_a}"}).json()["id"]
        token_b = _signup(client, "exportb@example.com")
        owner_b = client.get("/me", headers={"Authorization": f"Bearer {token_b}"}).json()["id"]

        _make_case("DN-EXPORT-MINE", owner_a)
        _make_case("DN-EXPORT-NOT-MINE", owner_b)

        r = client.get("/api/me/data-export", headers={"Authorization": f"Bearer {token_a}"})
        case_ids = [c["case_id"] for c in r.json()["cases"]]
        assert case_ids == ["DN-EXPORT-MINE"]

    def test_case_export_is_full_not_a_summary(self, client, db_session):
        # Unlike GET /api/cases (a lightweight list), the export should
        # include the full case document -- description, evidence, etc.
        token = _signup(client, "exportfull@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-EXPORT-FULL", owner_id, description="A very specific complaint text.")

        r = client.get("/api/me/data-export", headers={"Authorization": f"Bearer {token}"})
        case = r.json()["cases"][0]
        assert case["description"] == "A very specific complaint text."

    def test_documents_included_but_storage_path_omitted(self, client, db_session):
        token = _signup(client, "exportdocs@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-EXPORT-DOCS", owner_id)
        db.insert_document({
            "id": "DOC-export-1", "case_id": "DN-EXPORT-DOCS", "original_filename": "receipt.pdf",
            "storage_path": "/internal/uploads/secret/path.pdf", "mime_type": "application/pdf",
            "file_size": 100, "cleaned_text": "Receipt for Rs 5000",
        })

        r = client.get("/api/me/data-export", headers={"Authorization": f"Bearer {token}"})
        docs = r.json()["cases"][0]["documents"]
        assert len(docs) == 1
        assert docs[0]["cleaned_text"] == "Receipt for Rs 5000"
        assert "storage_path" not in docs[0]

    def test_events_included(self, client, db_session):
        token = _signup(client, "exportevents@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        _make_case("DN-EXPORT-EVENTS", owner_id)
        db.append_event("DN-EXPORT-EVENTS", {"type": "ingestion", "agent": "ingestion", "status": "done", "title": "t", "detail": "", "payload": {}, "ts": 1.0})

        r = client.get("/api/me/data-export", headers={"Authorization": f"Bearer {token}"})
        events = r.json()["cases"][0]["events"]
        assert len(events) == 1
        assert events[0]["type"] == "ingestion"

    def test_no_cases_returns_empty_list_not_error(self, client, db_session):
        token = _signup(client, "exportnocases@example.com")
        r = client.get("/api/me/data-export", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["cases"] == []
