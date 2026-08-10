"""Unit tests for app.agents.preliminary_review -- run against an in-memory
sqlite DB (DIGINYAYA_DB=":memory:") with DIGINYAYA_USE_LLM=0, so these test
the no-LLM fallback paths (no text extracted / LLM unavailable) deterministically,
same convention as tests/test_discrepancy_agent.py. The per-document LLM
relevance call itself (document_relevance's happy path) is proven separately
by a live manual run against Sarvam, not re-mocked here.

Run with (from backend/):
    python -m unittest tests.test_preliminary_review -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["DIGINYAYA_DB"] = ":memory:"
os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.agents import preliminary_review  # noqa: E402


def _make_case(case_id: str, dispute_type: str = "money_recovery", description: str = "") -> None:
    db.save_case({
        "case_id": case_id, "owner_id": "u1", "claim_amount": 50000.0,
        "dispute_type": dispute_type, "description": description,
    })


def _make_document(doc_id: str, case_id: str, cleaned_text: str, *, extraction_status: str = "complete") -> str:
    full_id = f"{case_id}-{doc_id}"
    db.insert_document({
        "id": full_id, "case_id": case_id, "original_filename": f"{doc_id}.pdf",
        "extraction_status": extraction_status, "cleaned_text": cleaned_text,
        "raw_ocr_text": cleaned_text,
    })
    return full_id


class TestPreliminaryReview(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_no_documents_recommends_expected_evidence_for_dispute_type(self):
        case_id = "DN-PRELIM-EMPTY"
        _make_case(case_id, dispute_type="money_recovery")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertEqual(result["documents"], [])
        self.assertIn("bank transfer record", result["case_strength_note"])

    def test_document_with_no_extracted_text_marked_uncertain_not_irrelevant(self):
        case_id = "DN-PRELIM-BLANK"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertEqual(len(result["documents"]), 1)
        self.assertIsNone(result["documents"][0]["relevant"])
        self.assertIn("No readable text", result["documents"][0]["note"])
        # Authenticity is a *separate* claim from relevance -- no text means
        # no assessment either way, not a fabrication accusation.
        self.assertIsNone(result["documents"][0]["authenticity_flag"])

    def test_llm_unavailable_authenticity_also_defaults_to_uncertain(self):
        case_id = "DN-PRELIM-NOLLM-AUTH"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "Some document text with actual content in it.")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertIsNone(result["documents"][0]["authenticity_flag"])
        self.assertEqual(result["documents"][0]["authenticity_note"], "")

    def test_llm_unavailable_falls_back_to_uncertain_not_false_accusation(self):
        # DIGINYAYA_USE_LLM=0 makes llm.generate_json return falsy -- this
        # is the "couldn't assess" path, which must stay None (not False),
        # since a wrongly-flagged "irrelevant" would be a false accusation.
        case_id = "DN-PRELIM-NOLLM"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "Some document text with actual content in it.")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertEqual(len(result["documents"]), 1)
        self.assertIsNone(result["documents"][0]["relevant"])
        self.assertIn("Couldn't assess", result["documents"][0]["note"])

    def test_pending_extraction_reported_separately_from_relevance(self):
        case_id = "DN-PRELIM-PENDING"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "", extraction_status="pending")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertEqual(result["documents"], [])
        self.assertIn("still being processed", result["case_strength_note"])

    def test_failed_extraction_reported_as_failed_not_still_processing(self):
        # Regression test: a permanently-failed extraction (e.g. a corrupt
        # upload) must not be told to the claimant as "check back in a
        # moment" -- it never will complete. Found while verifying the
        # winnability fix above, via a real live-server run against a
        # deliberately malformed PDF.
        case_id = "DN-PRELIM-FAILED"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "", extraction_status="failed")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertEqual(result["documents"], [])
        self.assertNotIn("still being processed", result["case_strength_note"])
        self.assertIn("couldn't be processed", result["case_strength_note"])

    def test_failed_and_pending_together_both_mentioned(self):
        case_id = "DN-PRELIM-MIXED"
        _make_case(case_id)
        _make_document("DOC-1", case_id, "", extraction_status="failed")
        _make_document("DOC-2", case_id, "", extraction_status="pending")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertIn("still being processed", result["case_strength_note"])
        self.assertIn("couldn't be processed", result["case_strength_note"])

    def test_unknown_dispute_type_falls_back_to_generic_expected_evidence(self):
        case_id = "DN-PRELIM-GENERIC"
        _make_case(case_id, dispute_type="not_a_real_type")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertIn("documentary proof supporting the claim", result["case_strength_note"])

    def test_vague_description_flagged_as_not_detailed_enough(self):
        case_id = "DN-PRELIM-VAGUE"
        _make_case(case_id, description="he robbed me of my money i dont have any proof but i know it")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertFalse(result["description_review"]["detailed_enough"])
        self.assertTrue(result["description_review"]["note"])

    def test_specific_description_with_amount_and_dates_passes(self):
        case_id = "DN-PRELIM-SPECIFIC"
        _make_case(
            case_id,
            description=(
                "I lent Rs 4,00,000 to the respondent via bank transfer on 10 Jan 2024 as an "
                "interest-free personal loan, repayable within 6 months. He has repaid nothing "
                "and stopped responding to messages since March 2024."
            ),
        )
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertTrue(result["description_review"]["detailed_enough"])

    def test_winnability_score_present_and_low_for_empty_case(self):
        case_id = "DN-PRELIM-WINEMPTY"
        _make_case(case_id, description="he robbed me, no proof but i know it")
        result = preliminary_review.run_preliminary_review(case_id)
        self.assertIn("score", result["winnability"])
        self.assertIn("label", result["winnability"])
        self.assertLessEqual(result["winnability"]["score"], 40)
        self.assertEqual(result["winnability"]["label"], "weak")

    def test_winnability_score_higher_with_relevant_evidence_and_detail(self):
        case_id = "DN-PRELIM-WINSTRONG"
        _make_case(
            case_id,
            description=(
                "I lent Rs 4,00,000 to the respondent via bank transfer on 10 Jan 2024 as an "
                "interest-free personal loan, repayable within 6 months. He has repaid nothing."
            ),
        )
        _make_document("DOC-1", case_id, "Bank transfer receipt for Rs 4,00,000 dated 10-Jan-2024.")
        # DIGINYAYA_USE_LLM=0 for this module -> document relevance is
        # "uncertain" (None), not "relevant" (True), since that check also
        # needs the LLM -- so the scripted winnability fallback here can only
        # credit the longer description, not the (unassessed) document.
        result = preliminary_review.run_preliminary_review(case_id)
        empty_case_id = "DN-PRELIM-WINEMPTY2"
        _make_case(empty_case_id, description="he robbed me, no proof but i know it")
        empty_result = preliminary_review.run_preliminary_review(empty_case_id)
        self.assertGreater(result["winnability"]["score"], empty_result["winnability"]["score"])


class TestDocumentRelevancePartyMatching(unittest.TestCase):
    """document_relevance's party-matching instruction -- regression coverage
    for a real, confirmed bug (2026-08-06): a genuine Groww/mutual-fund
    payment receipt was accepted as "relevant" evidence for an unrelated
    claim against a different named person, because the prompt only checked
    whether the document was topically the right KIND of document, never
    whether its named parties actually matched the dispute's parties."""

    def _run_with_mock(self, llm_response, **kwargs):
        captured = {}

        def fake_generate_json(prompt, **_kwargs):
            captured["prompt"] = prompt
            return llm_response

        doc = {"id": "doc-1", "original_filename": "receipt.pdf", "cleaned_text": "some receipt text"}
        with patch("app.agents.preliminary_review.llm.generate_json", side_effect=fake_generate_json):
            result = preliminary_review.document_relevance("a claim", "money_recovery", doc, **kwargs)
        return result, captured["prompt"]

    def test_no_party_names_omits_party_check_from_prompt(self):
        # Backward-compatible: callers that don't have party names yet
        # (or tests) must not get a broken/empty instruction injected.
        _, prompt = self._run_with_mock({"relevant": True})
        self.assertNotIn("This dispute is specifically", prompt)

    def test_party_names_present_adds_explicit_check_to_prompt(self):
        _, prompt = self._run_with_mock(
            {"relevant": True}, claimant_name="Shivaum", respondent_name="Chandershekhar Sharma",
        )
        self.assertIn("Shivaum", prompt)
        self.assertIn("Chandershekhar Sharma", prompt)
        self.assertIn("does NOT support this specific claim", prompt)

    def test_llm_correctly_flags_mismatched_third_party(self):
        # Simulates the model doing its job once the party-check instruction
        # is present -- confirms the plumbing (not the model itself, already
        # verified live) passes the reason through untouched.
        result, _ = self._run_with_mock(
            {"relevant": False, "looks_like": "a bank transfer receipt",
             "note": "The document shows a payment to Mutual Funds Ncl, not to Chandershekhar Sharma."},
            claimant_name="Shivaum", respondent_name="Chandershekhar Sharma",
        )
        self.assertFalse(result["relevant"])
        self.assertIn("Mutual Funds Ncl", result["note"])


class TestDocSummaryLine(unittest.TestCase):
    """_doc_summary_line feeds the winnability LLM prompt -- these pin its
    output shape for each relevance state, since a regression here (e.g.
    dropping back to a bare relevant/not-relevant boolean) would silently
    reintroduce the "single strong document scores as weak" bug reported by
    a real user (2026-08-05): the model can't judge evidence QUALITY if the
    prompt never tells it what the evidence actually is."""

    def test_relevant_document_includes_looks_like_and_note(self):
        line = preliminary_review._doc_summary_line({
            "filename": "receipt.pdf", "relevant": True,
            "looks_like": "an Amazon order confirmation", "note": "Matches claimed amount and date.",
            "authenticity_flag": False, "authenticity_note": "",
        })
        self.assertIn("supports the claim", line)
        self.assertIn("an Amazon order confirmation", line)
        self.assertIn("Matches claimed amount and date.", line)

    def test_relevant_document_with_authenticity_flag_includes_concern(self):
        line = preliminary_review._doc_summary_line({
            "filename": "receipt.pdf", "relevant": True,
            "looks_like": "a receipt", "note": "",
            "authenticity_flag": True, "authenticity_note": "Contains placeholder text.",
        })
        self.assertIn("AUTHENTICITY CONCERN", line)
        self.assertIn("Contains placeholder text.", line)

    def test_irrelevant_document_says_so_with_looks_like(self):
        line = preliminary_review._doc_summary_line({
            "filename": "resume.pdf", "relevant": False,
            "looks_like": "a resume", "note": "", "authenticity_flag": False, "authenticity_note": "",
        })
        self.assertIn("does NOT appear relevant", line)
        self.assertIn("a resume", line)

    def test_unassessed_document_says_not_yet_assessable(self):
        line = preliminary_review._doc_summary_line({
            "filename": "scan.pdf", "relevant": None,
            "looks_like": None, "note": "", "authenticity_flag": None, "authenticity_note": "",
        })
        self.assertIn("not yet assessable", line)


class TestWinnabilityPromptContent(unittest.TestCase):
    """The LLM-path prompt itself -- verifies the fix's actual content
    reaches the model, mocking generate_json the same way
    tests/test_mediation_agent.py does for its LLM-path tests."""

    def _run_with_mock(self, doc_reviews, pending_count=0):
        captured = {}

        def fake_generate_json(prompt, **kwargs):
            captured["prompt"] = prompt
            return {"score": 80, "label": "strong", "reasons": ["specific matching evidence"]}

        with patch("app.agents.preliminary_review.llm.is_available", return_value=True), \
             patch("app.agents.preliminary_review.llm.generate_json", side_effect=fake_generate_json):
            result = preliminary_review._assess_winnability(
                "A detailed claim description.", "consumer_dispute", doc_reviews, len(doc_reviews), pending_count,
            )
        return result, captured["prompt"]

    def test_prompt_instructs_quality_over_count(self):
        _, prompt = self._run_with_mock([])
        self.assertIn("NOT by how many pieces", prompt)

    def test_prompt_carries_document_quality_signal(self):
        doc_reviews = [{
            "filename": "receipt.pdf", "relevant": True,
            "looks_like": "an Amazon order confirmation", "note": "Matches the claimed amount exactly.",
            "authenticity_flag": False, "authenticity_note": "",
        }]
        _, prompt = self._run_with_mock(doc_reviews)
        self.assertIn("an Amazon order confirmation", prompt)
        self.assertIn("Matches the claimed amount exactly.", prompt)

    def test_prompt_notes_pending_files_instead_of_claiming_no_evidence(self):
        # Regression test for the exact reported bug: a file still being
        # extracted must not make the prompt flatly claim "no evidence
        # submitted" with no qualification.
        _, prompt = self._run_with_mock([], pending_count=1)
        self.assertNotIn("no evidence submitted\n", prompt)  # unqualified phrasing must not appear
        self.assertIn("1 file(s) still being processed", prompt)
        self.assertIn('do not treat this as "no evidence"', prompt)

    def test_llm_result_is_used_when_available(self):
        result, _ = self._run_with_mock([])
        self.assertEqual(result["score"], 80)
        self.assertEqual(result["label"], "strong")


class TestSuggestDisputeType(unittest.TestCase):
    """suggest_dispute_type -- live, as-you-type nudge when a description
    sounds like it belongs to a different category than the one selected.
    Never blocks filing; always returns None rather than raising."""

    _LONG_CHEQUE_DESC = (
        "Suresh Traders issued a cheque for Rs 2,50,000 to settle a debt, and it bounced "
        "due to insufficient funds. I sent a legal demand notice under Section 138."
    )

    def test_too_short_returns_none_without_calling_llm(self):
        with patch("app.agents.preliminary_review.llm.is_available", return_value=True), \
             patch("app.agents.preliminary_review.llm.generate_json") as mock_generate:
            result = preliminary_review.suggest_dispute_type("too short", "consumer_dispute")
        self.assertIsNone(result)
        mock_generate.assert_not_called()

    def test_llm_unavailable_returns_none(self):
        result = preliminary_review.suggest_dispute_type(self._LONG_CHEQUE_DESC, "consumer_dispute")
        self.assertIsNone(result)

    def test_clear_mismatch_returns_suggestion(self):
        with patch("app.agents.preliminary_review.llm.is_available", return_value=True), \
             patch("app.agents.preliminary_review.llm.generate_json", return_value={
                 "mismatch": True, "suggested_type_id": "cheque_bounce",
                 "reason": "Describes a bounced cheque and a Section 138 notice.",
             }):
            result = preliminary_review.suggest_dispute_type(self._LONG_CHEQUE_DESC, "consumer_dispute")
        self.assertIsNotNone(result)
        self.assertEqual(result["suggested_type_id"], "cheque_bounce")
        self.assertEqual(result["suggested_type_label"], "Cheque Bounce")
        self.assertTrue(result["reason"])

    def test_no_mismatch_returns_none(self):
        with patch("app.agents.preliminary_review.llm.is_available", return_value=True), \
             patch("app.agents.preliminary_review.llm.generate_json", return_value={"mismatch": False}):
            result = preliminary_review.suggest_dispute_type(self._LONG_CHEQUE_DESC, "cheque_bounce")
        self.assertIsNone(result)

    def test_invalid_suggested_id_from_llm_returns_none(self):
        # Defensive: don't trust an out-of-vocabulary id the model might
        # hallucinate -- never surface a suggestion that can't actually be
        # navigated to.
        with patch("app.agents.preliminary_review.llm.is_available", return_value=True), \
             patch("app.agents.preliminary_review.llm.generate_json", return_value={
                 "mismatch": True, "suggested_type_id": "not_a_real_category", "reason": "x",
             }):
            result = preliminary_review.suggest_dispute_type(self._LONG_CHEQUE_DESC, "consumer_dispute")
        self.assertIsNone(result)

    def test_prompt_never_offers_the_currently_selected_type_as_an_option(self):
        captured = {}

        def fake_generate_json(prompt, **kwargs):
            captured["prompt"] = prompt
            return {"mismatch": False}

        with patch("app.agents.preliminary_review.llm.is_available", return_value=True), \
             patch("app.agents.preliminary_review.llm.generate_json", side_effect=fake_generate_json):
            preliminary_review.suggest_dispute_type(self._LONG_CHEQUE_DESC, "cheque_bounce")
        self.assertNotIn('"cheque_bounce"', captured["prompt"].split("Other available categories:")[1])


if __name__ == "__main__":
    unittest.main()
