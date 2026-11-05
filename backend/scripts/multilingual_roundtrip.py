"""Test the MULTILINGUAL claim end to end: does a case submitted in an Indian language reach the same outcome as the
same case in English?

WHY. The pipeline is English-only. "Multilingual" in DigiNyaya means the language gateway (app/language): detect
the language, translate the user's text into English with Sarvam Mayura, run the English pipeline, translate the
answer back. Nothing so far measured whether that path preserves the outcome. This script does:

  for each sampled case (rebuilt facts-only description, scripts/build_leakage_free_control.py) and each language:
    1. translate the English description INTO the language (Mayura en -> xx): the "user's submission";
    2. send it through the REAL inbound gateway (declared language xx -> English), exactly as app/main.py does;
    3. re-extract the case signals from that gateway output (the same prompt the English control used), so
       information lost or distorted in translation reaches the pipeline;
    4. run the same pipeline and the same LLM judge as the English control.
  then compare with the English control result on the SAME cases.

It reports, per language: how many round trips worked, the escalation rate (vs English), WINNER CONSISTENCY (same
winner as the English run), and winner accuracy and per-side recall against the real court, with an exact McNemar
test on accuracy. The sample over-represents respondent wins, because they are where a lost nuance flips the result;
so read per-side recall and consistency, not the overall accuracy.

What it does NOT test: the OUTBOUND direction (English -> user's language) quality, translation of uploaded
documents, or languages outside --langs. A round trip can also hide errors that cancel out (a mistranslation
repaired on the way back); consistency is therefore a lower bound on how much translation can hurt.

COST. About 4 LLM/translation calls per (case, language): translate, gateway inbound, signals, judge. Needs
SARVAM_API_KEY. Resumes from the progress file. Run from backend/:
  python -m scripts.multilingual_roundtrip --n 120 --langs hi-IN,ta-IN,te-IN,bn-IN,mr-IN
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")

from scripts.selective_guarantee import mcnemar_exact  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data_cache"
DEFAULT_LANGS = "hi-IN,ta-IN,te-IN,bn-IN,mr-IN"


def choose_cases(control_cases: list[dict], english: dict[str, dict], n: int, seed: int) -> list[dict]:
    """Cases with a usable English control result, with ALL respondent wins first (they are the scarce, fragile
    ones), then random claimant wins up to n. Deterministic for a given seed."""
    pool = [c for c in control_cases if english.get(c["case_id"], {}).get("claimant_prevailed_ai") is not None]
    resp = [c for c in pool if not english[c["case_id"]].get("claimant_prevailed_real")]
    claim = [c for c in pool if english[c["case_id"]].get("claimant_prevailed_real")]
    rng = random.Random(seed)
    rng.shuffle(resp)
    rng.shuffle(claim)
    chosen = resp[:n]
    chosen += claim[: max(0, n - len(chosen))]
    return chosen


def round_trip_case(case: dict, lang: str, *, translate, inbound, signals_of) -> tuple[str, dict | None, dict]:
    """Return (status, round_tripped_case_or_None, info). `translate(text, lang)` -> (text, ok);
    `inbound(text, lang)` -> (english_text, was_translated); `signals_of(english_text)` -> dict | None.
    Injected so the logic is unit-testable without any network."""
    foreign, ok = translate(case["case_description"], lang)
    if not ok or not foreign:
        return "translate_failed", None, {}
    english_again, was_translated = inbound(foreign, lang)
    if not was_translated or not english_again:
        return "inbound_failed", None, {}
    signals = signals_of(english_again)
    if signals is None:
        return "signals_failed", None, {}
    out = {k: v for k, v in case.items() if k not in ("signals", "control")}
    out["case_description"] = english_again
    out["signals"] = signals
    return "ok", out, {"foreign_chars": len(foreign), "english_chars": len(english_again)}


def summarise(rows: list[dict]) -> dict:
    """rows: one per round trip that produced a judged result, each with english/lang winner, real winner, escalated."""
    done = [r for r in rows if r["status"] == "ok"]
    esc = [r for r in done if r["escalated"]]
    judged = [r for r in done if not r["escalated"] and r.get("claimant_ai") is not None]
    cons = [r for r in judged if r["english_claimant_ai"] is not None]
    acc_en = [bool(r["english_claimant_ai"]) == bool(r["real_claimant"]) for r in cons]
    acc_xx = [bool(r["claimant_ai"]) == bool(r["real_claimant"]) for r in cons]
    better = sum(1 for a, b in zip(acc_en, acc_xx) if b and not a)
    worse = sum(1 for a, b in zip(acc_en, acc_xx) if a and not b)

    def recall(flag: bool, which: str):
        sel = [r for r in cons if bool(r["real_claimant"]) == flag]
        if not sel:
            return None
        key = "english_claimant_ai" if which == "english" else "claimant_ai"
        return sum(1 for r in sel if bool(r[key]) == flag) / len(sel)

    return {
        "attempted": len(rows), "round_trips_ok": len(done), "escalated": len(esc),
        "escalation_rate": len(esc) / len(done) if done else None, "judged_in_both": len(cons),
        "winner_consistency": (sum(1 for r in cons if bool(r["claimant_ai"]) == bool(r["english_claimant_ai"])) / len(cons)) if cons else None,
        "accuracy_english": sum(acc_en) / len(cons) if cons else None,
        "accuracy_language": sum(acc_xx) / len(cons) if cons else None,
        "recall_claimant_english": recall(True, "english"), "recall_claimant_language": recall(True, "lang"),
        "recall_respondent_english": recall(False, "english"), "recall_respondent_language": recall(False, "lang"),
        "improved": better, "worsened": worse, "mcnemar_p": mcnemar_exact(better, worse),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--control-cases", default=str(DATA / "eval_leakfree_control.json"))
    ap.add_argument("--control-verdicts", default=str(DATA / "leakfree_control_verdicts.json"))
    ap.add_argument("--langs", default=DEFAULT_LANGS)
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--out-dir", default=str(DATA))
    args = ap.parse_args()

    from app import llm
    if not llm.is_available():
        print("ERROR: LLM unavailable (needs SARVAM_API_KEY). Aborting.")
        return 1
    from app.language.config import is_supported_language
    from app.language.gateway import get_language_gateway
    from app.language.translator import translate_text
    from scripts.build_leakage_free_control import _generate_json
    from scripts.extract_case_signals import build_signals_prompt
    from scripts.judge_real_outcomes import judge, run_and_capture

    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    bad = [x for x in langs if not is_supported_language(x)]
    if bad:
        print(f"Unsupported language code(s): {bad}")
        return 1
    control_cases = json.loads(Path(args.control_cases).read_text(encoding="utf-8"))
    english = {v["case_id"]: v for v in json.loads(Path(args.control_verdicts).read_text(encoding="utf-8"))
               if v.get("verdict") in ("match", "partial", "mismatch")}
    sample = choose_cases(control_cases, english, args.n, args.seed)
    print(f"{len(sample)} cases ({sum(1 for c in sample if not english[c['case_id']].get('claimant_prevailed_real'))} "
          f"respondent wins) x {len(langs)} languages = {len(sample) * len(langs)} round trips")

    gw = get_language_gateway()

    def translate(text, lang):
        r = translate_text(text, source_lang="en-IN", target_lang=lang)
        return r.translated_text, not r.used_fallback

    def inbound(text, lang):
        r = gw.to_pipeline_language(text, declared_language=lang)
        return r.pipeline_text, r.was_translated

    def signals_of(text):
        return _generate_json(build_signals_prompt(text))

    out_dir = Path(args.out_dir)
    progress_path = out_dir / "multilingual_roundtrip_progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
    t0, done = time.time(), 0
    for lang in langs:
        for case in sample:
            key = f"{case['case_id']}|{lang}"
            if key in progress:
                continue
            eng = english[case["case_id"]]
            status, rt, info = round_trip_case(case, lang, translate=translate, inbound=inbound, signals_of=signals_of)
            row = {"case_id": case["case_id"], "lang": lang, "status": status, "real_claimant": eng.get("claimant_prevailed_real"),
                   "english_claimant_ai": eng.get("claimant_prevailed_ai"), "escalated": False, "claimant_ai": None, **info}
            if rt is not None:
                ai = run_and_capture(rt)
                if ai is None:
                    row["escalated"] = True
                else:
                    v = judge(rt, ai)
                    if v is None or v.get("verdict") not in ("match", "partial", "mismatch"):
                        row["status"] = "judge_failed"
                    else:
                        row["claimant_ai"] = v.get("claimant_prevailed_ai")
                        row["verdict"] = v["verdict"]
            progress[key] = row
            done += 1
            if done % 10 == 0:
                progress_path.write_text(json.dumps(progress, indent=1), encoding="utf-8")
                print(f"  {lang} {done} done, {time.time() - t0:.0f}s", flush=True)
    progress_path.write_text(json.dumps(progress, indent=1), encoding="utf-8")

    by_lang: dict[str, list[dict]] = defaultdict(list)
    for r in progress.values():
        if r["lang"] in langs:
            by_lang[r["lang"]].append(r)
    report = {lang: summarise(rows) for lang, rows in by_lang.items()}
    (out_dir / "multilingual_roundtrip_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    pct = lambda x: "  -  " if x is None else f"{100 * x:5.1f}%"  # noqa: E731
    print(f"\n{'lang':<7}{'ok':>5}{'esc%':>7}{'both':>6}{'consist':>9}{'acc EN':>8}{'acc xx':>8}{'resp-recall EN':>16}{'xx':>7}{'McNemar p':>11}")
    for lang, s in report.items():
        print(f"{lang:<7}{s['round_trips_ok']:>5}{pct(s['escalation_rate']):>7}{s['judged_in_both']:>6}{pct(s['winner_consistency']):>9}"
              f"{pct(s['accuracy_english']):>8}{pct(s['accuracy_language']):>8}{pct(s['recall_respondent_english']):>16}"
              f"{pct(s['recall_respondent_language']):>7}{s['mcnemar_p']:>11.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
