"""Integration tests (via TestClient) for the per-case evidence-count cap
added during the production-readiness review (2026-08-06): each uploaded
document costs at least one LLM relevance-check call downstream, so an
unbounded upload count is an unbounded cost/latency vector.
"""
from __future__ import annotations

from app import db
from app.routers.documents import _MAX_EVIDENCE_PER_CASE


def _signup(client, email, password="correcthorsebatterystaple", name="Test User"):
    r = client.post(
        "/auth/signup/email",
        json={"email": email, "password": password, "full_name": name, "preferred_language": "en-IN"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _make_case(case_id, owner_id):
    case = {
        "case_id": case_id, "owner_id": owner_id, "dispute_type": "consumer_dispute",
        "claimant": {"name": "Test", "role": "claimant"}, "respondent": {"name": "Vendor", "role": "respondent"},
        "claim_amount": 1000.0, "description": "test", "evidence": [], "status": "draft",
        "tier": 1, "tier_label": "Tier 1", "created_at": "2026-01-01T00:00:00",
    }
    db.init_db()
    db.save_case(case)
    return case


def _pdf_bytes():
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<< >>endobj\ntrailer<< /Root 1 0 R >>\n%%EOF"


class TestEvidenceCountCap:
    def test_allows_uploads_up_to_the_cap(self, client, db_session):
        token = _signup(client, "evidencecap1@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        case_id = "DN-EVCAP-1"
        _make_case(case_id, owner_id)
        headers = {"Authorization": f"Bearer {token}"}

        files = [("files", (f"doc{i}.pdf", _pdf_bytes(), "application/pdf")) for i in range(_MAX_EVIDENCE_PER_CASE)]
        r = client.post(f"/api/cases/{case_id}/documents", files=files, headers=headers)
        assert r.status_code == 200, r.text
        assert len(r.json()) == _MAX_EVIDENCE_PER_CASE

    def test_blocks_the_upload_that_would_exceed_the_cap(self, client, db_session):
        token = _signup(client, "evidencecap2@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        case_id = "DN-EVCAP-2"
        _make_case(case_id, owner_id)
        headers = {"Authorization": f"Bearer {token}"}

        files = [("files", (f"doc{i}.pdf", _pdf_bytes(), "application/pdf")) for i in range(_MAX_EVIDENCE_PER_CASE)]
        r = client.post(f"/api/cases/{case_id}/documents", files=files, headers=headers)
        assert r.status_code == 200, r.text

        one_more = [("files", ("one_too_many.pdf", _pdf_bytes(), "application/pdf"))]
        r = client.post(f"/api/cases/{case_id}/documents", files=one_more, headers=headers)
        assert r.status_code == 400
        assert "maximum" in r.json()["detail"].lower()

        # The rejected batch must not have been partially persisted.
        assert len(db.list_documents(case_id)) == _MAX_EVIDENCE_PER_CASE

    def test_rejects_a_single_batch_that_alone_exceeds_the_cap(self, client, db_session):
        token = _signup(client, "evidencecap3@example.com")
        owner_id = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
        case_id = "DN-EVCAP-3"
        _make_case(case_id, owner_id)
        headers = {"Authorization": f"Bearer {token}"}

        files = [("files", (f"doc{i}.pdf", _pdf_bytes(), "application/pdf")) for i in range(_MAX_EVIDENCE_PER_CASE + 1)]
        r = client.post(f"/api/cases/{case_id}/documents", files=files, headers=headers)
        assert r.status_code == 400
        assert len(db.list_documents(case_id)) == 0
