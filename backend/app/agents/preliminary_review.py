"""Preliminary, pre-filing review of a draft case's evidence.

Deliberately NOT part of the 5-agent pipeline (app.core.graph) -- this runs
on a case still in "draft" status, before the respondent has even been
notified, purely to help the claimant fix an obviously missing or irrelevant
piece of evidence before spending the respondent's 72-hour window. Nothing
here is authoritative or binding; the real adjudication is the actual
pipeline, which only runs after the respondent replies (or the window
lapses). Re-runnable any number of times before the claimant files for real,
so this is a plain function, not a graph node/event generator.
"""

from __future__ import annotations

from .. import db, llm
from . import nlp
from .base import wrap_untrusted

# What kind of documentary proof a claim of this kind typically needs, in
# plain language a citizen (not a lawyer) would recognise -- used both to
# give the LLM per-document call some context and for the no-documents/
# nothing-relevant-yet fallback message below.
_EXPECTED_EVIDENCE_BY_TYPE = {
    "consumer_dispute": "a receipt, invoice, order confirmation, or payment record for what you paid",
    "money_recovery": "proof the money actually changed hands -- a bank transfer record, a signed loan agreement, a promissory note, or a receipt",
    "contract_breach": "the written contract or agreement itself, plus anything showing the other side didn't do what it promised",
    "cheque_bounce": "the dishonoured cheque, the bank's return memo, and the demand notice you sent",
}


def _document_relevance(case_description: str, dispute_type: str, doc: dict) -> dict:
    """Ask the LLM whether one uploaded document plausibly supports this
    claim, or is clearly something else entirely (a resume, an unrelated
    photo, a blank scan). Falls back to "can't tell yet" -- never a false
    accusation -- when there's no text or the LLM is unavailable."""
    text = (doc.get("cleaned_text") or "").strip()
    base = {"document_id": doc["id"], "filename": doc.get("original_filename")}
    if not text:
        return {
            **base,
            "relevant": None,
            "looks_like": None,
            "note": "No readable text could be extracted from this file yet.",
        }

    schema = (
        '{"relevant": <true|false>, '
        '"looks_like": "<=6 words naming what this document actually appears to be, e.g. '
        '\'a resume\', \'a bank transfer receipt\', \'a rental agreement\'>", '
        '"note": "<=25 words, plain language, no legal jargon>"}'
    )
    prompt = (
        f"A citizen filed a {nlp.dispute_label(dispute_type)} describing: "
        f"{wrap_untrusted('CLAIM', case_description)}\n\n"
        f"They uploaded this as supporting evidence:\n{wrap_untrusted('DOCUMENT', text[:3000])}\n\n"
        "Does this document plausibly support THIS claim, or is it something unrelated entirely "
        "(e.g. a resume, an unrelated photo, a document for a different matter)? Return JSON only, "
        f"matching this schema: {schema}"
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=200)
    if not data:
        return {
            **base,
            "relevant": None,
            "looks_like": None,
            "note": "Couldn't assess this document right now -- it will still be reviewed properly once you file.",
        }
    return {
        **base,
        "relevant": bool(data.get("relevant")),
        "looks_like": str(data.get("looks_like") or "").strip() or None,
        "note": str(data.get("note") or "").strip(),
    }


def run_preliminary_review(case_id: str) -> dict:
    case = db.get_case(case_id) or {}
    dispute_type = case.get("dispute_type", "consumer_dispute")
    description = case.get("description", "")
    docs = db.list_documents(case_id)
    complete_docs = [d for d in docs if d.get("extraction_status") == "complete"]

    doc_reviews = [_document_relevance(description, dispute_type, d) for d in complete_docs]
    relevant_count = sum(1 for r in doc_reviews if r["relevant"] is True)

    expected = _EXPECTED_EVIDENCE_BY_TYPE.get(dispute_type, "documentary proof supporting the claim")
    pending_count = len(docs) - len(complete_docs)

    if not docs:
        strength_note = (
            f"You haven't attached any evidence yet. Based on a preliminary look, cases like this "
            f"typically don't succeed without it -- you'll usually need {expected}. Consider adding "
            "that before filing."
        )
    elif relevant_count == 0 and pending_count == 0:
        strength_note = (
            f"Based on a preliminary look, what you've uploaded so far doesn't look like it supports "
            f"this specific claim. Cases like this typically need {expected} -- consider adding that "
            "before filing."
        )
    elif pending_count > 0:
        strength_note = (
            f"{pending_count} file(s) are still being processed -- check back in a moment for a "
            "complete picture."
        )
    else:
        strength_note = (
            f"You have {relevant_count} relevant item(s) on record. This is only a preliminary, "
            "non-binding look -- the real review happens once the respondent replies."
        )

    return {"documents": doc_reviews, "case_strength_note": strength_note}
