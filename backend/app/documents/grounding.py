"""Grounding for discrepancy claims -- mirrors app.rag.index.verify_citations's
shape: a proposed claim only survives if it actually points back to real,
retrievable source material, adapted from (precedent id) to
(document_id, source text snippet) pairs.

Without this, an LLM-proposed discrepancy is a free-floating assertion --
exactly the failure mode app.rag.index.verify_citations already exists to
prevent for precedent citations in resolution.py.
"""

from __future__ import annotations

from typing import Any


def verify_discrepancy_sources(
    candidates: list[dict[str, Any]],
    documents_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop any candidate discrepancy that either (a) cites a document_id not
    present in ``documents_by_id``, or (b) whose claimed source snippet
    can't actually be found in that document's text. Candidates citing
    multiple documents must have every cited document verified.

    ``candidates`` items: {"document_ids": [...], "source_snippet": str, ...}
    ``documents_by_id``: {document_id: {"cleaned_text": str, "raw_ocr_text": str, ...}}
    """
    verified = []
    for candidate in candidates:
        doc_ids = candidate.get("document_ids") or []
        if not doc_ids or any(doc_id not in documents_by_id for doc_id in doc_ids):
            continue
        snippet = (candidate.get("source_snippet") or "").strip()
        if snippet:
            found = any(
                snippet.lower() in (documents_by_id[doc_id].get("cleaned_text") or "").lower()
                or snippet.lower() in (documents_by_id[doc_id].get("raw_ocr_text") or "").lower()
                for doc_id in doc_ids
            )
            if not found:
                continue
        verified.append(candidate)
    return verified
