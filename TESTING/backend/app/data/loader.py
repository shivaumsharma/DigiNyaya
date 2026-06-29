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
        "tier": 1,
        "tier_label": "Tier 1 — Fully Autonomous AI Resolution",
        "active": False,
        "description": "Recovery of lent money, unpaid dues, loan repayment disputes.",
        "examples": [],
    },
    {
        "id": "contract_breach",
        "label": "Simple Contract Breach",
        "icon": "file-text",
        "tier": 1,
        "tier_label": "Tier 1 — Fully Autonomous AI Resolution",
        "active": False,
        "description": "Breach of a clear written agreement with defined obligations.",
        "examples": [],
    },
    {
        "id": "cheque_bounce",
        "label": "Cheque Bounce",
        "icon": "receipt",
        "tier": 1,
        "tier_label": "Tier 1 — Fully Autonomous AI Resolution",
        "active": False,
        "description": "Dishonoured cheque claims under Section 138 NI Act.",
        "examples": [],
    },
]


def get_dispute_type(dispute_id: str) -> dict | None:
    for dt in DISPUTE_TYPES:
        if dt["id"] == dispute_id:
            return dt
    return None
