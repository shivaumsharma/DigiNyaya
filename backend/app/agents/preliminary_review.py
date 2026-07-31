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
    photo, a blank scan) -- and, separately, whether the document's TEXT
    shows any obvious internal red flag (placeholder-like content,
    self-contradictory dates/amounts, a template that was never filled in).

    This is NOT forgery/tampering detection -- it only ever reads the OCR'd
    or extracted text, never the original image/PDF, so it cannot see a
    doctored image, an altered scan, or a forged signature. It exists only
    to catch the crude, textually-obvious cases. Defaults to "no concern
    raised" (None/False), never a false accusation, when the model can't
    point to something specific -- see authenticity_flag below.
    """
    text = (doc.get("cleaned_text") or "").strip()
    base = {"document_id": doc["id"], "filename": doc.get("original_filename")}
    if not text:
        return {
            **base,
            "relevant": None,
            "looks_like": None,
            "note": "No readable text could be extracted from this file yet.",
            "authenticity_flag": None,
            "authenticity_note": "",
        }

    schema = (
        '{"relevant": <true|false>, '
        '"looks_like": "<=6 words naming what this document actually appears to be, e.g. '
        '\'a resume\', \'a bank transfer receipt\', \'a rental agreement\'>", '
        '"note": "<=25 words, plain language, no legal jargon>", '
        '"authenticity_flag": <true|false>, '
        '"authenticity_note": "<=25 words -- REQUIRED and specific if authenticity_flag is true, '
        'else empty string>"}'
    )
    prompt = (
        f"A citizen filed a {nlp.dispute_label(dispute_type)} describing: "
        f"{wrap_untrusted('CLAIM', case_description)}\n\n"
        f"They uploaded this as supporting evidence:\n{wrap_untrusted('DOCUMENT', text[:3000])}\n\n"
        "Does this document plausibly support THIS claim, or is it something unrelated entirely "
        "(e.g. a resume, an unrelated photo, a document for a different matter)?\n\n"
        "Separately: does the TEXT of this document show any CLEAR, SPECIFIC sign of being fabricated "
        "or inconsistent -- e.g. placeholder/lorem-ipsum-like content, a template that was never filled "
        "in, or dates/amounts that contradict each other within the same document? Only set "
        "authenticity_flag to true if you can name the specific issue. Being informal, short, or simply "
        "worded is NOT a red flag -- do not flag genuine-looking documents just because they're plain. "
        "You are only reading extracted text, not the original image, so you cannot detect a doctored "
        "photo or an altered scan -- do not claim to. Return JSON only, "
        f"matching this schema: {schema}"
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=250)
    if not data:
        return {
            **base,
            "relevant": None,
            "looks_like": None,
            "note": "Couldn't assess this document right now -- it will still be reviewed properly once you file.",
            "authenticity_flag": None,
            "authenticity_note": "",
        }
    return {
        **base,
        "relevant": bool(data.get("relevant")),
        "looks_like": str(data.get("looks_like") or "").strip() or None,
        "note": str(data.get("note") or "").strip(),
        "authenticity_flag": bool(data.get("authenticity_flag")),
        "authenticity_note": str(data.get("authenticity_note") or "").strip(),
    }


def _scripted_description_note(description: str) -> dict:
    """No-LLM fallback: a length/specificity heuristic. Deliberately
    conservative -- only flags a description as thin when it's genuinely
    short, never invents a judgment about content it can't actually read."""
    text = description.strip()
    has_digit = any(ch.isdigit() for ch in text)
    if len(text) < 60 or not has_digit:
        return {
            "detailed_enough": False,
            "note": (
                "We looked at your description and it may not be detailed enough yet. For a higher "
                "chance of winning, clearly describe what happened, when, and how much money or loss "
                "was involved -- as specifically as possible."
            ),
        }
    return {"detailed_enough": True, "note": ""}


def _assess_description(description: str, dispute_type: str) -> dict:
    """LLM check on the claim narrative itself (distinct from _document_relevance,
    which checks the EVIDENCE) -- catches the case where a claimant writes
    something as thin as "he robbed me, no proof but I know it" with nothing
    concrete for any agent to reason about later."""
    text = description.strip()
    if not text:
        return {"detailed_enough": False, "note": "No description was entered yet."}
    if not llm.is_available():
        return _scripted_description_note(text)

    schema = '{"detailed_enough": <true|false>, "note": "<=35 words, plain language, encouraging tone"}'
    prompt = (
        f"A citizen is filing a {nlp.dispute_label(dispute_type)}. Their description of what happened:\n"
        f"{wrap_untrusted('CLAIM', text)}\n\n"
        "Is this description detailed enough to give a case a real chance (does it say what happened, "
        "roughly when, how much money/loss was involved, and what they want)? If not, say so plainly and "
        "tell them what to add for a higher chance of winning -- do not lecture, just help. Return JSON only, "
        f"matching this schema: {schema}"
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=150)
    if not data:
        return _scripted_description_note(text)
    return {
        "detailed_enough": bool(data.get("detailed_enough")),
        "note": str(data.get("note") or "").strip(),
    }


def _scripted_winnability(description: str, doc_reviews: list[dict], evidence_count: int) -> dict:
    """No-LLM fallback score -- mirrors the shape of the same
    scripted-baseline-then-LLM-enrichment pattern used elsewhere in this
    codebase (see app.agents.discrepancy), just simpler since there's no
    deterministic ground truth to narrow toward here."""
    score = 20
    if len(description.strip()) >= 120:
        score += 15
    relevant = sum(1 for r in doc_reviews if r["relevant"] is True)
    score += min(relevant, 3) * 20
    if evidence_count == 0:
        score = min(score, 25)
    score = max(0, min(100, score))
    label = "weak" if score < 40 else "moderate" if score < 70 else "strong"
    return {"score": score, "label": label, "reasons": []}


def _assess_winnability(description: str, dispute_type: str, doc_reviews: list[dict], evidence_count: int) -> dict:
    """Holistic 0-100 estimate from the description + how the evidence
    assessed above. Advisory only, same as the rest of this module -- shown
    to the claimant before filing AND to the respondent (re-running the same
    check on the filed case) so both sides see the same honest read."""
    if not llm.is_available():
        return _scripted_winnability(description, doc_reviews, evidence_count)

    doc_summary = "; ".join(
        f"{d.get('filename')}: {'supports the claim' if d['relevant'] else 'does not appear relevant' if d['relevant'] is False else 'not yet assessable'}"
        for d in doc_reviews
    ) or "no evidence submitted"

    schema = (
        '{"score": <integer 0-100>, "label": "weak|moderate|strong", '
        '"reasons": [<=3 short strings, <=15 words each, plain language]}'
    )
    prompt = (
        f"Estimate how strong this {nlp.dispute_label(dispute_type)} claim looks on the record so far. "
        f"Description: {wrap_untrusted('CLAIM', description)}\n"
        f"Evidence on file: {doc_summary}\n\n"
        "0 means essentially no case (no evidence, vague description); 100 means a very strong, "
        "well-documented case. Be honest and conservative -- a claim with no evidence or a vague "
        "description should score low regardless of how sympathetic it sounds. Return JSON only, "
        f"matching this schema: {schema}"
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=200)
    if not data:
        return _scripted_winnability(description, doc_reviews, evidence_count)
    try:
        score = max(0, min(100, int(data.get("score", 0))))
    except (TypeError, ValueError):
        return _scripted_winnability(description, doc_reviews, evidence_count)
    label = str(data.get("label") or "").strip() or ("weak" if score < 40 else "moderate" if score < 70 else "strong")
    reasons = [str(r).strip() for r in (data.get("reasons") or []) if str(r).strip()][:3]
    return {"score": score, "label": label, "reasons": reasons}


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

    description_review = _assess_description(description, dispute_type)
    winnability = _assess_winnability(description, dispute_type, doc_reviews, len(docs))

    return {
        "documents": doc_reviews,
        "case_strength_note": strength_note,
        "description_review": description_review,
        "winnability": winnability,
    }
