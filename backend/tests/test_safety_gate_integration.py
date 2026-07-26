"""Integration tests: does the safety gate actually block the graph, not just
the standalone module? Complements tests/test_safety_gate.py (which verifies
each of the 5 conditions in isolation with mocks) by verifying the wiring in
app.core.graph -- the seam a unit test on safety_gate.py alone can't see.

Run with (from backend/):
    python -m unittest tests.test_safety_gate_integration -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import graph  # noqa: E402
from app.core.context import CaseContext  # noqa: E402
from app.core.safety_gate import EscalationResult  # noqa: E402


def _ctx(**overrides) -> CaseContext:
    base = dict(
        case_id="DN-SAFETYTEST-0001",
        owner_id="citizen_test",
        dispute_type="consumer_dispute",
        claimant_name="Ananya Sharma",
        respondent_name="QuickShop Online",
        claim_amount=5000.0,
        description="Paid Rs. 5,000 for a laptop bag that arrived damaged. Seller refuses a refund.",
        evidence=[{"filename": "invoice.pdf", "kind": "invoice"}],
    )
    base.update(overrides)
    return CaseContext(**base)


class TestCheckpointABlocksPipelineEntirely(unittest.TestCase):
    def test_criminal_matter_description_stops_pipeline_before_any_agent_runs(self):
        ctx = _ctx(description="The respondent assaulted me physically and threatened to kill me over this dispute.")
        events = list(graph.run_pipeline(ctx))

        event_types = [e["type"] for e in events]
        self.assertIn("escalated_terminal", event_types)
        self.assertNotIn("resolved", event_types)
        self.assertNotIn("awaiting_decision", event_types)
        # No agent (ingestion/research/analysis/mediation) event should ever
        # appear -- checkpoint A must stop the pipeline before Agent 1 runs.
        agent_events = [e for e in events if e["type"] == "agent"]
        self.assertEqual(agent_events, [])

        self.assertIsNone(ctx.ingestion)
        self.assertIsNone(ctx.research)
        self.assertIsNotNone(ctx.escalation)
        self.assertIn("criminal_matter_detected", ctx.escalation["triggered_conditions"])

    def test_clean_case_runs_the_pipeline_normally_up_to_the_mediation_pause(self):
        ctx = _ctx()
        events = list(graph.run_pipeline(ctx))
        event_types = [e["type"] for e in events]

        self.assertNotIn("escalated_terminal", event_types)
        self.assertIn("awaiting_decision", event_types)
        self.assertIsNone(ctx.escalation)
        self.assertIsNotNone(ctx.ingestion)
        self.assertIsNotNone(ctx.mediation)


class TestCheckpointBDiscardsResolutionOutput(unittest.TestCase):
    def _run_to_mediation_pause(self) -> CaseContext:
        ctx = _ctx()
        list(graph.run_pipeline(ctx))
        self.assertIsNotNone(ctx.mediation, "precondition: pipeline must reach mediation before testing resolve")
        return ctx

    def test_forced_escalation_discards_resolution_before_it_is_ever_yielded(self):
        ctx = self._run_to_mediation_pause()

        forced = EscalationResult(
            triggered_conditions=["confidence_below_floor"],
            details={"confidence_below_floor": "forced for test"},
            user_message="This case cannot be resolved through automated mediation and requires review by a qualified human legal authority.",
            court_registration_status={"status": "not_integrated", "message": "Court database integration pending"},
            case_id=ctx.case_id,
            checkpoint="post_check",
            timestamp="2026-01-01T00:00:00+00:00",
        )

        with patch("app.core.graph.safety_gate.check_escalation", return_value=forced) as mock_check:
            events = list(graph.run_resolution(ctx, via_mediation=True))

        # check_escalation must have been called with the finished ctx as
        # BOTH args (checkpoint B's call convention), not skipped.
        mock_check.assert_called_once_with(ctx, ctx)

        event_types = [e["type"] for e in events]
        self.assertIn("escalated_terminal", event_types)
        # The whole point of checkpoint B: neither the resolution "done"
        # event nor "resolved" may ever reach the caller once escalation
        # fires, even though resolution.finalize() already ran internally.
        self.assertNotIn("resolved", event_types)
        resolution_done_events = [
            e for e in events if e["type"] == "agent" and e.get("agent") == "resolution" and e.get("status") == "done"
        ]
        self.assertEqual(resolution_done_events, [])

        self.assertIsNotNone(ctx.escalation)
        self.assertEqual(ctx.escalation["triggered_conditions"], ["confidence_below_floor"])

    def test_without_forcing_escalation_a_clean_case_resolves_normally(self):
        ctx = self._run_to_mediation_pause()
        events = list(graph.run_resolution(ctx, via_mediation=True))
        event_types = [e["type"] for e in events]

        self.assertIn("resolved", event_types)
        self.assertNotIn("escalated_terminal", event_types)
        self.assertIsNone(ctx.escalation)
        self.assertIsNotNone(ctx.resolution)


if __name__ == "__main__":
    unittest.main()
