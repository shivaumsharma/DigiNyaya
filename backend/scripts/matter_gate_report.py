"""Where does a matter type stand against the evaluation gate? (docs/PLATFORM_PLAN.md section 5; app.matters.GATE_CHECKS)

A matter type may not be raised to L2 until every gate check passes. Two of the six checks can be MEASURED from the corpus files
this repo already produces; the rest need a person or an LLM run, so they are listed with the exact command and are never
marked as passed by this script:

  MEASURED   real_cases_with_endings       enough real cases of this type with the ending recorded, and enough of them CONTESTED
                                           (ex parte and no-merits endings cannot show the system can tell a good claim from a bad one)
             frozen_contested_test_set     the held-out contested test set holds enough cases of this type
  MANUAL     judge_validated_vs_humans, balanced_accuracy_with_ci, escalation_calibrated, lawyer_reviewed

It also reports native precedents (below app.data.loader.MIN_NATIVE_PRECEDENTS the type borrows another type's precedents) and
warns when the code's registry claims a measured check the data does not support.

THRESHOLDS ARE PROPOSALS, chosen so the confidence intervals the later checks report are not hopelessly wide; change them with
--min-* and say so when you report. Nothing is written, no LLM or network is used.

Run (from backend/):
  python -m scripts.matter_gate_report --matter tenancy_dispute
  python -m scripts.matter_gate_report --matter tenancy_dispute --cases data_cache/pilot_tagged.json --no-tag
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
CONTESTED = {"decreed", "dismissed"}
DEFAULTS = {"min_cases": 300, "min_contested": 100, "min_test": 50}

# What a person has to run for the checks this script cannot measure.
MANUAL_STEPS = {
    "judge_validated_vs_humans": "label a sheet for this type (scripts/build_human_label_sheet.py), then scripts/judge_human_agreement.py with a target",
    "balanced_accuracy_with_ci": "python -m scripts.accuracy_by_disposal --split test, plus scripts/leakfree_control_report.py; needs the LLM key",
    "escalation_calibrated": "python -m scripts.selective_guarantee on validation data for this type",
    "lawyer_reviewed": "a lawyer reviews this type's statutes (app/data/statutes.json), prompts and guide; record who and when",
}
MEASURED = ("real_cases_with_endings", "frozen_contested_test_set")


def categories_for(matter_id: str) -> list[str]:
    """Corpus categories that belong to a matter: the native mapping used by the eval, plus the id itself (e.g. cheque_bounce)."""
    from scripts.run_real_judgment_eval import CATEGORY_TO_DISPUTE_TYPE, NATIVE_CATEGORY_TO_DISPUTE_TYPE

    cats = {c for c, t in NATIVE_CATEGORY_TO_DISPUTE_TYPE.items() if t == matter_id}
    # Original types own the categories mapped to them, except those that belong to a native type or are escalation tests.
    cats |= {c for c, t in CATEGORY_TO_DISPUTE_TYPE.items()
             if t == matter_id and c not in NATIVE_CATEGORY_TO_DISPUTE_TYPE and not c.startswith("escalation__")}
    return sorted(cats)


def count_cases(tagged: list[dict], cats: list[str]) -> dict:
    mine = [c for c in tagged if c.get("category") in cats]
    disp = collections.Counter(((c.get("source") or {}).get("disposal") or "untagged") for c in mine)
    ended = sum(v for k, v in disp.items() if k not in {"untagged", "unknown"})
    return {"total": len(mine), "by_disposal": dict(disp), "with_ending": ended,
            "contested": sum(disp.get(k, 0) for k in CONTESTED)}


def count_test(manifest: dict | None, cats: list[str]) -> int | None:
    if manifest is None:
        return None
    by_cat = manifest.get("by_category") or {}
    return sum((by_cat.get(c) or {}).get("test", 0) for c in cats)


def native_precedents(matter_id: str) -> int:
    from app.data.loader import load_precedents

    return sum(1 for p in load_precedents() if p.get("category") == matter_id)


def assess(matter_id: str, counts: dict, test_n: int | None, registry_passed: tuple[str, ...], limits: dict) -> list[dict]:
    """One row per gate check: status MET / NOT_MET / UNKNOWN (cannot be measured) / MANUAL, plus the numbers."""
    rows = []
    if counts["total"] == 0:
        r1 = ("UNKNOWN", "no cases of this type in the corpus (or categories not mapped)")
    else:
        ok = counts["with_ending"] >= limits["min_cases"] and counts["contested"] >= limits["min_contested"]
        r1 = ("MET" if ok else "NOT_MET",
              f"{counts['with_ending']} with an ending (need {limits['min_cases']}), {counts['contested']} contested (need {limits['min_contested']}); "
              f"endings: {counts['by_disposal']}")
    rows.append({"check": "real_cases_with_endings", "status": r1[0], "detail": r1[1]})
    if test_n is None:
        r2 = ("UNKNOWN", "no held-out manifest found: run scripts/freeze_heldout.py")
    else:
        r2 = ("MET" if test_n >= limits["min_test"] else "NOT_MET", f"{test_n} contested test cases (need {limits['min_test']})")
    rows.append({"check": "frozen_contested_test_set", "status": r2[0], "detail": r2[1]})
    for check, how in MANUAL_STEPS.items():
        rows.append({"check": check, "status": "MANUAL", "detail": how})
    for r in rows:
        r["registry_claims_passed"] = r["check"] in registry_passed
        r["inconsistent"] = r["registry_claims_passed"] and r["status"] in {"NOT_MET", "UNKNOWN"} and r["check"] in MEASURED
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matter", required=True)
    ap.add_argument("--cases", default=str(DATA / "eval_judgments.json"))
    ap.add_argument("--cache-dir", default=str(DATA / "indiankanoon"))
    ap.add_argument("--manifest", default=str(DATA / "heldout_manifest.json"))
    ap.add_argument("--no-tag", action="store_true", help="cases already carry source.disposal (e.g. a *_tagged.json file)")
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k.replace('_', '-')}", type=int, default=v)
    args = ap.parse_args()

    from app.matters import get_matter

    m = get_matter(args.matter)
    if m is None or m.pipeline_type is None:
        print(f"'{args.matter}' is not a matter with a resolution pipeline.")
        return 1
    cats = categories_for(args.matter)
    cp = Path(args.cases)
    if not cp.exists():
        print(f"Not found: {cp}. The real-case corpus is local to your machine (data_cache/ is not committed).")
        return 1
    cases = [c for c in json.loads(cp.read_text(encoding="utf-8")) if c.get("category") in cats]
    if args.no_tag:
        tagged = cases
    else:
        from scripts.tag_disposal import tag_cases

        tagged, _ = tag_cases(cases, Path(args.cache_dir))
    mp = Path(args.manifest)
    manifest = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
    limits = {k: getattr(args, k) for k in DEFAULTS}
    counts = count_cases(tagged, cats)
    rows = assess(args.matter, counts, count_test(manifest, cats), m.gate_passed, limits)

    print(f"{m.label} ({m.id}): level {m.level.name}, status {m.status}; corpus categories: {', '.join(cats) or 'none'}\n")
    for r in rows:
        flag = "   <-- registry claims this check but the data does not support it" if r["inconsistent"] else ""
        print(f"  {r['status']:8s} {r['check']:28s} {r['detail']}{flag}")
    n = native_precedents(args.matter)
    from app.data.loader import MIN_NATIVE_PRECEDENTS, effective_type

    print(f"\n  Native precedents: {n} (retrieval uses the type's own once there are {MIN_NATIVE_PRECEDENTS}; "
          f"until then it borrows '{effective_type(m.pipeline_type)}')")
    open_checks = [r["check"] for r in rows if r["status"] != "MET"]
    print(f"\n  Open checks ({len(open_checks)}/{len(rows)}): {', '.join(open_checks) or 'none'}")
    print("  Until all six pass, this type must not be raised to L2 for users; keep it at preview/guide.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
