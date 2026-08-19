"""Unit tests for app.db's tamper-evident event hash chain (append_event's
hash/prev_hash columns + verify_case_events), added during the
production-readiness review (2026-08-08) -- db.py's own module docstring
already calls the events table "the audit record (essential for a justice
system)"; this makes tampering with it after the fact detectable rather
than merely discouraged by convention.
"""
from __future__ import annotations

import sqlite3

from app import db


def _event(**overrides):
    base = {"type": "ingestion", "agent": "ingestion", "status": "done", "title": "t", "detail": "", "payload": {}, "ts": 1.0}
    base.update(overrides)
    return base


class TestAppendEventHashChain:
    def test_first_event_chains_from_genesis(self, db_session):
        db.init_db()
        db.append_event("DN-CHAIN-1", _event())
        result = db.verify_case_events("DN-CHAIN-1")
        assert result == {
            "verified": True,
            "event_count": 1,
            "verified_count": 1,
            "unverifiable_count": 0,
            "first_break_seq": None,
        }

    def test_multiple_events_form_a_verified_chain(self, db_session):
        db.init_db()
        for i in range(5):
            db.append_event("DN-CHAIN-2", _event(title=f"event {i}"))
        result = db.verify_case_events("DN-CHAIN-2")
        assert result["verified"] is True
        assert result["event_count"] == 5
        assert result["verified_count"] == 5

    def test_chains_are_independent_per_case(self, db_session):
        db.init_db()
        db.append_event("DN-CHAIN-3A", _event(title="only in A"))
        db.append_event("DN-CHAIN-3B", _event(title="only in B"))
        # Same content in both cases -- if case_id weren't part of the hash
        # input, these two single-event chains would produce identical
        # hashes despite being unrelated audit trails.
        result_a = db.verify_case_events("DN-CHAIN-3A")
        result_b = db.verify_case_events("DN-CHAIN-3B")
        assert result_a["verified"] and result_b["verified"]

    def test_no_events_is_trivially_verified(self, db_session):
        db.init_db()
        result = db.verify_case_events("DN-CHAIN-NONE")
        assert result == {
            "verified": True,
            "event_count": 0,
            "verified_count": 0,
            "unverifiable_count": 0,
            "first_break_seq": None,
        }


class TestVerifyCaseEventsDetectsTampering:
    def _tamper_title(self, seq: int, new_title: str) -> None:
        conn = sqlite3.connect(db._DB_PATH)
        conn.execute("UPDATE events SET title=? WHERE seq=?", (new_title, seq))
        conn.commit()
        conn.close()

    def test_mutated_field_breaks_verification_at_that_seq(self, db_session):
        db.init_db()
        db.append_event("DN-TAMPER-1", _event(title="original"))
        seq2 = db.append_event("DN-TAMPER-1", _event(title="second"))
        db.append_event("DN-TAMPER-1", _event(title="third"))

        self._tamper_title(seq2, "altered after the fact")

        result = db.verify_case_events("DN-TAMPER-1")
        assert result["verified"] is False
        assert result["first_break_seq"] == seq2

    def test_tampering_also_breaks_every_subsequent_event(self, db_session):
        # Each event's hash chains from the previous one, so altering an
        # early event invalidates every later event's prev_hash too --
        # verified_count should reflect only the events BEFORE the break.
        db.init_db()
        seq1 = db.append_event("DN-TAMPER-2", _event(title="first"))
        db.append_event("DN-TAMPER-2", _event(title="second"))
        db.append_event("DN-TAMPER-2", _event(title="third"))

        self._tamper_title(seq1, "altered")

        result = db.verify_case_events("DN-TAMPER-2")
        assert result["verified"] is False
        assert result["first_break_seq"] == seq1
        assert result["verified_count"] == 0


class TestVerifyCaseEventsLegacyRows:
    """Rows inserted before the hash chain existed have hash=NULL --
    verify_case_events must treat those as unverifiable (a known gap), not
    as evidence of tampering, and must resume the chain cleanly for events
    appended after the gap."""

    def _insert_legacy_row(self, case_id: str, seq_title: str) -> None:
        conn = sqlite3.connect(db._DB_PATH)
        conn.execute(
            "INSERT INTO events(case_id, type, agent, status, title, detail, payload, ts) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (case_id, "legacy", "legacy", "done", seq_title, "", "{}", 0.0),
        )
        conn.commit()
        conn.close()

    def test_legacy_row_counts_as_unverifiable_not_broken(self, db_session):
        db.init_db()
        self._insert_legacy_row("DN-LEGACY-1", "pre-hash event")
        result = db.verify_case_events("DN-LEGACY-1")
        assert result["verified"] is True
        assert result["event_count"] == 1
        assert result["unverifiable_count"] == 1
        assert result["verified_count"] == 0

    def test_events_after_a_legacy_row_form_their_own_verified_chain(self, db_session):
        db.init_db()
        self._insert_legacy_row("DN-LEGACY-2", "pre-hash event")
        db.append_event("DN-LEGACY-2", _event(title="first hashed event"))
        db.append_event("DN-LEGACY-2", _event(title="second hashed event"))

        result = db.verify_case_events("DN-LEGACY-2")
        assert result["verified"] is True
        assert result["event_count"] == 3
        assert result["unverifiable_count"] == 1
        assert result["verified_count"] == 2
