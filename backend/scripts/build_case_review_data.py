"""Assemble full per-case review data for small_claims_debt_recovery and
consumer_complaints: the facts fed in, what our system decided, the real
court's actual outcome, and the judge's comparison -- one row per case, no
sampling, so a human can look for patterns directly instead of trusting a
percentage.

Run (from backend/): python -m scripts.build_case_review_data
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from app.agents import nlp  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
OUT_PATH = _DATA_DIR / "case_review_data.json"

CATEGORIES = ("small_claims_debt_recovery", "consumer_complaints")


def main() -> int:
    signals = {s["case_id"]: s for s in json.loads((_DATA_DIR / "eval_judgments_with_signals.json").read_text(encoding="utf-8"))}
    results = {r["case_id"]: r for r in json.loads((_DATA_DIR / "real_judgment_eval_results.json").read_text(encoding="utf-8"))}
    verdicts = json.loads((_DATA_DIR / "real_judgment_verdict_comparison.json").read_text(encoding="utf-8"))

    rows = []
    for v in verdicts:
        cid = v["case_id"]
        if v["category"] not in CATEGORIES:
            continue
        sig = signals.get(cid)
        res = results.get(cid)
        if not sig or not res:
            continue
        sub = sig.get("signals") or {}
        c_score = res.get("claimant_strength_score")
        r_score = res.get("respondent_strength_score")
        rows.append({
            "case_id": cid,
            "category": v["category"],
            "source_court": (sig.get("source") or {}).get("court"),
            "case_description": sig.get("case_description", ""),
            "expected_outcome": sig.get("expected_outcome", ""),
            "claimant_evidence_count": sub.get("claimant_evidence_count"),
            "has_documentary_instrument": nlp.has_documentary_instrument(sig.get("case_description", "")),
            "respondent_defense_summary": sub.get("respondent_defense_summary"),
            "respondent_legal_ground": sub.get("respondent_legal_ground"),
            "respondent_ground_has_specific_support": sub.get("respondent_ground_has_specific_support"),
            "respondent_accepts_liability": sub.get("respondent_accepts_liability"),
            "respondent_offered_settlement_amount": sub.get("respondent_offered_settlement_amount"),
            "claimant_strength_score": c_score,
            "respondent_strength_score": r_score,
            "net_strength": round(c_score - r_score, 3) if c_score is not None and r_score is not None else None,
            "ingestion_confidence": res.get("ingestion_confidence"),
            "composite_confidence": res.get("composite_confidence"),
            "precedents_retrieved": res.get("precedents_retrieved"),
            "verdict": v["verdict"],
            "judge_reason": v.get("reason"),
        })

    OUT_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(rows)} case(s) -> {OUT_PATH}")
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], 0)
        by_cat[r["category"]] += 1
    print(by_cat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
