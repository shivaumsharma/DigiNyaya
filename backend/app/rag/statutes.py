"""Optional statute grounding: deterministic retrieval of the statutory provisions relevant to a dispute.

Why this exists: NyayaRAG (Nigam et al., 2025) found that adding the applicable statutes to the case text
improved both judgment prediction and explanation quality more than adding similar past cases. DigiNyaya
retrieves precedents only. This module adds a small, deterministic statute layer behind a feature flag so the
effect can be measured as an ablation (flag off vs on) instead of assumed.

Deliberately NOT embedding-based and NOT LLM-selected: statute retrieval here is keyword overlap within the
dispute type, so it is reproducible, cheap, and cannot invent a provision. Anything the resolution cites must
come from this retrieved list (see strip_unretrieved_section_mentions).

Default OFF. Set DIGINYAYA_STATUTE_GROUNDING=1 to enable.
"""
from __future__ import annotations

import os
import re

from ..data.loader import load_statutes

_TRUE = {"1", "true", "yes", "on"}
_SECTION_RE = re.compile(r"\b(?:section|sec\.?|s\.)\s*(\d+[A-Za-z]?)", re.IGNORECASE)


def statute_grounding_enabled() -> bool:
    return os.environ.get("DIGINYAYA_STATUTE_GROUNDING", "").strip().lower() in _TRUE


def retrieve_statutes(dispute_type: str, text: str, k: int = 3) -> list[dict]:
    """Top-k provisions for this dispute type, ranked by keyword hits in `text`, then core provisions first,
    then id for a stable order. Always returns the core provisions for the type if nothing matches."""
    haystack = (text or "").lower()
    ranked = []
    for st in load_statutes():
        if dispute_type not in st["dispute_types"]:
            continue
        hits = [kw for kw in st["keywords"] if kw.lower() in haystack]
        ranked.append((len(hits), bool(st.get("core")), st["id"], st, hits))
    ranked.sort(key=lambda t: (-t[0], not t[1], t[2]))
    out = []
    for n_hits, core, _id, st, hits in ranked:
        if len(out) >= k:
            break
        if n_hits == 0 and not core:
            continue
        out.append({"id": st["id"], "act": st["act"], "section": st["section"], "title": st["title"],
                    "summary": st["summary"], "matched_keywords": hits})
    return out


def _section_number(section: str) -> str:
    m = re.match(r"\d+[A-Za-z]?", section)
    return m.group(0).lower() if m else section.lower()


def allowed_section_numbers(statutes: list) -> set[str]:
    """Leading section numbers of the retrieved provisions, e.g. '2(11)' -> '2', '138' -> '138'."""
    return {_section_number(s["section"] if isinstance(s, dict) else s.section) for s in statutes}


def strip_unretrieved_section_mentions(sentences: list[str], statutes: list) -> list[str]:
    """Drop any sentence that cites a 'Section N' that was not among the retrieved provisions, so a model cannot
    introduce a statute the pipeline never retrieved. Sentences that cite no section are untouched."""
    allowed = allowed_section_numbers(statutes)
    kept = []
    for sentence in sentences:
        cited = {m.group(1).lower() for m in _SECTION_RE.finditer(sentence)}
        if cited and not cited <= allowed:
            continue
        kept.append(sentence)
    return kept


def statutes_sentence(statutes: list) -> str:
    """One deterministic sentence naming the provisions considered (built only from retrieved entries)."""
    parts = [f"{(s['act'] if isinstance(s, dict) else s.act)}, section "
             f"{(s['section'] if isinstance(s, dict) else s.section)}" for s in statutes]
    return "The claim was considered with reference to: " + "; ".join(parts) + "."
