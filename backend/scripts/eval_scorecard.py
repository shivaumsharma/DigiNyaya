"""Evaluation scorecard: the questions an interviewer (or a real user) would ask
of the who-wins / how-much benchmark, answered from data already on disk.

  1. Lift over the dumb baseline  -- ours minus "always say the claimant wins"
  2. Accuracy per confidence bucket -- is 0.9 confidence more accurate than 0.6?
  4. Wrong-but-confident rate     -- of high-confidence resolutions, how many were wrong
  5. Respondent-side precision/recall PER CATEGORY, always with the case count
  (3. judge-vs-human agreement lives in scripts/judge_human_agreement.py.)

Inputs: the judge's verdict file (real_judgment_verdict_comparison.json by
default) joined to the pipeline's per-case confidence in
real_judgment_eval_results.json. Every run is stamped (date, models, judge
prompt hash, pipeline code hash, sample size, categories) so a change in the
JUDGE is never mistaken for a change in the MODEL when two scorecards are
compared later.

Cases whose real judgment is also a RAG precedent are excluded (test-set
contamination) -- see contaminated_case_ids().

Run (from backend/): python -m scripts.eval_scorecard
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import inspect
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
BACKEND = Path(__file__).resolve().parent.parent
GOOD = ("match", "partial", "mismatch")
BUCKETS = [(0.0, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
CONFIDENT_THRESHOLDS = (0.8, 0.85, 0.9)
MIN_N = 30  # below this a rate is shown but flagged as too small to lean on


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def contaminated_case_ids(dataset: list[dict]) -> set[str]:
    """Eval cases whose real judgment is ALSO one of the RAG precedents -- the
    pipeline could retrieve the very case it's being tested on."""
    prec = _load(BACKEND / "app" / "data" / "precedents.json")
    docids = {str(p["docid"]) for p in prec if p.get("docid")}
    return {c["case_id"] for c in dataset if str(c["source"].get("docid")) in docids}


def run_stamp(rows: list[dict], verdict_path: str) -> dict:
    from app.llm.config import config
    import scripts.judge_real_outcomes as J
    from collections import Counter

    n_cases, categories = len(rows), sorted({r["category"] for r in rows})
    judge_src = inspect.getsource(J.judge) + inspect.getsource(J._amount_within_tolerance)
    # What actually produced THESE verdicts, read from the verdict entries
    # themselves -- not from the code as it stands today, which may have moved
    # on. Entries carrying an `amount_within_tolerance` key were judged with the
    # numeric-tolerance judge; older ones were not. A file mixing both (e.g.
    # reused cached verdicts) is flagged so nobody compares it to a clean run.
    tol = Counter("v2_tolerance" if "amount_within_tolerance" in r else "v1_llm_only" for r in rows)
    hashes = Counter(r.get("judge_prompt_hash") or "unstamped" for r in rows)
    fps = Counter((r.get("code_fingerprint") or "unknown")[:12] for r in rows)
    return {
        "verdicts_judge_versions": dict(tol),
        "verdicts_judge_prompt_hashes": dict(hashes),
        "verdicts_pipeline_code_hashes": dict(fps),
        "mixed_judge_versions": len(tol) > 1,
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "verdict_file": Path(verdict_path).name,
        "n_cases": n_cases,
        "categories": categories,
        "judge_model": config.sarvam_fast_model,
        "pipeline_reasoning_model": config.sarvam_reasoning_model,
        "current_judge_prompt_hash": hashlib.sha256(judge_src.encode()).hexdigest()[:12],
        "amount_tolerance": J.AMOUNT_TOLERANCE,
        "current_pipeline_code_hash": J._pipeline_fingerprint()[:12],
    }


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else None
    return p, r, f


def _fmt(x):
    return "  n/a" if x is None else f"{100 * x:5.1f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", default=str(DATA / "real_judgment_verdict_comparison.json"))
    ap.add_argument("--results", default=str(DATA / "real_judgment_eval_results.json"))
    ap.add_argument("--dataset", default=str(DATA / "eval_judgments.json"))
    ap.add_argument("--out", default=str(DATA / "eval_scorecard.json"))
    args = ap.parse_args()

    dataset = _load(args.dataset)
    bad = contaminated_case_ids(dataset)
    conf = {r["case_id"]: r for r in _load(args.results)}
    rows = [
        r for r in _load(args.verdicts)
        if r.get("verdict") in GOOD and r["case_id"] not in bad and r["case_id"] in conf
    ]
    for r in rows:
        r["confidence"] = conf[r["case_id"]].get("composite_confidence")
        r["correct"] = r["claimant_prevailed_ai"] == r["claimant_prevailed_real"]
    n = len(rows)
    stamp = run_stamp(rows, args.verdicts)
    print("RUN STAMP:", json.dumps(stamp))
    print(f"Excluded {len(bad)} contaminated case(s) (real judgment is also a RAG precedent): {sorted(bad)}\n")
    report: dict = {"stamp": stamp, "excluded_contaminated": sorted(bad)}

    # 1. Lift over the dumb baseline ------------------------------------
    def block(sub):
        tp = sum(r["claimant_prevailed_ai"] and r["claimant_prevailed_real"] for r in sub)
        fp = sum(r["claimant_prevailed_ai"] and not r["claimant_prevailed_real"] for r in sub)
        fn = sum(not r["claimant_prevailed_ai"] and r["claimant_prevailed_real"] for r in sub)
        tn = len(sub) - tp - fp - fn
        return tp, fp, tn, fn

    tp, fp, tn, fn = block(rows)
    acc = (tp + tn) / n
    base_acc = (tp + fn) / n  # "always claimant" is right whenever the claimant really won
    cp, cr, cf = _prf(tp, fp, fn)
    rp, rr, rf = _prf(tn, fn, fp)
    # dumb baseline: predicts claimant every time -> tp'=tp+fn, fp'=tn+fp, fn'=0
    bcp, bcr, bcf = _prf(tp + fn, tn + fp, 0)
    macro, base_macro = (cf + (rf or 0)) / 2, (bcf + 0.0) / 2
    print("1. LIFT OVER 'ALWAYS SAY THE CLAIMANT WINS'  (n=%d)" % n)
    print(f"   directional accuracy   ours {100 * acc:5.1f}%   dumb baseline {100 * base_acc:5.1f}%   lift {100 * (acc - base_acc):+5.1f}pt")
    print(f"   macro-F1               ours {100 * macro:5.1f}%   dumb baseline {100 * base_macro:5.1f}%   lift {100 * (macro - base_macro):+5.1f}pt")
    print(f"   respondent-class recall ours {100 * (rr or 0):5.1f}%   dumb baseline   0.0%")
    verdict = "BELOW" if acc < base_acc else "ABOVE"
    print(f"   -> accuracy is {verdict} the lazy guess; the value is in spotting respondent wins, not in raw accuracy.\n")
    report["lift"] = {"n": n, "accuracy": acc, "baseline_accuracy": base_acc, "macro_f1": macro,
                      "baseline_macro_f1": base_macro, "respondent_recall": rr}

    # 2. Accuracy per confidence bucket ---------------------------------
    print("2. ACCURACY BY CONFIDENCE BUCKET (composite_confidence)")
    print(f"   {'bucket':>10s} {'n':>6s} {'direction%':>11s} {'full-match%':>12s}")
    cal = []
    for lo, hi in BUCKETS:
        sub = [r for r in rows if r["confidence"] is not None and lo <= r["confidence"] < hi]
        if not sub:
            continue
        d = sum(r["correct"] for r in sub) / len(sub)
        m = sum(r["verdict"] == "match" for r in sub) / len(sub)
        cal.append({"bucket": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(sub), "direction": d, "full_match": m})
        print(f"   {lo:.1f}-{min(hi,1.0):.1f}{'':>4s} {len(sub):6d} {100*d:10.1f}% {100*m:11.1f}%")
    scored = [r for r in rows if r["confidence"] is not None]
    xs, ys = [r["confidence"] for r in scored], [1.0 if r["correct"] else 0.0 for r in scored]
    mx, my = st.mean(xs), st.mean(ys)
    corr = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            / ((sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5 or 1))
    print(f"   correlation(confidence, correct) = {corr:+.3f}  (near 0 => confidence carries no information)\n")
    report["calibration"] = {"buckets": cal, "corr_confidence_correct": corr}

    # 4. Wrong-but-confident --------------------------------------------
    print("4. WRONG-BUT-CONFIDENT (wrong side, at/above a confidence threshold)")
    total_wrong = sum(not r["correct"] for r in scored)
    wbc = []
    for t in CONFIDENT_THRESHOLDS:
        hi = [r for r in scored if r["confidence"] >= t]
        wrong = [r for r in hi if not r["correct"]]
        fpw = sum(r["claimant_prevailed_ai"] and not r["claimant_prevailed_real"] for r in wrong)
        wbc.append({"threshold": t, "n_confident": len(hi), "wrong": len(wrong),
                    "wrong_rate": len(wrong) / len(hi) if hi else None,
                    "share_of_all_wrong": len(wrong) / total_wrong if total_wrong else None,
                    "of_which_gave_claimant_a_win_they_lost": fpw})
        print(f"   >= {t:.2f}: {len(hi):6d} resolved, {len(wrong):5d} wrong = {_fmt(len(wrong)/len(hi) if hi else None)}%"
              f"   ({100*len(wrong)/total_wrong:.1f}% of ALL wrong answers; {fpw} wrongly favoured the claimant)")
    print()
    report["wrong_but_confident"] = wbc

    # 5. Respondent-side P/R per category -------------------------------
    print("5. RESPONDENT-SIDE PRECISION / RECALL PER CATEGORY")
    print(f"   {'category':34s} {'n':>6s} {'resp.wins':>9s} {'prec%':>6s} {'recall%':>8s} {'full-match%':>12s}")
    by = defaultdict(list)
    for r in rows:
        by[r["category"]].append(r)
    per_cat = {}
    for cat, sub in sorted(by.items(), key=lambda kv: -len(kv[1])):
        tp_, fp_, tn_, fn_ = block(sub)
        rp_, rr_, _ = _prf(tn_, fn_, fp_)
        fm = sum(r["verdict"] == "match" for r in sub) / len(sub)
        flag = "  (small n)" if len(sub) < MIN_N else ""
        per_cat[cat] = {"n": len(sub), "respondent_wins": tn_ + fp_, "resp_precision": rp_, "resp_recall": rr_, "full_match": fm}
        print(f"   {cat:34s} {len(sub):6d} {tn_+fp_:9d} {_fmt(rp_):>6s} {_fmt(rr_):>8s} {100*fm:11.1f}%{flag}")
    report["per_category"] = per_cat

    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
