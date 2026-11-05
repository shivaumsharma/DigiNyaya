"""Why does LLM mode dismiss more cases than scripted mode? (live 61.6% accuracy vs scripted 75.0%; the LLM
predicts a claimant win in 55.9% of cases against 74.2% scripted and 83.1% in reality.)

Two agents can change the outcome in LLM mode:
  * analysis  (app/agents/analysis.py)  -- the LLM scores the respondent's defence strength and how well the
                                           claimant's own statement ESTABLISHES the disputed fact; these replace
                                           the scripted strengths, and net strength = claimant - respondent;
  * mediation (app/agents/mediation.py) -- the LLM proposes outcome type and relief ratio from those strengths.

Per case this script runs the pipeline up to mediation four ways and records strengths and the final relief type:
  SCRIPTED   no LLM
  LIVE       both agents on the LLM
  ANALYSIS   only the analysis agent on the LLM  (mediation forced scripted)
  MEDIATION  only the mediation agent on the LLM (analysis forced scripted)
The winner proxy is "relief type is not dismissed". Comparing the four shows which stage moves the claimant
rate, and the raw LLM scores show whether they are systematically low against what the scripted formula assumes.

No judge calls (so no judge noise). ~4 LLM calls per case. Needs SARVAM_API_KEY. Run from backend/:
  python -m scripts.diagnose_llm_dismissal --n 200 --out data_cache/llm_dismissal_diag.json
  python -m scripts.diagnose_llm_dismissal report data_cache/llm_dismissal_diag.json
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
_captured = threading.local()


def kind_of(prompt: str) -> str:
    if prompt.startswith("Analyse this"):
        return "analysis"
    if prompt.startswith("You are mediating"):
        return "mediation"
    return "other"


def install_wrapper(disabled: set[str]):
    """Wrap llm.generate_json: record the parsed output per agent kind, and return None (= scripted fallback)
    for the kinds in `disabled`. Returns an undo function."""
    from app import llm

    original = llm.generate_json

    def wrapped(prompt, *a, **kw):
        k = kind_of(prompt)
        if k in disabled:
            return None
        out = original(prompt, *a, **kw)
        if not hasattr(_captured, "d"):
            _captured.d = {}
        _captured.d[k] = out
        return out

    llm.generate_json = wrapped
    return lambda: setattr(llm, "generate_json", original)


def run_mode(case: dict, mode: str, build_ctx, graph, llm_disabled_for) -> dict:
    """Pipeline up to mediation in one mode. Returns strengths, relief type, and the raw LLM scores."""
    disabled = {"SCRIPTED": set(), "LIVE": set(), "ANALYSIS": {"mediation"}, "MEDIATION": {"analysis"}}[mode]
    _captured.d = {}
    ctx = build_ctx(case)
    undo = install_wrapper(disabled)
    try:
        with llm_disabled_for("0" if mode == "SCRIPTED" else "1"):
            list(graph.run_pipeline(ctx))
    finally:
        undo()
    if ctx.escalation is not None or ctx.mediation is None or ctx.analysis is None:
        return {"escalated": True}
    s = ctx.analysis.strength_score
    a = (_captured.d or {}).get("analysis") or {}
    m = (_captured.d or {}).get("mediation") or {}
    return {"escalated": False, "c": s.get("claimant"), "r": s.get("respondent"), "type": ctx.mediation.type,
            "claimant_wins": ctx.mediation.type != "dismissed", "engine": ctx.analysis.engine,
            "llm_defense": a.get("respondent_defense_strength"), "llm_substance": a.get("claimant_case_substance"),
            "llm_med_type": m.get("outcome_type"), "llm_med_ratio": m.get("relief_ratio")}


def collect(args) -> int:
    from app import llm
    if not llm.is_available():
        print("ERROR: LLM unavailable (needs SARVAM_API_KEY).")
        return 1
    from app.core import graph
    from scripts.judge_real_outcomes import _llm_disabled_for
    from scripts.run_real_judgment_eval import _build_ctx

    live_ids = {x["case_id"] for x in json.loads(Path(args.live).read_text(encoding="utf-8")) if x["verdict"] in ("match", "partial", "mismatch")}
    cases = [c for c in json.loads(Path(args.cases).read_text(encoding="utf-8")) if c["case_id"] in live_ids][: args.n]
    out = Path(args.out)
    done = {r["case_id"]: r for r in (json.loads(out.read_text(encoding="utf-8")) if out.exists() else [])}
    for i, case in enumerate(cases, 1):
        if case["case_id"] in done:
            continue
        rec = {"case_id": case["case_id"], "category": case["category"]}
        for mode in ("SCRIPTED", "LIVE", "ANALYSIS", "MEDIATION"):
            rec[mode] = run_mode(case, mode, _build_ctx, graph, _llm_disabled_for)
        done[case["case_id"]] = rec
        if i % 10 == 0:
            out.write_text(json.dumps(list(done.values()), indent=1), encoding="utf-8")
            print(f"  {i}/{len(cases)}", flush=True)
    out.write_text(json.dumps(list(done.values()), indent=1), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


def report(path: str) -> int:
    recs = json.loads(Path(path).read_text(encoding="utf-8"))
    modes = ("SCRIPTED", "LIVE", "ANALYSIS", "MEDIATION")
    ok = [r for r in recs if all(not r[m].get("escalated") for m in modes)]
    n = len(ok)
    print(f"{n} cases resolved in all four modes (of {len(recs)})\n")
    print(f"{'mode':<10}{'claimant wins':>15}{'mean c':>9}{'mean r':>9}{'mean c-r':>10}")
    for m in modes:
        cw = sum(r[m]["claimant_wins"] for r in ok) / n
        c, rr = mean([r[m]["c"] for r in ok]), mean([r[m]["r"] for r in ok])
        print(f"{m:<10}{100 * cw:>14.1f}%{c:>9.2f}{rr:>9.2f}{c - rr:>10.2f}")
    print("\nRaw LLM scores (LIVE mode): mean claimant_case_substance "
          f"{mean([r['LIVE']['llm_substance'] for r in ok]):.2f}, mean respondent_defense_strength {mean([r['LIVE']['llm_defense'] for r in ok]):.2f}")
    flips = [r for r in ok if r["SCRIPTED"]["claimant_wins"] and not r["LIVE"]["claimant_wins"]]
    rev = [r for r in ok if not r["SCRIPTED"]["claimant_wins"] and r["LIVE"]["claimant_wins"]]
    print(f"\nScripted claimant-win -> LIVE dismissed: {len(flips)} cases; the reverse: {len(rev)}")
    ana = sum(1 for r in flips if not r["ANALYSIS"]["claimant_wins"])
    med = sum(1 for r in flips if not r["MEDIATION"]["claimant_wins"])
    both = sum(1 for r in flips if not r["ANALYSIS"]["claimant_wins"] and not r["MEDIATION"]["claimant_wins"])
    print(f"  of those, dismissed when ONLY analysis is LLM: {ana}; when ONLY mediation is LLM: {med}; both alone do: {both}")
    low = [r for r in flips if r["LIVE"]["llm_substance"] is not None]
    if low:
        print(f"  in those {len(low)} flips: mean LLM claimant substance {mean([r['LIVE']['llm_substance'] for r in low]):.2f}, "
              f"mean LLM defence {mean([r['LIVE']['llm_defense'] for r in low]):.2f}, mean claimant strength scripted {mean([r['SCRIPTED']['c'] for r in low]):.2f} -> live {mean([r['LIVE']['c'] for r in low]):.2f}, "
              f"respondent {mean([r['SCRIPTED']['r'] for r in low]):.2f} -> {mean([r['LIVE']['r'] for r in low]):.2f}")
    mismatch = [r for r in ok if r["MEDIATION"]["llm_med_type"] and (r["MEDIATION"]["llm_med_type"] == "dismissed") != (not r["MEDIATION"]["claimant_wins"])]
    print(f"\nMediation LLM proposed 'dismissed' but the validator/override ended differently (or vice versa): {len(mismatch)}")
    by_cat: dict[str, list] = {}
    for r in ok:
        by_cat.setdefault(r["category"], []).append(r)
    print("\nclaimant-win rate by category (scripted vs live):")
    for c, rs in sorted(by_cat.items(), key=lambda x: -len(x[1]))[:5]:
        print(f"  {c:<32} n={len(rs):<4} {100 * sum(r['SCRIPTED']['claimant_wins'] for r in rs) / len(rs):.0f}% vs {100 * sum(r['LIVE']['claimant_wins'] for r in rs) / len(rs):.0f}%")
    return 0


K_GRID = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5)


def calib_collect(args) -> int:
    """One LLM call per case (analysis agent only, mediation scripted): save the raw LLM scores so a correction can be
    fitted offline. Resumable."""
    from app import llm
    if not llm.is_available():
        print("ERROR: LLM unavailable (needs SARVAM_API_KEY).")
        return 1
    from app.core import graph
    from scripts.judge_real_outcomes import _llm_disabled_for
    from scripts.run_real_judgment_eval import _build_ctx

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))[args.start:args.start + args.n]
    out = Path(args.out)
    done = {r["case_id"]: r for r in (json.loads(out.read_text(encoding="utf-8")) if out.exists() else [])}
    for i, case in enumerate(cases, 1):
        if case["case_id"] in done:
            continue
        r = run_mode(case, "ANALYSIS", _build_ctx, graph, _llm_disabled_for)
        r["case_id"], r["category"] = case["case_id"], case["category"]
        done[case["case_id"]] = r
        if i % 25 == 0:
            out.write_text(json.dumps(list(done.values())), encoding="utf-8")
            print(f"  {i}/{len(cases)}", flush=True)
    out.write_text(json.dumps(list(done.values())), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def _scripted_winner_with(case: dict, c: float, r: float, build_ctx, graph, mediation, llm_disabled_for) -> bool | None:
    """Scripted pipeline up to analysis, then replace the strengths and run the (scripted) mediation agent."""
    ctx = build_ctx(case)
    with llm_disabled_for("0"):
        list(graph.run_pipeline(ctx))
        if ctx.escalation is not None or ctx.analysis is None:
            return None
        ctx.analysis.strength_score = {**ctx.analysis.strength_score, "claimant": c, "respondent": r}
        ctx.mediation = mediation.run(ctx).output
    return ctx.mediation.type != "dismissed"


def _f1(t: int, fp: int, fn: int) -> float:
    return 0.0 if t == 0 else 2 * t / (2 * t + fp + fn)


def calib_fit(args) -> int:
    """Fit k in c' = c_scripted + k * (substance - M), M = mean substance on the FIT half (so the average claimant
    strength stays at the scripted level), on one random half; report the other half. Free: no LLM calls."""
    import hashlib
    from app.agents import mediation
    from app.core import graph
    from scripts.judge_real_outcomes import _llm_disabled_for
    from scripts.run_real_judgment_eval import _build_ctx

    cases = {c["case_id"]: c for c in json.loads(Path(args.cases).read_text(encoding="utf-8"))}
    real = {v["case_id"]: bool(v["claimant_prevailed_real"]) for v in json.loads(Path(args.verdicts).read_text(encoding="utf-8"))
            if v.get("claimant_prevailed_real") is not None}
    rows = [r for r in json.loads(Path(args.collected).read_text(encoding="utf-8"))
            if not r.get("escalated") and r.get("llm_substance") is not None and r["case_id"] in real]

    def scripted_c(r):
        ctx = _build_ctx(cases[r["case_id"]])
        with _llm_disabled_for("0"):
            list(graph.run_pipeline(ctx))
        return None if ctx.analysis is None else ctx.analysis.strength_score["claimant"]

    for r in rows:
        r["c_scripted"] = scripted_c(r)
    rows = [r for r in rows if r["c_scripted"] is not None]

    def half(cid: str) -> int:
        return int(hashlib.sha256(f"calib:{cid}".encode()).hexdigest()[:8], 16) % 2

    fit, test = [r for r in rows if half(r["case_id"]) == 0], [r for r in rows if half(r["case_id"]) == 1]
    M = sum(r["llm_substance"] for r in fit) / len(fit)
    print(f"{len(rows)} cases with LLM scores ({len(fit)} fit / {len(test)} test); M = mean substance on fit = {M:.3f}")

    def evaluate(sub_rows, k):
        wins, ys = [], []
        for r in sub_rows:
            c = round(min(max(r["c_scripted"] + k * (r["llm_substance"] - M), 0.1), 0.97), 2)
            w = _scripted_winner_with(cases[r["case_id"]], c, r["r"], _build_ctx, graph, mediation, _llm_disabled_for)
            if w is not None:
                wins.append(w)
                ys.append(real[r["case_id"]])
        n = len(ys)
        tp = sum(a and b for a, b in zip(ys, wins))
        tn = sum((not a) and (not b) for a, b in zip(ys, wins))
        fp = sum((not a) and b for a, b in zip(ys, wins))
        fn = sum(a and (not b) for a, b in zip(ys, wins))
        return {"n": n, "acc": (tp + tn) / n, "macro_f1": (_f1(tp, fp, fn) + _f1(tn, fn, fp)) / 2,
                "claimant_rate": sum(wins) / n, "resp_recall": tn / max(1, tn + fp)}

    print(f"\n{'k':>5} | {'FIT acc':>8}{'macroF1':>9}{'claim%':>8} | {'TEST acc':>9}{'macroF1':>9}{'claim%':>8}{'resp rec':>9}")
    results = {}
    for k in K_GRID:
        f, t = evaluate(fit, k), evaluate(test, k)
        results[k] = (f, t)
        print(f"{k:>5.1f} | {100 * f['acc']:>7.1f}%{f['macro_f1']:>9.3f}{100 * f['claimant_rate']:>7.0f}% | "
              f"{100 * t['acc']:>8.1f}%{t['macro_f1']:>9.3f}{100 * t['claimant_rate']:>7.0f}%{100 * t['resp_recall']:>8.0f}%")
    old = [r for r in test if r.get("claimant_wins") is not None]
    print(f"\nOLD LLM formula on the test half: accuracy "
          f"{100 * sum(r['claimant_wins'] == real[r['case_id']] for r in old) / len(old):.1f}%, "
          f"claimant rate {100 * sum(r['claimant_wins'] for r in old) / len(old):.0f}%")
    best = max(K_GRID, key=lambda k: results[k][0]["macro_f1"])
    print(f"\nBest k on the FIT half by macro-F1: {best}  (M = {M:.3f}) -> TEST half: "
          f"accuracy {100 * results[best][1]['acc']:.1f}%, macro-F1 {results[best][1]['macro_f1']:.3f}")
    Path(args.out).write_text(json.dumps({"M": M, "best_k": best, "grid": {str(k): {"fit": v[0], "test": v[1]} for k, v in results.items()}}, indent=2), encoding="utf-8")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in ("calib-collect", "calib-fit"):
        ap = argparse.ArgumentParser()
        ap.add_argument("mode")
        ap.add_argument("--cases", default=str(DATA / "eval_live_2000.json"))
        ap.add_argument("--verdicts", default=str(DATA / "real_judgment_verdict_comparison_v2.json"))
        ap.add_argument("--collected", default=str(DATA / "llm_calib_collected.json"))
        ap.add_argument("--start", type=int, default=0)
        ap.add_argument("--n", type=int, default=1000)
        ap.add_argument("--out", default=None)
        a = ap.parse_args(argv)
        if a.mode == "calib-collect":
            a.out = a.out or a.collected
            return calib_collect(a)
        a.out = a.out or str(DATA / "llm_calib_fit.json")
        return calib_fit(a)
    if argv and argv[0] == "report":
        return report(argv[1])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(DATA / "eval_live_2000.json"))
    ap.add_argument("--live", default=str(DATA / "live_llm_verdicts_2000.json"))
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--out", default=str(DATA / "llm_dismissal_diag.json"))
    return collect(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
