"""How much does the pipeline's decision depend on how well a party writes their side, rather than the merits?

The Civil Court Simulation paper (Chen et al., 2026) found that giving the plaintiff a stronger, clearer
presentation raised judgment scores and a stronger defendant lowered them. For DigiNyaya the analogous worry
is fairness: a claimant or respondent who is less articulate (or writes in a second language) could lose or
win for reasons unrelated to the dispute. This script holds each case's FACTS fixed, perturbs only the
presentation, re-runs the deterministic pipeline, and reports how often the outcome moves.

Variants (each compared with the unmodified case):
  claimant_stronger   append a fixed paragraph listing documentary evidence and a formal notice; +2 evidence items
  claimant_terse      keep only the first sentence of the claimant's description
  respondent_stronger append a specific, supported legal defence to the respondent's statement
  respondent_silent   remove the respondent's reply entirely (uncontested)

Reported per variant, over cases that resolve without escalation in BOTH runs:
  direction flips     claim dismissed <-> relief awarded
  amount shift >20%   relief amount moves by more than 20% of the baseline amount
  escalation change   case escalates (or stops escalating) because of the perturbation
and two monotonicity checks that SHOULD be zero: a stronger claimant should never turn a win into a dismissal,
and a stronger respondent should never turn a dismissal into a win.

Scripted (deterministic) mode only, so it is free and reproducible: it measures the decision logic's own
sensitivity. Sensitivity of the live LLM path is a separate, paid experiment.

Run (from backend/):  python -m scripts.narrative_sensitivity [--dataset PATH] [--limit N] [--demo]
  --demo   use a few built-in synthetic cases (no data_cache needed) to see the output format
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

VARIANTS = ("claimant_stronger", "claimant_terse", "respondent_stronger", "respondent_silent")

CLAIMANT_BOOST = (
    " The claimant has produced the written agreement, dated payment receipts, bank statements and the "
    "correspondence between the parties, and sent a formal written notice on a stated date, to which the "
    "respondent gave no satisfactory reply."
)
RESPONDENT_BOOST = (
    " The respondent's defence is that the claim is barred by limitation and that the dispute was settled "
    "by a signed settlement agreement, supported by specific facts in the record including the date and "
    "reference of that agreement."
)

DEMO_CASES = [
    {"case_id": "DEMO-1", "category": "consumer_complaints", "case_description":
     "The claimant bought a washing machine for Rs. 28,000. It stopped working within a month. The seller "
     "refused a refund or repair despite two complaints. The claimant seeks a refund of the price."},
    {"case_id": "DEMO-2", "category": "small_claims_debt_recovery", "case_description":
     "The claimant lent Rs. 1,50,000 to the respondent, who promised to repay in six months. Repayment is "
     "overdue by a year. The claimant seeks recovery of the sum with interest."},
    {"case_id": "DEMO-3", "category": "contract_disputes", "case_description":
     "The respondent agreed to deliver furniture for Rs. 60,000 against an advance of Rs. 30,000 but never "
     "delivered. The claimant seeks the return of the advance and compensation for the delay."},
]


def first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip(), maxsplit=1)
    return parts[0] if parts else ""


def apply_variant(ctx, variant: str):
    """Return a perturbed deep copy of a CaseContext. Facts (parties, amount, dispute type) are never touched."""
    c = ctx.model_copy(deep=True)
    if variant == "claimant_stronger":
        c.description = (c.description or "") + CLAIMANT_BOOST
        extra = copy.deepcopy(c.evidence[:1]) or [{"filename": "extra.pdf", "kind": "document"}]
        c.evidence = (c.evidence + extra + extra)[:5]
    elif variant == "claimant_terse":
        c.description = first_sentence(c.description)
    elif variant == "respondent_stronger":
        sub = dict(c.respondent_submission or {"accepts_liability": False})
        sub["statement"] = (sub.get("statement") or "The respondent disputes the claim.") + RESPONDENT_BOOST
        c.respondent_submission = sub
    elif variant == "respondent_silent":
        c.respondent_submission = None
    else:
        raise ValueError(f"unknown variant {variant!r}")
    return c


def run_ctx(ctx) -> dict:
    """Run the deterministic pipeline up to the mediation decision and summarise it."""
    from app.core import graph
    from scripts.judge_real_outcomes import _llm_disabled_for

    with _llm_disabled_for("0"):
        list(graph.run_pipeline(ctx))
    if ctx.escalation is not None:
        return {"escalated": True, "dismissed": None, "amount": None}
    med = ctx.mediation
    if med is None:
        return {"escalated": True, "dismissed": None, "amount": None}
    return {"escalated": False, "dismissed": med.type == "dismissed", "amount": float(med.amount or 0.0)}


def compare(base: dict, var: dict, variant: str) -> dict:
    """Per-case comparison of a perturbed run with its baseline. Pure function (unit-tested)."""
    out = {"comparable": False, "flip": False, "amount_shift": False, "escalation_change": False, "monotonic_violation": False}
    out["escalation_change"] = base["escalated"] != var["escalated"]
    if base["escalated"] or var["escalated"]:
        return out
    out["comparable"] = True
    out["flip"] = base["dismissed"] != var["dismissed"]
    if base["amount"]:
        out["amount_shift"] = abs(var["amount"] - base["amount"]) > 0.2 * base["amount"]
    else:
        out["amount_shift"] = var["amount"] > 0
    if variant == "claimant_stronger" and not base["dismissed"] and var["dismissed"]:
        out["monotonic_violation"] = True
    if variant == "respondent_stronger" and base["dismissed"] and not var["dismissed"]:
        out["monotonic_violation"] = True
    return out


def summarise(rows: list[dict]) -> dict:
    comparable = [r for r in rows if r["comparable"]]
    n = len(comparable)
    return {
        "n_total": len(rows),
        "n_comparable": n,
        "direction_flips": sum(r["flip"] for r in comparable),
        "amount_shift_over_20pct": sum(r["amount_shift"] for r in comparable),
        "escalation_changes": sum(r["escalation_change"] for r in rows),
        "monotonic_violations": sum(r["monotonic_violation"] for r in comparable),
    }


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else "0/0"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset")
    ap.add_argument("--limit", type=int, default=200, help="max cases (first N after a seeded shuffle)")
    ap.add_argument("--demo", action="store_true", help="use built-in synthetic cases")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    # The pipeline reads the documents table. Use a scratch SQLite file unless the caller already chose a
    # database, so this never needs (or touches) the real one; create_all is idempotent.
    import os
    import tempfile
    os.environ.setdefault("DIGINYAYA_DB", str(Path(tempfile.gettempdir()) / "diginyaya_sensitivity.db"))
    from app import db as app_db
    app_db.init_db()

    from scripts.run_real_judgment_eval import DATASET_PATH, _build_ctx

    if args.demo:
        cases = DEMO_CASES
    else:
        path = Path(args.dataset) if args.dataset else Path(DATASET_PATH)
        if not path.exists():
            print(f"Not found: {path} (use --demo to try the output format without the eval data)")
            return 1
        import random
        cases = json.loads(path.read_text(encoding="utf-8"))
        random.Random(args.seed).shuffle(cases)
        cases = cases[: args.limit]

    results: dict[str, list[dict]] = {v: [] for v in VARIANTS}
    for case in cases:
        try:
            base_ctx = _build_ctx(case)
            base = run_ctx(base_ctx.model_copy(deep=True))
            for v in VARIANTS:
                results[v].append(compare(base, run_ctx(apply_variant(base_ctx, v)), v))
        except Exception as exc:  # one malformed case must not hide the others
            print(f"skipped {case.get('case_id')}: {exc}")

    print(f"Narrative sensitivity over {len(cases)} case(s), deterministic pipeline\n")
    print(f"{'variant':20s} {'comparable':>10s} {'direction flips':>18s} {'amount shift >20%':>19s} {'escalation chg':>15s} {'monotonic viol.':>16s}")
    for v in VARIANTS:
        s = summarise(results[v])
        n = s["n_comparable"]
        print(f"{v:20s} {n:10d} {_pct(s['direction_flips'], n):>18s} {_pct(s['amount_shift_over_20pct'], n):>19s} "
              f"{s['escalation_changes']:15d} {s['monotonic_violations']:16d}")
    print("\nMonotonic violations should be 0 (a stronger claimant should never turn a win into a dismissal; "
          "a stronger respondent should never turn a dismissal into a win).")
    print("High flip rates for claimant_terse or claimant_stronger mean presentation, not merits, drives outcomes; "
          "that is a fairness finding to report, and a reason to normalise or structure the input.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
