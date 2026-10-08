"""Loads the bundled precedent corpus and dispute-type metadata."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent


_REQUIRED_FIELDS = (
    "id", "title", "court", "year", "category", "tags", "summary",
    "principle", "outcome", "outcome_detail", "relief_amount_ratio",
    "compliance_days", "citation",
)


def validate_precedent(p: dict) -> tuple[bool, str]:
    """Check a precedent has the fields the agents/retrieval depend on.

    Used by the ingestion pipeline before writing real judgments to the corpus,
    so malformed LLM extractions are dropped rather than silently breaking the
    Research/Mediation agents.
    """
    for f in _REQUIRED_FIELDS:
        if f not in p or p[f] in (None, ""):
            return False, f"missing field '{f}'"
    if not isinstance(p.get("tags"), list) or not p["tags"]:
        return False, "tags must be a non-empty list"
    try:
        ratio = float(p["relief_amount_ratio"])
    except (TypeError, ValueError):
        return False, "relief_amount_ratio must be numeric"
    if not 0.0 <= ratio <= 1.0:
        return False, "relief_amount_ratio out of range [0,1]"
    try:
        int(p["compliance_days"])
    except (TypeError, ValueError):
        return False, "compliance_days must be an integer"
    return True, "ok"


@lru_cache(maxsize=1)
def load_precedents() -> list[dict]:
    with open(_DATA_DIR / "precedents.json", "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # Skip structurally invalid entries so one bad record can't break retrieval.
    valid = []
    for p in data:
        ok, _ = validate_precedent(p)
        if ok:
            valid.append(p)
    return valid


DISPUTE_TYPES = [
    {
        "id": "consumer_dispute",
        "label": "Consumer Dispute",
        "icon": "shopping-bag",
        "tier": 1,
        "tier_label": "Tier 1 — Fully Autonomous AI Resolution",
        "active": True,
        "description": "Defective products, non-delivery, refunds, service deficiency, misleading ads.",
        "examples": [
            "Paid for a product that was never delivered",
            "Received a defective or counterfeit item",
            "Refund denied by an online seller",
        ],
    },
    {
        "id": "money_recovery",
        "label": "Money Recovery / Loan Dispute",
        "icon": "banknote",
        "tier": 2,
        "tier_label": "Tier 2 — AI-Assisted Virtual Judge",
        "active": True,
        "description": "Recovery of lent money, unpaid dues, loan repayment disputes.",
        "examples": [
            "Lent money to someone who is not repaying it",
            "A friend or business contact owes an agreed amount and won't pay",
        ],
    },
    {
        "id": "contract_breach",
        "label": "Simple Contract Breach",
        "icon": "file-text",
        "tier": 2,
        "tier_label": "Tier 2 — AI-Assisted Virtual Judge",
        "active": True,
        "description": "Breach of a clear written agreement with defined obligations.",
        "examples": [
            "The other party didn't fulfil their side of a written agreement",
            "An advance/security deposit was forfeited without justification",
        ],
    },
    {
        "id": "cheque_bounce",
        "label": "Cheque Bounce",
        "icon": "receipt",
        "tier": 2,
        "tier_label": "Tier 2 — AI-Assisted Virtual Judge",
        "active": True,
        "description": "Dishonoured cheque claims under Section 138 NI Act.",
        "examples": [
            "A cheque issued to settle a debt bounced due to insufficient funds",
            "Payment cheque was stopped or dishonoured by the bank",
        ],
    },
]


# --- Civil expansion (phase 1) ------------------------------------------------------------------------------
# These types get their own identity (label, evidence guidance, safety-gate registration, statutes) but
# deliberately BEHAVE EXACTLY like the registered type the real-judgment eval already maps them to
# (scripts/run_real_judgment_eval.py CATEGORY_TO_DISPUTE_TYPE): tenancy and employment like money_recovery,
# property like contract_breach. The mediation thresholds and the full-claim override were tuned on real data
# through those mapped types, so inheriting them keeps results identical and the headline comparable.
# Anything that would CHANGE behaviour (native precedents, native signals, new thresholds) belongs in phase 2
# and needs its own measured before/after. They ship inactive; DIGINYAYA_ENABLE_PREVIEW_TYPES=1 opens them.
_PREVIEW_TYPES = [
    {
        "id": "tenancy_dispute",
        "label": "Tenancy / Rent Dispute",
        "icon": "home",
        "tier": 2,
        "tier_label": "Tier 2 — AI-Assisted Virtual Judge",
        "active": False,
        "preview": True,
        "behaves_as": "money_recovery",
        "description": "Unpaid rent, security deposit refunds, and rent-related claims between landlord and tenant.",
        "examples": [
            "A tenant stopped paying rent for several months",
            "A landlord is not returning the security deposit after the tenant vacated",
        ],
    },
    {
        "id": "property_dispute",
        "label": "Property / Neighbour Dispute",
        "icon": "map",
        "tier": 2,
        "tier_label": "Tier 2 — AI-Assisted Virtual Judge",
        "active": False,
        "preview": True,
        "behaves_as": "contract_breach",
        "description": "Boundary, encroachment, possession and similar disputes between owners or neighbours.",
        "examples": [
            "A neighbour built a wall over the boundary of my plot",
            "A buyer paid an advance for a plot and the seller did not complete the sale",
        ],
    },
    {
        "id": "employment_dispute",
        "label": "Employment / Unpaid Wages",
        "icon": "briefcase",
        "tier": 2,
        "tier_label": "Tier 2 — AI-Assisted Virtual Judge",
        "active": False,
        "preview": True,
        "behaves_as": "money_recovery",
        "description": "Unpaid salary, notice pay, final settlement and similar claims between employee and employer.",
        "examples": [
            "My employer has not paid my last three months' salary",
            "My full and final settlement was never paid after I resigned",
        ],
    },
]
DISPUTE_TYPES.extend(_PREVIEW_TYPES)

PREVIEW_ENV = "DIGINYAYA_ENABLE_PREVIEW_TYPES"
# A type uses its OWN precedents for retrieval only once the corpus holds at least this many of them;
# until then it retrieves from the type it behaves as (today's behaviour). Real precedents only.
MIN_NATIVE_PRECEDENTS = 15


def preview_types_enabled() -> bool:
    import os
    return os.environ.get(PREVIEW_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def list_dispute_types() -> list[dict]:
    """The dispute types as the API should present them: preview types are inactive unless
    DIGINYAYA_ENABLE_PREVIEW_TYPES is set. Returns copies, so callers cannot mutate the registry."""
    out = []
    for dt in DISPUTE_TYPES:
        item = dict(dt)
        if item.get("preview") and preview_types_enabled():
            item["active"] = True
        out.append(item)
    return out


def get_dispute_type(dispute_id: str) -> dict | None:
    for dt in list_dispute_types():
        if dt["id"] == dispute_id:
            return dt
    return None


def effective_type(dispute_type: str) -> str:
    """The registered type whose tuned behaviour this type inherits (itself for the original four)."""
    for dt in DISPUTE_TYPES:
        if dt["id"] == dispute_type:
            return dt.get("behaves_as", dispute_type)
    return dispute_type


def precedent_category(dispute_type: str) -> str:
    """Which precedent category to retrieve from: the type's own once enough real precedents exist,
    otherwise the type it behaves as."""
    native = sum(1 for p in load_precedents() if p.get("category") == dispute_type)
    return dispute_type if native >= MIN_NATIVE_PRECEDENTS else effective_type(dispute_type)


_STATUTE_REQUIRED = ("id", "act", "section", "title", "summary", "dispute_types", "keywords")


def validate_statute(st: dict) -> tuple[bool, str]:
    for f in _STATUTE_REQUIRED:
        if f not in st or st[f] in (None, "", []):
            return False, f"missing field '{f}'"
    if not isinstance(st["dispute_types"], list) or not isinstance(st["keywords"], list):
        return False, "dispute_types and keywords must be lists"
    return True, "ok"


@lru_cache(maxsize=1)
def load_statutes() -> list[dict]:
    """Bundled statutory provisions used by the optional statute-grounding feature
    (DIGINYAYA_STATUTE_GROUNDING). The summaries are short neutral paraphrases written for orientation,
    NOT statutory text, and have not been reviewed by a lawyer -- see docs/STATUTE_GROUNDING.md."""
    with open(_DATA_DIR / "statutes.json", "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [st for st in data if validate_statute(st)[0]]
