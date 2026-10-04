"""Tests for the optional statute-grounding layer: the bundled data, deterministic retrieval, the
anti-hallucination guard, and (flag on vs off) the end-to-end pipeline. The flag defaults to OFF so existing
results are unchanged."""
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
from app.data.loader import load_statutes, validate_statute  # noqa: E402
from app.rag import statutes as sr  # noqa: E402

REGISTERED = ("consumer_dispute", "money_recovery", "contract_breach", "cheque_bounce",
              "tenancy_dispute", "property_dispute", "employment_dispute")


def _ctx(dispute_type="consumer_dispute", description=None, amount=5000.0) -> CaseContext:
    return CaseContext(
        case_id="DN-STATUTE-0001", owner_id="citizen_test", dispute_type=dispute_type,
        claimant_name="Ananya Sharma", respondent_name="QuickShop Online", claim_amount=amount,
        description=description or "Paid Rs. 5,000 for a laptop bag that arrived damaged. Seller refuses a refund.",
        evidence=[{"filename": "invoice.pdf", "kind": "invoice"}],
    )


class TestStatuteData(unittest.TestCase):
    def test_every_entry_is_valid_and_ids_are_unique(self):
        import json
        raw = json.loads((Path(__file__).resolve().parents[1] / "app" / "data" / "statutes.json").read_text(encoding="utf-8"))
        self.assertEqual(len(load_statutes()), len(raw), "an entry failed validation and was silently dropped")
        ids = [s["id"] for s in raw]
        self.assertEqual(len(ids), len(set(ids)))
        for s in raw:
            self.assertTrue(validate_statute(s)[0], s["id"])

    def test_every_registered_dispute_type_has_a_core_provision(self):
        for dt in REGISTERED:
            self.assertTrue([s for s in load_statutes() if dt in s["dispute_types"] and s.get("core")], dt)

    def test_dispute_types_are_only_registered_ones(self):
        for s in load_statutes():
            self.assertTrue(set(s["dispute_types"]) <= set(REGISTERED), s["id"])


class TestRetrieval(unittest.TestCase):
    def test_cheque_text_ranks_section_138_first_and_stays_in_type(self):
        got = sr.retrieve_statutes("cheque_bounce", "The cheque was dishonoured for insufficient funds after a demand notice.")
        self.assertEqual(got[0]["id"], "NIA1881-138")
        self.assertTrue(all("Negotiable" in g["act"] or "Limitation" in g["act"] or "Interest" in g["act"] for g in got))

    def test_consumer_text_returns_consumer_act(self):
        got = sr.retrieve_statutes("consumer_dispute", "The product was defective and the seller refused a refund.")
        self.assertIn("Consumer Protection Act, 2019", {g["act"] for g in got})
        self.assertLessEqual(len(got), 3)

    def test_no_keyword_match_still_returns_core_provisions(self):
        got = sr.retrieve_statutes("contract_breach", "zzz qqq")
        self.assertTrue(got)
        core_ids = {s["id"] for s in load_statutes() if s.get("core") and "contract_breach" in s["dispute_types"]}
        self.assertTrue({g["id"] for g in got} <= core_ids)

    def test_unknown_dispute_type_returns_nothing(self):
        self.assertEqual(sr.retrieve_statutes("criminal_matter", "assault"), [])

    def test_is_deterministic(self):
        a = sr.retrieve_statutes("money_recovery", "unpaid loan with interest overdue")
        self.assertEqual(a, sr.retrieve_statutes("money_recovery", "unpaid loan with interest overdue"))


class TestGuard(unittest.TestCase):
    STATUTES = [{"section": "138"}, {"section": "2(11)"}]

    def test_drops_sentences_citing_an_unretrieved_section(self):
        out = sr.strip_unretrieved_section_mentions(
            ["The respondent is liable under Section 138.", "This also breaches Section 420 of the Penal Code.",
             "No statute is mentioned here."], self.STATUTES)
        self.assertEqual(out, ["The respondent is liable under Section 138.", "No statute is mentioned here."])

    def test_subsection_style_numbers_use_the_leading_number(self):
        self.assertEqual(sr.strip_unretrieved_section_mentions(["See section 2 and s. 138."], self.STATUTES),
                         ["See section 2 and s. 138."])

    def test_with_nothing_retrieved_any_section_citation_is_dropped(self):
        self.assertEqual(sr.strip_unretrieved_section_mentions(["Under Section 73 the claim fails.", "Fine."], []), ["Fine."])


class TestFlag(unittest.TestCase):
    def test_default_is_off_and_accepts_common_truthy_values(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DIGINYAYA_STATUTE_GROUNDING", None)
            self.assertFalse(sr.statute_grounding_enabled())
        for v in ("1", "true", "YES", "on"):
            with patch.dict(os.environ, {"DIGINYAYA_STATUTE_GROUNDING": v}):
                self.assertTrue(sr.statute_grounding_enabled(), v)
        with patch.dict(os.environ, {"DIGINYAYA_STATUTE_GROUNDING": "0"}):
            self.assertFalse(sr.statute_grounding_enabled())


class TestPipeline(unittest.TestCase):
    def _run(self, flag: str | None):
        env = {"DIGINYAYA_STATUTE_GROUNDING": flag} if flag is not None else {}
        with patch.dict(os.environ, env, clear=False):
            if flag is None:
                os.environ.pop("DIGINYAYA_STATUTE_GROUNDING", None)
            ctx = _ctx()
            list(graph.run_pipeline(ctx))
            list(graph.run_resolution(ctx, via_mediation=True))
        return ctx

    def test_flag_off_changes_nothing(self):
        ctx = self._run(None)
        self.assertEqual(ctx.research.statutes, [])
        self.assertEqual(ctx.resolution.cited_statutes, [])
        self.assertFalse(any("considered with reference to" in f for f in ctx.resolution.findings))

    def test_flag_on_cites_only_retrieved_provisions(self):
        ctx = self._run("1")
        self.assertTrue(ctx.research.statutes)
        retrieved = {f"{s.act}, section {s.section}" for s in ctx.research.statutes}
        cited = {c["citation"] for c in ctx.resolution.cited_statutes}
        self.assertTrue(cited and cited <= retrieved)
        self.assertTrue(any("considered with reference to" in f for f in ctx.resolution.findings))
        # the deterministic order is untouched by grounding: same relief amount with the flag on and off
        self.assertEqual(ctx.resolution.relief_amount, self._run(None).resolution.relief_amount)

    def test_llm_findings_citing_an_unretrieved_section_are_rejected(self):
        with patch.dict(os.environ, {"DIGINYAYA_STATUTE_GROUNDING": "1"}):
            ctx = _ctx()
            list(graph.run_pipeline(ctx))
            from app.agents import resolution
            result = resolution.finalize(ctx, "1. The respondent is liable under Section 420 of the Penal Code.")
        doc = result.output
        self.assertFalse(any("420" in f for f in doc.findings))
        self.assertEqual(doc.engine, "scripted")  # nothing survived the guard, so it fell back to the scripted findings



class TestSplitFindings(unittest.TestCase):
    """_split turns the model's findings text into sentences; a single numbered line used to yield a stray '1.'."""

    def test_single_numbered_line_has_no_stray_marker(self):
        from app.agents.resolution import _split
        self.assertEqual(_split("1. The respondent is liable under the contract."),
                         ["The respondent is liable under the contract."])

    def test_multi_line_numbered_list_is_unchanged(self):
        from app.agents.resolution import _split
        self.assertEqual(_split("1. First finding.\n2. Second finding.\n3) Third finding."),
                         ["First finding.", "Second finding.", "Third finding."])

    def test_one_paragraph_is_split_into_sentences_and_capped_at_five(self):
        from app.agents.resolution import _split
        out = _split("A. B. C. D. E. F. G.")
        self.assertLessEqual(len(out), 5)
        self.assertTrue(all(not x.rstrip(".").isdigit() for x in out))


if __name__ == "__main__":
    unittest.main()
