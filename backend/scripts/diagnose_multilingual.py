"""DIAGNOSE why the multilingual round trip changes outcomes (scripts/multilingual_roundtrip.py: ~26% of winners flip).

The round trip differs from the English control in MORE than language. Per case this script builds four conditions and
saves every intermediate, so the drop can be split into its causes:

  E0  the English control as scored before: signals extracted from the pre-decision JUDGMENT text (up to 8,500 chars).
  E1  English, signals re-extracted from the short DESCRIPTION only (the round trip can only ever see the description).
  E2  E1 repeated with the same text and prompt: the pure noise of LLM signal extraction (+ the judge).
  T   the round trip: description -> language xx -> gateway inbound -> English -> signals -> pipeline -> judge.

  E0 -> E1   information lost by describing instead of reading the judgment      (a DATASET / design issue)
  E1 -> E2   extraction + judge noise on identical input                          (a MODEL-reliability issue)
  E1 -> T    what translation itself adds, net of that noise                      (the LANGUAGE issue)

Also measured on the saved texts: whether numbers (amounts, dates, section numbers) survive translation, the length
ratio, and which of the six signals change between conditions.

Two modes.  run:     python -m scripts.diagnose_multilingual run --n 60 --langs te-IN --out data_cache/diag_te.json
            analyse: python -m scripts.diagnose_multilingual analyse data_cache/diag_*.json
`run` with --langs "" computes only E1/E2. Each run file is independent, so several can run in parallel.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")

from scripts.selective_guarantee import mcnemar_exact  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data_cache"
SIGNAL_KEYS = ("claimant_evidence_count", "respondent_defense_summary", "respondent_legal_ground",
               "respondent_ground_has_specific_support", "respondent_accepts_liability", "respondent_offered_settlement_amount")
NUM_RE = re.compile(r"\d[\d,\.]*")


def numbers_in(text: str) -> set[str]:
    """Numeric tokens, with separators dropped, ignoring one-digit ones (list markers) that carry no facts."""
    out = set()
    for tok in NUM_RE.findall(text or ""):
        t = tok.replace(",", "").strip(".")
        if len(t) >= 2:
            out.add(t)
    return out


def number_recall(original: str, back: str) -> float | None:
    a = numbers_in(original)
    return None if not a else len(a & numbers_in(back)) / len(a)


def signal_changes(a: dict | None, b: dict | None) -> list[str]:
    """Which of the six signals differ. Free-text summaries are compared only on presence/absence (their wording
    always differs); counts, flags and amounts are compared on value."""
    if not a or not b:
        return []
    out = []
    for k in SIGNAL_KEYS:
        va, vb = a.get(k), b.get(k)
        if k in ("respondent_defense_summary", "respondent_legal_ground"):
            if bool(va) != bool(vb):
                out.append(k)
        elif va != vb:
            out.append(k)
    return out


def score_case(case: dict, signals: dict | None, run_and_capture, judge) -> dict:
    """Pipeline + judge for `case` with `signals`. Returns {escalated, winner_claimant, verdict}."""
    if signals is None:
        return {"failed": True}
    c = {k: v for k, v in case.items() if k not in ("signals", "control")}
    c["signals"] = signals
    ai = run_and_capture(c)
    if ai is None:
        return {"escalated": True}
    v = judge(c, ai)
    if v is None or v.get("verdict") not in ("match", "partial", "mismatch"):
        return {"failed": True}
    return {"escalated": False, "winner_claimant": v.get("claimant_prevailed_ai"), "verdict": v["verdict"]}


def decompose(records: list[dict], lang: str) -> dict:
    """Winner accuracy and flips between conditions on cases where every needed condition produced a winner."""
    def w(r, cond):
        x = r.get(cond) if cond != "T" else (r.get("T") or {}).get(lang)
        return None if not x or x.get("escalated") or x.get("failed") else x.get("winner_claimant")

    rows = [r for r in records if all(w(r, c) is not None for c in ("E0", "E1", "E2", "T"))]
    n = len(rows)
    if not n:
        return {"n": 0}
    real = lambda r: bool(r["real_claimant"])  # noqa: E731
    acc = {c: sum(bool(w(r, c)) == real(r) for r in rows) / n for c in ("E0", "E1", "E2", "T")}
    flip = lambda a, b: sum(bool(w(r, a)) != bool(w(r, b)) for r in rows) / n  # noqa: E731

    def mc(a, b):
        imp = sum(1 for r in rows if bool(w(r, b)) == real(r) and bool(w(r, a)) != real(r))
        wor = sum(1 for r in rows if bool(w(r, a)) == real(r) and bool(w(r, b)) != real(r))
        return mcnemar_exact(imp, wor)

    return {"n": n, "accuracy": acc, "flip_rate": {"E0->E1": flip("E0", "E1"), "E1->E2": flip("E1", "E2"),
            "E1->T": flip("E1", "T"), "E0->T": flip("E0", "T")},
            "p_acc": {"E0 vs E1": mc("E0", "E1"), "E1 vs T": mc("E1", "T")}}


def aggregate_signals(samples: list[dict | None]) -> dict | None:
    """Combine repeated extractions of the SAME text into one steadier signal set: median for counts and amounts,
    majority for flags, the most common non-empty text for the free-text fields."""
    ok = [s for s in samples if s]
    if not ok:
        return None
    import statistics

    def vals(k):
        return [s.get(k) for s in ok if s.get(k) is not None]

    def majority(k):
        v = vals(k)
        return None if not v else (sum(1 for x in v if x) * 2 > len(v))

    def median(k):
        v = [x for x in vals(k) if isinstance(x, (int, float))]
        return None if not v else statistics.median_low(v)

    def common(k):
        v = [x for x in vals(k) if x]
        return Counter(v).most_common(1)[0][0] if v else None

    return {"claimant_evidence_count": median("claimant_evidence_count"),
            "respondent_defense_summary": common("respondent_defense_summary"),
            "respondent_legal_ground": common("respondent_legal_ground"),
            "respondent_ground_has_specific_support": majority("respondent_ground_has_specific_support"),
            "respondent_accepts_liability": bool(majority("respondent_accepts_liability")),
            "respondent_offered_settlement_amount": median("respondent_offered_settlement_amount")}


def pilot_mode(args) -> int:
    """Does averaging k extractions per case steady the winner? Reuses the saved round-trip text (no new translation).
    S_A, S_B = two independent k-sample aggregates of the English description (their flip rate is the remaining
    noise); S_T = a k-sample aggregate of the round-trip text. Run shards in parallel with --shard i/k."""
    from scripts.build_leakage_free_control import _generate_json
    from scripts.extract_case_signals import build_signals_prompt
    from scripts.judge_real_outcomes import judge, run_and_capture

    control = {c["case_id"]: c for c in json.loads(Path(args.control_cases).read_text(encoding="utf-8"))}
    saved = []
    for f in args.diag:
        saved += json.loads(Path(f).read_text(encoding="utf-8"))
    by_case: dict[str, dict] = {}
    for r in saved:
        m = by_case.setdefault(r["case_id"], {"case_id": r["case_id"], "real_claimant": r["real_claimant"], "T": {}})
        m["T"].update(r.get("T") or {})
    i, k = (int(x) for x in args.shard.split("/"))
    ids = sorted(by_case)[i::k]
    out = Path(args.out)
    done = {r["case_id"]: r for r in (json.loads(out.read_text(encoding="utf-8")) if out.exists() else [])}
    sig = lambda text: _generate_json(build_signals_prompt(text))  # noqa: E731
    for n, cid in enumerate(ids, 1):
        if cid in done:
            continue
        case, r = control[cid], by_case[cid]
        agg = lambda text: aggregate_signals([sig(text) for _ in range(args.k)])  # noqa: E731
        rec = {"case_id": cid, "real_claimant": r["real_claimant"], "A": score_case(case, agg(case["case_description"]), run_and_capture, judge),
               "B": score_case(case, agg(case["case_description"]), run_and_capture, judge), "T": {}}
        for lang, t in r["T"].items():
            if t.get("back"):
                bc = dict(case)
                bc["case_description"] = t["back"]
                rec["T"][lang] = score_case(bc, agg(t["back"]), run_and_capture, judge)
        done[cid] = rec
        if n % 5 == 0:
            out.write_text(json.dumps(list(done.values()), indent=1), encoding="utf-8")
            print(f"  {n}/{len(ids)}", flush=True)
    out.write_text(json.dumps(list(done.values()), indent=1), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def pilot_report(files: list[str]) -> int:
    recs = [r for f in files for r in json.loads(Path(f).read_text(encoding="utf-8"))]
    ok = lambda x: x and not x.get("escalated") and not x.get("failed")  # noqa: E731
    ab = [r for r in recs if ok(r["A"]) and ok(r["B"])]
    print(f"{len(ab)} cases with both aggregates")
    print(f"flip rate between two independent aggregates of the same text: {100 * sum(bool(r['A']['winner_claimant']) != bool(r['B']['winner_claimant']) for r in ab) / len(ab):.1f}%")
    for lang in sorted({lg for r in recs for lg in r["T"]}):
        rows = [r for r in ab if ok(r["T"].get(lang))]
        if rows:
            fl = sum(bool(r["A"]["winner_claimant"]) != bool(r["T"][lang]["winner_claimant"]) for r in rows) / len(rows)
            accA = sum(bool(r["A"]["winner_claimant"]) == bool(r["real_claimant"]) for r in rows) / len(rows)
            accT = sum(bool(r["T"][lang]["winner_claimant"]) == bool(r["real_claimant"]) for r in rows) / len(rows)
            print(f"{lang}: English vs round trip flips {100 * fl:.1f}% | accuracy English {100 * accA:.1f}% round trip {100 * accT:.1f}% (n={len(rows)})")
    return 0


def direct_mode(args) -> int:
    """Condition D: extract the signals DIRECTLY from the foreign-language text (no back-translation for the signals),
    using the foreign text saved by `run`. The pipeline's case description is still the gateway's English output, so
    D differs from T in exactly one thing: where the signals come from."""
    from scripts.build_leakage_free_control import _generate_json
    from scripts.extract_case_signals import build_signals_prompt
    from scripts.judge_real_outcomes import judge, run_and_capture

    control = {c["case_id"]: c for c in json.loads(Path(args.control_cases).read_text(encoding="utf-8"))}
    saved: dict[str, dict] = {}
    for f in args.diag:
        for r in json.loads(Path(f).read_text(encoding="utf-8")):
            saved.setdefault(r["case_id"], {"case_id": r["case_id"], "real_claimant": r["real_claimant"], "T": {}})["T"].update(r.get("T") or {})
    i, k = (int(x) for x in args.shard.split("/"))
    out = Path(args.out)
    done = {r["case_id"]: r for r in (json.loads(out.read_text(encoding="utf-8")) if out.exists() else [])}
    for n, cid in enumerate(sorted(saved)[i::k], 1):
        rec = done.setdefault(cid, {"case_id": cid, "real_claimant": saved[cid]["real_claimant"], "D": {}})
        for lang, t in saved[cid]["T"].items():
            if lang in rec["D"] or not t.get("foreign") or not t.get("back"):
                continue
            sig = _generate_json(build_signals_prompt(t["foreign"]))
            bc = dict(control[cid])
            bc["case_description"] = t["back"]
            rec["D"][lang] = dict(score_case(bc, sig, run_and_capture, judge), signals=sig)
        if n % 5 == 0:
            out.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {n}", flush=True)
    out.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def direct_report(diag_files: list[str], direct_files: list[str]) -> int:
    base: dict[str, dict] = {}
    for f in diag_files:
        for r in json.loads(Path(f).read_text(encoding="utf-8")):
            m = base.setdefault(r["case_id"], {"T": {}})
            for kk, v in r.items():
                if kk == "T":
                    m["T"].update(v)
                else:
                    m[kk] = v
    direct: dict[str, dict] = {}
    for f in direct_files:
        for r in json.loads(Path(f).read_text(encoding="utf-8")):
            direct.setdefault(r["case_id"], {}).update(r.get("D") or {})
    ok = lambda x: isinstance(x, dict) and not x.get("escalated") and not x.get("failed")  # noqa: E731
    for lang in sorted({lg for d in direct.values() for lg in d}):
        rows = [(base[c], base[c]["T"][lang], d[lang]) for c, d in direct.items() if c in base and lang in d
                and ok(base[c].get("E1")) and ok(base[c]["T"].get(lang)) and ok(d[lang])]
        n = len(rows)
        if not n:
            print(f"{lang}: no complete cases")
            continue
        w = lambda x: bool(x["winner_claimant"])  # noqa: E731
        real = lambda r: bool(r["real_claimant"])  # noqa: E731
        flips_t = sum(w(b["E1"]) != w(t) for b, t, _ in rows) / n
        flips_d = sum(w(b["E1"]) != w(d) for b, _, d in rows) / n
        acc = lambda pick: sum(w(pick(b, t, d)) == real(b) for b, t, d in rows) / n  # noqa: E731
        imp = sum(1 for b, t, d in rows if w(d) == real(b) and w(t) != real(b))
        wor = sum(1 for b, t, d in rows if w(t) == real(b) and w(d) != real(b))
        print(f"{lang} (n={n}): flips vs English description  back-translation {100 * flips_t:.1f}%  direct {100 * flips_d:.1f}%  "
              f"(English noise floor E1->E2 about 14-17%)")
        print(f"   accuracy: English {100 * acc(lambda b, t, d: b['E1']):.1f}%  back-translation {100 * acc(lambda b, t, d: t):.1f}%  "
              f"direct {100 * acc(lambda b, t, d: d):.1f}%   (direct better in {imp} cases, worse in {wor}; McNemar p={mcnemar_exact(imp, wor):.3f})")
    return 0


def run_mode(args) -> int:
    from app import llm
    if not llm.is_available():
        print("ERROR: LLM unavailable (needs SARVAM_API_KEY).")
        return 1
    from app.language.gateway import get_language_gateway
    from app.language.translator import translate_text
    from scripts.build_leakage_free_control import _generate_json
    from scripts.extract_case_signals import build_signals_prompt
    from scripts.judge_real_outcomes import judge, run_and_capture
    from scripts.multilingual_roundtrip import choose_cases

    control = json.loads(Path(args.control_cases).read_text(encoding="utf-8"))
    english = {v["case_id"]: v for v in json.loads(Path(args.control_verdicts).read_text(encoding="utf-8"))
               if v.get("verdict") in ("match", "partial", "mismatch")}
    sample = choose_cases(control, english, args.n, args.seed)
    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    out = Path(args.out)
    recs = {r["case_id"]: r for r in (json.loads(out.read_text(encoding="utf-8")) if out.exists() else [])}
    gw = get_language_gateway()
    sig = lambda text: _generate_json(build_signals_prompt(text))  # noqa: E731
    for i, case in enumerate(sample, 1):
        r = recs.setdefault(case["case_id"], {"case_id": case["case_id"], "real_claimant": english[case["case_id"]].get("claimant_prevailed_real")})
        eng = english[case["case_id"]]
        r["E0"] = {"escalated": False, "winner_claimant": eng.get("claimant_prevailed_ai")}
        r["E0_signals"] = case.get("signals")
        if not langs:  # baseline conditions only
            if "E1" not in r:
                r["E1_signals"] = sig(case["case_description"])
                r["E1"] = score_case(case, r["E1_signals"], run_and_capture, judge)
            if "E2" not in r:
                r["E2_signals"] = sig(case["case_description"])
                r["E2"] = score_case(case, r["E2_signals"], run_and_capture, judge)
        for lang in langs:
            r.setdefault("T", {})
            if lang in r["T"]:
                continue
            tr = translate_text(case["case_description"], source_lang="en-IN", target_lang=lang)
            t = {"foreign": tr.translated_text, "translate_ok": not tr.used_fallback}
            if t["translate_ok"]:
                inb = gw.to_pipeline_language(tr.translated_text, declared_language=lang)
                t["back"] = inb.pipeline_text
                t["signals"] = sig(inb.pipeline_text) if inb.was_translated else None
                t["number_recall"] = number_recall(case["case_description"], inb.pipeline_text)
                t["len_ratio"] = len(inb.pipeline_text) / max(1, len(case["case_description"]))
                t.update(score_case(case, t["signals"], run_and_capture, judge))
            r["T"][lang] = t
        if i % 5 == 0:
            out.write_text(json.dumps(list(recs.values()), ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {i}/{len(sample)}", flush=True)
    out.write_text(json.dumps(list(recs.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def analyse_mode(args) -> int:
    merged: dict[str, dict] = {}
    for f in args.files:
        for r in json.loads(Path(f).read_text(encoding="utf-8")):
            m = merged.setdefault(r["case_id"], {"case_id": r["case_id"]})
            for k, v in r.items():
                if k == "T":
                    m.setdefault("T", {}).update(v)
                else:
                    m[k] = v
    recs = list(merged.values())
    langs = sorted({lg for r in recs for lg in (r.get("T") or {})})
    print(f"{len(recs)} cases; languages: {langs}\n")
    for lang in langs:
        d = decompose(recs, lang)
        if not d["n"]:
            print(f"{lang}: no case has all four conditions yet")
            continue
        a, f, p = d["accuracy"], d["flip_rate"], d["p_acc"]
        print(f"{lang} (n={d['n']}): accuracy E0 {100 * a['E0']:.1f}%  E1 {100 * a['E1']:.1f}%  E2 {100 * a['E2']:.1f}%  T {100 * a['T']:.1f}%")
        print(f"   winner flips  E0->E1 {100 * f['E0->E1']:.1f}% (description-only loss) | E1->E2 {100 * f['E1->E2']:.1f}% (pure noise) | "
              f"E1->T {100 * f['E1->T']:.1f}% (translation + noise) | E0->T {100 * f['E0->T']:.1f}% (what we measured before)")
        print(f"   McNemar p: E0 vs E1 {p['E0 vs E1']:.3f} | E1 vs T {p['E1 vs T']:.3f}")
        nr = [r["T"][lang].get("number_recall") for r in recs if lang in (r.get("T") or {}) and r["T"][lang].get("number_recall") is not None]
        lr = [r["T"][lang].get("len_ratio") for r in recs if lang in (r.get("T") or {}) and r["T"][lang].get("len_ratio")]
        ch = Counter(k for r in recs if lang in (r.get("T") or {}) for k in signal_changes(r.get("E1_signals"), r["T"][lang].get("signals")))
        print(f"   numbers kept through translation: {100 * sum(nr) / len(nr):.0f}% (n={len(nr)}) | back/original length ratio {sum(lr) / len(lr):.2f}"
              if nr and lr else "   (no translation texts)")
        print(f"   signals that changed E1 -> T: {dict(ch)}")
    e12 = Counter(k for r in recs for k in signal_changes(r.get("E1_signals"), r.get("E2_signals")))
    e01 = Counter(k for r in recs for k in signal_changes(r.get("E0_signals"), r.get("E1_signals")))
    print(f"\nsignals that changed E1 -> E2 (same text twice): {dict(e12)}")
    print(f"signals that changed E0 -> E1 (judgment text vs description): {dict(e01)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    r.add_argument("--control-cases", default=str(DATA / "eval_leakfree_control.json"))
    r.add_argument("--control-verdicts", default=str(DATA / "leakfree_control_verdicts.json"))
    r.add_argument("--langs", default="")
    r.add_argument("--n", type=int, default=60)
    r.add_argument("--seed", type=int, default=5)
    r.add_argument("--out", required=True)
    a = sub.add_parser("analyse")
    a.add_argument("files", nargs="+")
    p = sub.add_parser("pilot")
    p.add_argument("--control-cases", default=str(DATA / "eval_leakfree_control.json"))
    p.add_argument("--diag", nargs="+", required=True)
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--shard", default="0/1")
    p.add_argument("--out", required=True)
    d = sub.add_parser("direct")
    d.add_argument("--control-cases", default=str(DATA / "eval_leakfree_control.json"))
    d.add_argument("--diag", nargs="+", required=True)
    d.add_argument("--shard", default="0/1")
    d.add_argument("--out", required=True)
    dr = sub.add_parser("direct-report")
    dr.add_argument("--diag", nargs="+", required=True)
    dr.add_argument("--direct", nargs="+", required=True)
    pr = sub.add_parser("pilot-report")
    pr.add_argument("files", nargs="+")
    args = ap.parse_args()
    if args.mode == "run":
        return run_mode(args)
    if args.mode == "pilot":
        return pilot_mode(args)
    if args.mode == "direct":
        return direct_mode(args)
    if args.mode == "direct-report":
        return direct_report(args.diag, args.direct)
    if args.mode == "pilot-report":
        return pilot_report(args.files)
    return analyse_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
