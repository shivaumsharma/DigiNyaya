"""Build a LEAKAGE-FREE CONTROL set: re-create each sampled case's inputs from the PRE-DECISION part of the
judgment only, so the evaluation can be re-run and compared with the original.

WHY. Every eval case's input (`case_description`, and the `signals` the pipeline actually consumes) was written
by an LLM that was given text containing the court's decision:
  * descriptions: the LLM read the whole judgment while also writing `expected_outcome`;
  * signals: scripts/extract_case_signals.py passes _window(body) = the first 6,000 + LAST 2,500 characters,
    and for any judgment under 8,500 characters the WHOLE text -- the conclusion included. The prompt only
    INSTRUCTS the model to ignore it.
The leakage audit (scripts/audit_outcome_leakage.py) shows explicit result phrasing is rare (1.1%) and does not
drive the numbers, but it cannot see SUBTLE leakage (a narrative framed around the winner, a "defence collapsed"
signal that depends on what happened later). The only test that can is to remove the decision from the input and
see whether the headline moves. That is this experiment.

WHAT IT DOES, per sampled case:
  1. loads the cached raw judgment text (data_cache/indiankanoon/<docid>.json, the same cache the sourcing
     scripts wrote -- no new fetches);
  2. cuts it BEFORE the decision: at the earliest decisive marker ("in the result", "the suit is decreed", ...)
     or at --max-fraction of the text, whichever comes first;
  3. asks the LLM for a neutral description of the facts, claims and issues from that text only, and re-runs the
     SAME signal-extraction prompt (extract_case_signals.build_signals_prompt) on it;
  4. scans the new description for strong outcome phrasing or a named judge (the leakage audit's own patterns),
     retries once with a stricter instruction, and EXCLUDES the case if it is still flagged;
  5. keeps everything else about the case identical (category, expected_outcome, claimed_amount, ...).

OUTPUTS (data_cache/): eval_leakfree_control.json (the control cases), eval_leakfree_original.json (the ORIGINAL
versions of exactly the same cases), leakfree_control_progress.json (status per case; lets a re-run resume) and
leakfree_control_report.json (counts). Nothing existing is modified.

COST. About two LLM calls per case (description + signals), so --n 400 is ~800 calls, plus scoring the control
set with scripts/judge_real_outcomes.py (one judge call per case). Needs SARVAM_API_KEY. Start with --n 40.

Run (from backend/):  python -m scripts.build_leakage_free_control --n 400
Then follow docs/LEAKAGE_FREE_CONTROL.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
CACHE_DIR = DATA / "indiankanoon"
GOOD = ("match", "partial", "mismatch")
MAX_SIGNAL_WINDOW = 8500  # same total size extract_case_signals' window allows, but head-only here

# The earliest of these ends the "pre-decision" text. Deliberately about the DECISION, not the reasoning, because
# the reasoning is often needed to understand the facts; --max-fraction guards against decisions phrased unusually.
DECISION_MARKERS: list[tuple[str, re.Pattern]] = [
    (name, re.compile(rx, re.IGNORECASE))
    for name, rx in [
        ("in the result", r"\bin\s+the\s+result\b"),
        ("for these reasons", r"\bfor\s+(?:the|these)\s+(?:foregoing|aforesaid|above|said)?\s*reasons?\b"),
        ("in view of the above", r"\bin\s+view\s+of\s+the\s+(?:above|foregoing|aforesaid)\b"),
        ("point answered", r"\b(?:point|issue)\s+no\.?\s*\d+\s*[:\-]?\s*(?:in\s+the\s+)?(?:affirmative|negative)\b"),
        ("operative order", r"\b(?:suit|appeal|petition|complaint|claim|application)\s+(?:is|stands)\s+(?:hereby\s+)?(?:decreed|dismissed|allowed|disposed)\b"),
        ("is hereby decreed", r"\bis\s+hereby\s+(?:decreed|dismissed|allowed)\b"),
        ("directed to pay", r"\b(?:defendants?|respondents?|opposite\s+part(?:y|ies))\s+(?:is|are|shall\s+be)\s+(?:hereby\s+)?(?:directed|ordered|liable)\b"),
        ("following order", r"\bthe\s+following\s+(?:order|judg(?:e)?ment)\b"),
        ("final order heading", r"\n\s*(?:final\s+)?order\s*[:\-]\s"),
    ]
]


def bucket(seed: int, case_id: str) -> float:
    """A stable number in [0, 1) for this case, independent of every other case."""
    return int(hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).hexdigest()[:12], 16) / float(16 ** 12)


def pre_decision_text(text: str, max_fraction: float = 0.6, min_keep: int = 800, min_len: int = 600) -> dict | None:
    """Cut `text` before the decision. Returns {"text", "reason", "kept_chars", "total_chars"} or None when what
    is left is too short to describe a case (the case is then skipped, never padded with the decision)."""
    text = (text or "").strip()
    total = len(text)
    if total < min_len:
        return None
    frac_cut = int(total * max_fraction)
    cut, reason = frac_cut, f"fraction {max_fraction:g}"
    for name, rx in DECISION_MARKERS:
        m = rx.search(text, min(min_keep, total))
        if m and m.start() < cut:
            cut, reason = m.start(), f"marker: {name}"
    kept = text[:cut].strip()
    # End on a complete sentence: if a marker were ever missed, this stops a half-sentence of decision wording
    # ("In the result, the ...") from sitting at the cut edge.
    window = kept[-400:]
    ends = [window.rfind(ch) for ch in (". ", "? ", "! ", "\n")]
    best = max(ends)
    if best > 0:
        kept = kept[: len(kept) - len(window) + best + 1].strip()
    if len(kept) < min_len:
        return None
    return {"text": kept, "reason": reason, "kept_chars": len(kept), "total_chars": total}


def select_sample(cases: list[dict], verdicts: dict[str, dict], n: int, seed: int) -> list[dict]:
    """Deterministic, stratified (category x real outcome) sample of cases that were judged in `verdicts`."""
    pool = [c for c in cases if verdicts.get(c["case_id"], {}).get("verdict") in GOOD]
    strata: dict[tuple, list[dict]] = {}
    for c in pool:
        v = verdicts[c["case_id"]]
        strata.setdefault((c["category"], bool(v.get("claimant_prevailed_real"))), []).append(c)
    total = len(pool)
    if total == 0 or n <= 0:
        return []
    chosen: list[dict] = []
    for key in sorted(strata, key=str):
        group = sorted(strata[key], key=lambda c: bucket(seed, c["case_id"]))
        quota = max(1, round(n * len(group) / total))
        chosen.extend(group[: min(quota, len(group))])
    chosen.sort(key=lambda c: bucket(seed, c["case_id"]))
    return chosen[:n]


DESCRIPTION_SYSTEM = (
    "You write neutral case descriptions for a dispute-resolution benchmark from the BEFORE-THE-DECISION part of "
    "an Indian court judgment. You never know, state or hint at how the case was decided."
)
STRICT_SUFFIX = (
    " Your previous answer contained wording that states or implies a ruling. Rewrite it using ONLY the parties' "
    "claims and defences as ALLEGATIONS ('the claimant alleges...', 'the respondent contends...'), with no "
    "findings, no 'held', no 'decreed', no 'ordered', and no judge's name."
)


def description_prompt(pre_text: str, strict: bool = False) -> str:
    return (
        "Below is the first part of a real Indian court judgment, cut off BEFORE the court's decision. Write a "
        "120-200 word neutral account of the facts, each side's claims and defences, and the legal issue(s) in "
        "dispute, as a fresh case narrative a claimant might submit. Rules: refer to parties generically ('the "
        "claimant', 'the respondent', 'the tenant', 'the landlord'); never use real names of private people or "
        "companies; do NOT name the judge; state the relief the claimant ASKS FOR, with any amounts as claimed, "
        "never as awarded; describe allegations as allegations; do not state, predict or hint at who should win. "
        'Return JSON only: {"case_description": "<text>"}.'
        + (STRICT_SUFFIX if strict else "")
        + f"\n\nJUDGMENT (BEFORE THE DECISION):\n{pre_text}"
    )


def _generate_json(prompt: str, *, system: str | None = None, attempts: int = 2, backoff: float = 35.0) -> dict | None:
    """llm.generate_json with one retry after the circuit breaker's cooldown (it returns None for both a real
    failure and its 30s fail-fast window). Wrapped so tests can replace it."""
    from app import llm

    for attempt in range(attempts):
        result = llm.generate_json(prompt, system=system or llm.SYSTEM_PROMPT, max_tokens=2000)
        if result is not None:
            return result
        if attempt < attempts - 1:
            time.sleep(backoff)
    return None


def description_leaks(description: str) -> list[str]:
    """Reasons a description is NOT leakage-free, using the same patterns as the leakage audit."""
    from scripts.audit_outcome_leakage import audit_text

    r = audit_text({"case_description": description})
    reasons = list(r["outcome_language"])
    if r["names_judge"]:
        reasons.append("names a judge")
    return reasons


def build_one(case: dict, raw_text: str | None, *, max_fraction: float, generate=None) -> tuple[str, dict | None, dict]:
    """Return (status, control_case_or_None, info). status is one of: ok, no_text, too_short, llm_fail,
    leak_excluded. Pure given `generate` (defaults to the real LLM call), so it is unit-tested with a fake."""
    from scripts.extract_case_signals import build_signals_prompt

    generate = generate or _generate_json
    if not raw_text:
        return "no_text", None, {}
    cut = pre_decision_text(raw_text, max_fraction=max_fraction)
    if cut is None:
        return "too_short", None, {}
    info = {"cut_reason": cut["reason"], "kept_chars": cut["kept_chars"], "total_chars": cut["total_chars"]}

    description, leaks, retries = None, [], 0
    for strict in (False, True):
        out = generate(description_prompt(cut["text"], strict=strict), system=DESCRIPTION_SYSTEM)
        if not out or not str(out.get("case_description") or "").strip():
            return "llm_fail", None, info
        description = str(out["case_description"]).strip()
        leaks = description_leaks(description)
        if not leaks:
            break
        retries += 1
    info["retries"] = retries
    if leaks:
        info["leak_flags"] = leaks
        return "leak_excluded", None, info

    signals = generate(build_signals_prompt(cut["text"][:MAX_SIGNAL_WINDOW]))
    if signals is None:
        return "llm_fail", None, info

    control = {k: v for k, v in case.items() if k not in ("signals",)}
    control["case_description"] = description
    control["signals"] = signals
    control["control"] = {"original_description": case.get("case_description"), "had_original_signals": "signals" in case, **info}
    return "ok", control, info


def load_raw_text(case: dict, cache_dir: Path = CACHE_DIR) -> str | None:
    from scripts.source_eval_judgments import html_to_text

    docid = (case.get("source") or {}).get("docid") or str(case["case_id"]).split("-")[-1]
    f = cache_dir / f"{docid}.json"
    if not f.exists():
        return None
    try:
        return html_to_text(json.loads(f.read_text(encoding="utf-8")).get("doc", ""))
    except (OSError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(DATA / "eval_judgments_with_signals.json"))
    ap.add_argument("--verdicts", default=None, help="verdict file used to pick judged cases (default: the v2 file if present)")
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--max-fraction", type=float, default=0.6, help="never keep more than this fraction of the judgment")
    ap.add_argument("--out-dir", default=str(DATA))
    ap.add_argument("--cache-dir", default=str(CACHE_DIR))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    ds = Path(args.dataset)
    vpath = Path(args.verdicts) if args.verdicts else (
        DATA / "real_judgment_verdict_comparison_v2.json" if (DATA / "real_judgment_verdict_comparison_v2.json").exists()
        else DATA / "real_judgment_verdict_comparison.json")
    for label, path in (("dataset", ds), ("verdicts", vpath)):
        if not path.exists():
            print(f"Not found ({label}): {path}")
            return 1
    from app import llm
    if not llm.is_available():
        print("ERROR: LLM unavailable (needs SARVAM_API_KEY, or a local Ollama). Aborting.")
        return 1

    cases = json.loads(ds.read_text(encoding="utf-8"))
    verdicts = {v["case_id"]: v for v in json.loads(vpath.read_text(encoding="utf-8"))}
    sample = select_sample(cases, verdicts, args.n, args.seed)
    print(f"Sampled {len(sample)} judged cases (stratified by category x real outcome, seed {args.seed}).")

    control_path, original_path = out_dir / "eval_leakfree_control.json", out_dir / "eval_leakfree_original.json"
    progress_path, report_path = out_dir / "leakfree_control_progress.json", out_dir / "leakfree_control_report.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
    controls = {c["case_id"]: c for c in (json.loads(control_path.read_text(encoding="utf-8")) if control_path.exists() else [])}
    cache_dir = Path(args.cache_dir)

    def save() -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(json.dumps(progress, indent=1), encoding="utf-8")
        control_path.write_text(json.dumps(list(controls.values()), indent=1, ensure_ascii=False), encoding="utf-8")
        by_id = {c["case_id"]: c for c in sample}
        original_path.write_text(json.dumps([by_id[i] for i in controls if i in by_id], indent=1, ensure_ascii=False), encoding="utf-8")

    for i, case in enumerate(sample, 1):
        cid = case["case_id"]
        if cid in progress and progress[cid].get("status") in ("ok", "no_text", "too_short", "leak_excluded"):
            continue
        status, control, info = build_one(case, load_raw_text(case, cache_dir), max_fraction=args.max_fraction)
        progress[cid] = {"status": status, **info}
        if control is not None:
            controls[cid] = control
        if i % 10 == 0:
            save()
            print(f"  {i}/{len(sample)}  ok={sum(1 for p in progress.values() if p['status'] == 'ok')}")
    save()

    counts = Counter(p["status"] for p in progress.values())
    cut_reasons = Counter(p.get("cut_reason", "").split(":")[0].split(" ")[0] for p in progress.values() if p.get("cut_reason"))
    kept = sorted(p["kept_chars"] / p["total_chars"] for p in progress.values() if p.get("kept_chars"))
    report = {"n_sampled": len(sample), "status": dict(counts), "cut_reasons": dict(cut_reasons),
              "median_kept_fraction": kept[len(kept) // 2] if kept else None, "seed": args.seed, "max_fraction": args.max_fraction}
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nStatus: {dict(counts)}")
    if kept:
        print(f"Median share of each judgment kept (the rest, including the decision, was withheld): {100 * kept[len(kept) // 2]:.0f}%")
    print(f"Wrote {len(controls)} control case(s) -> {control_path}\n      originals of the same cases -> {original_path}")
    print("Next: see docs/LEAKAGE_FREE_CONTROL.md (score the control set, then run scripts.leakfree_control_report).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
