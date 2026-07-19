"""Discrepancy-detection over a case's uploaded documents.

Deliberately NOT `agents.base.AgentResult`/`run(ctx: CaseContext)` -- that
contract is inherently blackboard-shaped (reads/writes the single-dispute
CaseContext the 5-agent pipeline shares), and this feature operates over N
documents in their own table, fully decoupled from CaseContext/graph.py (no
existing agent reads file content, only evidence_count/evidence_kinds).
Instead this exposes a generator in the same node-style as graph.py's nodes:

    run_discrepancy_check(case_id) -> Iterator[dict]

so app.jobs._pump() can consume it exactly like graph.run_pipeline().

Detection is a "scripted baseline + LLM enrichment + deterministic
narrowing" pipeline, mirroring mediation.py/resolution.py's pattern:
  1. Scripted, high-confidence mechanical checks (missing text, no
     signature marker on an agreement-shaped document) -- decided here, no
     LLM needed, so discrepancy-check still does something useful with the
     LLM unavailable.
  2. An LLM pass proposes candidate discrepancies (date/amount conflicts,
     name inconsistencies) given each document's cleaned text plus the
     scripted date/amount hints.
  3. Grounding (app.documents.grounding.verify_discrepancy_sources) drops
     any candidate not backed by real extracted text -- same shape as
     app.rag.index.verify_citations for precedent citations.
  4. Name-inconsistency candidates get a stdlib difflib similarity check to
     downgrade likely OCR noise rather than silently dropping it.
"""

from __future__ import annotations

import difflib
import uuid
from typing import Any

from .. import llm
from ..core import safety_gate
from ..core.events import make_event
from .. import db
from ..documents import grounding
from .base import wrap_untrusted
from . import nlp

_VALID_TYPES = {"date_conflict", "amount_mismatch", "name_inconsistency", "missing_element", "other"}

# Heuristic markers for the scripted "missing signature" check: a document
# that looks like an agreement/contract (uses this vocabulary) but has no
# signature-related marker anywhere is worth flagging -- not proof positive
# (short excerpts or a signature page dropped during OCR both look the
# same), which is exactly why this is scored as a mechanical-certainty
# candidate, not a definitive claim.
_AGREEMENT_MARKERS = ("agreement", "contract", "hereby", "witnesseth", "parties agree", "terms and conditions")
_SIGNATURE_MARKERS = ("signature", "signed by", "signed on", "s/d-", "sd/-", "witness")

_MIN_TEXT_CHARS = 30  # below this, a document is treated as effectively blank


def _scripted_candidates(complete_docs: list[dict]) -> list[dict[str, Any]]:
    """High-confidence mechanical checks that don't need an LLM. Each
    candidate already carries a "certain" agreement_component (0.8, per the
    confidence formula below) since these are direct text-presence checks,
    not judgment calls."""
    candidates = []
    for doc in complete_docs:
        text = (doc.get("cleaned_text") or "").strip()
        lowered = text.lower()

        if len(text) < _MIN_TEXT_CHARS:
            candidates.append({
                "discrepancy_type": "missing_element",
                "document_ids": [doc["id"]],
                "explanation": "Document contains little or no extractable text -- possibly a blank page, "
                               "missing content, or a failed/low-quality scan.",
                "source_snippet": None,
                "severity": "medium",
                "_scripted": True,
            })
            continue  # nothing further to check on an effectively-blank document

        if any(m in lowered for m in _AGREEMENT_MARKERS) and not any(m in lowered for m in _SIGNATURE_MARKERS):
            candidates.append({
                "discrepancy_type": "missing_element",
                "document_ids": [doc["id"]],
                "explanation": "Document reads as an agreement/contract but no signature block or "
                               "witness marker was found in the extracted text.",
                "source_snippet": None,
                "severity": "medium",
                "_scripted": True,
            })

        if not nlp.extract_dates(text):
            candidates.append({
                "discrepancy_type": "missing_element",
                "document_ids": [doc["id"]],
                "explanation": "No date could be found anywhere in this document.",
                "source_snippet": None,
                "severity": "low",
                "_scripted": True,
            })
    return candidates


def _llm_candidates(complete_docs: list[dict]) -> list[dict[str, Any]]:
    if not complete_docs or len(complete_docs) < 1:
        return []
    hints = []
    doc_sections = []
    for doc in complete_docs:
        text = doc.get("cleaned_text") or ""
        dates = nlp.extract_dates(text)
        amounts = nlp.extract_amounts(text)
        hints.append(f"{doc['id']} ({doc['original_filename']}): dates seen={dates or 'none'}, amounts seen={amounts or 'none'}")
        doc_sections.append(wrap_untrusted(f"DOCUMENT_{doc['id']}", text[:4000]))

    schema = (
        '{"discrepancies": [{"discrepancy_type": "date_conflict|amount_mismatch|name_inconsistency|other", '
        '"document_ids": ["<doc id>", ...], "explanation": "<=40 words", '
        '"source_snippet": "<a short exact substring from one of the cited documents>", '
        '"compared_values": ["<value A>", "<value B>"] }]}'
    )
    prompt = (
        "You are checking a legal case's supporting documents for INTERNAL inconsistencies -- conflicting "
        "dates, mismatched amounts, or inconsistently-referenced party names across or within these documents. "
        "Only report discrepancies you can point to specific text for. Do not invent a discrepancy that isn't "
        "actually present. If nothing is inconsistent, return an empty list. Return JSON only, matching this "
        f"schema: {schema}\n\n"
        f"Extracted signal hints (for reference, not exhaustive):\n" + "\n".join(hints) + "\n\n"
        + "\n".join(doc_sections)
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=700)
    if not data:
        return []
    raw = data.get("discrepancies")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        dtype = item.get("discrepancy_type") if item.get("discrepancy_type") in _VALID_TYPES else "other"
        doc_ids = item.get("document_ids") or []
        if not isinstance(doc_ids, list) or not doc_ids:
            continue
        out.append({
            "discrepancy_type": dtype,
            "document_ids": doc_ids,
            "explanation": str(item.get("explanation") or "").strip() or "Potential inconsistency detected.",
            "source_snippet": item.get("source_snippet"),
            "compared_values": item.get("compared_values"),
            "_scripted": False,
        })
    return out


def _name_similarity_downgrade(candidate: dict) -> dict:
    """difflib-based OCR-noise guard: two very similar name strings are more
    likely an OCR variant of the same name than a substantive inconsistency
    -- downgrade rather than drop, so a human reviewer still sees it."""
    if candidate["discrepancy_type"] != "name_inconsistency":
        return candidate
    values = candidate.get("compared_values")
    if not values or len(values) < 2:
        return candidate
    ratio = difflib.SequenceMatcher(None, str(values[0]).lower(), str(values[1]).lower()).ratio()
    if ratio > 0.85:
        candidate["severity"] = "low"
        candidate["explanation"] = (candidate["explanation"] + " (possible OCR variation, not confirmed)").strip()
    return candidate


def _infer_severity(candidate: dict, claim_amount: float) -> str:
    if "severity" in candidate:  # scripted candidates already carry one
        return candidate["severity"]
    dtype = candidate["discrepancy_type"]
    if dtype == "amount_mismatch":
        values = candidate.get("compared_values") or []
        try:
            nums = [float(v) for v in values]
            gap = abs(nums[0] - nums[1]) if len(nums) >= 2 else 0.0
            ratio = gap / claim_amount if claim_amount else 0.0
            return "high" if ratio >= 0.2 else "medium" if ratio > 0 else "low"
        except (TypeError, ValueError, IndexError):
            return "medium"
    if dtype == "date_conflict":
        return "medium"
    if dtype == "name_inconsistency":
        return "medium"
    return "low"


def _confidence(candidate: dict, documents_by_id: dict[str, dict]) -> float:
    """0.6*ocr_component + 0.4*agreement_component -- a small, bespoke
    formula, deliberately NOT app.core.confidence.composite() (that's wired
    specifically to the 5-agent resolution pipeline's ingestion/retrieval/
    schema/citation weights, which have no equivalent here)."""
    ocr_scores = []
    for doc_id in candidate["document_ids"]:
        doc = documents_by_id.get(doc_id)
        if doc is None:
            continue
        ocr_scores.append(1.0 if not doc.get("is_scanned") else float(doc.get("ocr_confidence") or 0.0))
    ocr_component = sum(ocr_scores) / len(ocr_scores) if ocr_scores else 0.5

    agreement_component = 0.8 if candidate.get("_scripted") else 0.6
    return round(0.6 * ocr_component + 0.4 * agreement_component, 3)


def run_discrepancy_check(case_id: str):
    """Generator, same node-style as graph.py's nodes. Persists each
    discrepancy via db.insert_discrepancy as it's produced -- unlike the
    main pipeline there's no single end-of-run context object to snapshot,
    each discrepancy is its own row."""
    docs = db.list_documents(case_id)
    complete_docs = [d for d in docs if d.get("extraction_status") == "complete"]
    if not complete_docs:
        yield make_event(
            "discrepancy_check_done", agent="discrepancy", status="done",
            detail="No fully-extracted documents to check yet.", payload={"found": 0},
        )
        return

    case = db.get_case(case_id) or {}
    claim_amount = float(case.get("claim_amount") or 0.0)
    documents_by_id = {d["id"]: d for d in complete_docs}

    candidates = _scripted_candidates(complete_docs)
    llm_candidates = _llm_candidates(complete_docs)
    llm_candidates = grounding.verify_discrepancy_sources(llm_candidates, documents_by_id)
    candidates.extend(_name_similarity_downgrade(c) for c in llm_candidates)

    found = 0
    for candidate in candidates:
        severity = _infer_severity(candidate, claim_amount)
        confidence = _confidence(candidate, documents_by_id)
        flagged = severity == "high" or confidence < safety_gate.CONFIDENCE_FLOOR
        disc_id = f"DISC-{uuid.uuid4().hex[:12]}"
        row = {
            "id": disc_id,
            "case_id": case_id,
            "document_ids": candidate["document_ids"],
            "discrepancy_type": candidate["discrepancy_type"],
            "severity": severity,
            "confidence_score": confidence,
            "explanation": candidate["explanation"],
            "source_location": ",".join(candidate["document_ids"]),
            "flagged_for_review": flagged,
        }
        db.insert_discrepancy(row)
        found += 1
        yield make_event(
            "discrepancy_found", agent="discrepancy", status="done",
            detail=candidate["explanation"],
            payload={**row, "id": disc_id},
        )

    yield make_event(
        "discrepancy_check_done", agent="discrepancy", status="done",
        detail=f"Discrepancy check complete: {found} item(s) flagged.",
        payload={"found": found},
    )
