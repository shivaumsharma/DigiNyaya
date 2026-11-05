"""Audit the safety gate's two biggest escalation triggers on the real-judgment corpus. No LLM calls.

Over 23,841 civil judgments the gate escalated 8,266 cases: 5,650 for `agent_disagreement` and 2,438 for
`criminal_matter_detected`. Escalation is meant to catch cases the system would get wrong or must not decide, so the
question is: would the pipeline have been WORSE on the cases the gate held back?

Method (all scripted, deterministic):
  * The real winner comes from the stored `expected_outcome` text with a conservative keyword labeller
    (`text_label`). On the cases the judge also labelled it agrees 98.8% of the time, and it labels about 79%
    of cases; the rest are left out, never guessed.
  * The pipeline's winner is `mediation.type != "dismissed"` (the relief type the mediation agent settled on).
  * For cases the gate held back, the trigger is switched off for this audit only, to see what the draft WOULD have
    said. Production code is not touched.
Reported: per-keyword counts for the criminal trigger and how many cases it catches on a single, uncorroborated word;
the accuracy the pipeline would have had on held-back cases versus on the cases it did resolve.

IMPORTANT: criminal-matter escalation is deliberately unconditional (see the long comment in app/core/safety_gate.py):
a released criminal matter is far worse than an extra human review. This audit only measures; tightening the keyword
list is a decision for a human reviewer, and any change must be checked against genuine criminal cases first.

Run from backend/:  python -m scripts.gate_audit --out data_cache/gate_audit.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
_NEG = re.compile(r"\b(dismiss(?:ed|ing)?|rejected|refus(?:ed|al)|not entitled|declin(?:ed|e)|no relief|failed to prove|"
                  r"disallowed|struck off|non-?suit(?:ed)?)\b", re.IGNORECASE)
_POS = re.compile(r"\b(decree[ds]?|allowed|granted|awarded|directed|ordered|entitled|upheld|partly allowed)\b", re.IGNORECASE)
CORROBORATION = re.compile(r"\b(fir|f\.i\.r|police|criminal|ipc|indian penal code|bnss|bns|cr\.?p\.?c|section\s*(?:4(?:6|2|7|8)\d|34|120)\b|"
                           r"cheating|arrest|chargesheet|charge sheet|complaint under section)\b", re.IGNORECASE)
SINGLE_WORD = {"forged", "forgery"}


def text_label(expected_outcome: str) -> bool | None:
    """True = claimant prevailed, False = respondent, None = cannot tell (never guessed)."""
    neg, pos = bool(_NEG.search(expected_outcome or "")), bool(_POS.search(expected_outcome or ""))
    if neg and not pos:
        return False
    if pos and not neg:
        return True
    return None


def accuracy(rows: list[dict]) -> dict:
    rows = [r for r in rows if r.get("real") is not None and r.get("pred") is not None]
    n = len(rows)
    if not n:
        return {"n": 0}
    ok = sum(1 for r in rows if r["pred"] == r["real"])
    resp = [r for r in rows if r["real"] is False]
    return {"n": n, "accuracy": ok / n, "claimant_rate_real": sum(1 for r in rows if r["real"]) / n,
            "respondent_cases": len(resp), "respondent_recall": (sum(1 for r in resp if r["pred"] is False) / len(resp)) if resp else None}


def corroborated(text: str) -> bool:
    return bool(CORROBORATION.search(text or ""))


def snippet(text: str, word: str, width: int = 90) -> str:
    m = re.search(re.escape(word), text, re.IGNORECASE)
    if not m:
        return text[: 2 * width]
    return text[max(0, m.start() - width): m.end() + width].replace("\n", " ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(DATA / "eval_judgments_with_signals.json"))
    ap.add_argument("--results", default=str(DATA / "real_judgment_eval_results.json"))
    ap.add_argument("--out", default=str(DATA / "gate_audit.json"))
    ap.add_argument("--limit", type=int, default=0, help="debug: only the first N cases")
    args = ap.parse_args()

    import os
    os.environ["DIGINYAYA_USE_LLM"] = "0"
    from app.core import graph, safety_gate
    from scripts.run_real_judgment_eval import _build_ctx

    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[: args.limit]
    res = {r["case_id"]: r for r in json.loads(Path(args.results).read_text(encoding="utf-8"))}
    original_criminal = safety_gate._check_criminal_matter

    resolved, held_dis, held_crim = [], [], []
    kw = Counter()
    single_only = corroborated_only = uncorroborated = 0
    samples: list[dict] = []
    directions = {"favourable precedents / claimant weak": [], "unfavourable precedents / claimant strong": [], "other": []}

    for c in cases:
        r = res.get(c["case_id"])
        if r is None:
            continue
        real = text_label(c.get("expected_outcome", ""))
        conds = r.get("escalation_conditions") or []
        if r.get("resolved"):
            ctx = _build_ctx(c)
            list(graph.run_pipeline(ctx))
            if ctx.mediation is not None:
                resolved.append({"real": real, "pred": ctx.mediation.type != "dismissed"})
            continue
        if "criminal_matter_detected" in conds:
            ctx = _build_ctx(c)
            list(graph.run_pipeline(ctx))  # production gate on: record the hits
            esc = ctx.escalation
            details = (esc.get("details") if isinstance(esc, dict) else getattr(esc, "details", None)) or {} if esc else {}
            detail = details.get("criminal_matter_detected", "")
            m = re.search(r"detected: (.*)$", detail)
            hits = m.group(1).split(", ") if m else []
            for h in hits:
                kw[h] += 1
            text = f"{c.get('case_description', '')}\n{(c.get('signals') or {}).get('respondent_defense_summary') or ''}"
            if hits and set(hits) <= SINGLE_WORD:
                single_only += 1
                if corroborated(text):
                    corroborated_only += 1
                else:
                    uncorroborated += 1
                    if len(samples) < 12:
                        samples.append({"case_id": c["case_id"], "hits": hits, "context": snippet(text, hits[0])})
            safety_gate._check_criminal_matter = lambda t: None  # audit only: what would the draft have been?
            try:
                ctx2 = _build_ctx(c)
                list(graph.run_pipeline(ctx2))
            finally:
                safety_gate._check_criminal_matter = original_criminal
            if ctx2.mediation is not None:
                held_crim.append({"real": real, "pred": ctx2.mediation.type != "dismissed",
                                  "single_word_only": bool(hits) and set(hits) <= SINGLE_WORD,
                                  "corroborated": corroborated(text)})
        elif "agent_disagreement" in conds:
            ctx = _build_ctx(c)
            list(graph.run_pipeline(ctx))
            if ctx.mediation is None or ctx.analysis is None or ctx.research is None:
                continue
            ratios = [p.relief_amount_ratio for p in ctx.research.precedents if isinstance(p.relief_amount_ratio, (int, float))]
            mean_ratio = sum(ratios) / len(ratios) if ratios else None
            row = {"real": real, "pred": ctx.mediation.type != "dismissed"}
            held_dis.append(row)
            c_strength = ctx.analysis.strength_score.get("claimant", 0)
            if mean_ratio is not None and mean_ratio >= safety_gate.AGREEMENT_RATIO_HIGH and c_strength < 0.5:
                directions["favourable precedents / claimant weak"].append(row)
            elif mean_ratio is not None and mean_ratio <= safety_gate.AGREEMENT_RATIO_LOW and c_strength >= 0.75:
                directions["unfavourable precedents / claimant strong"].append(row)
            else:
                directions["other"].append(row)

    report = {
        "resolved_by_pipeline": accuracy(resolved),
        "criminal": {
            "flagged_cases_seen": len(held_crim),
            "keyword_counts": dict(kw.most_common()),
            "flagged_on_forged_or_forgery_only": single_only,
            "of_which_corroborated_by_police_fir_ipc_etc": corroborated_only,
            "of_which_uncorroborated": uncorroborated,
            "draft_accuracy_if_released_all": accuracy(held_crim),
            "draft_accuracy_if_released_uncorroborated_single_word": accuracy(
                [r for r in held_crim if r["single_word_only"] and not r["corroborated"]]),
            "sample_uncorroborated_contexts": samples,
        },
        "agent_disagreement": {
            "held_back": len(held_dis),
            "draft_accuracy_if_released": accuracy(held_dis),
            "by_direction": {k: accuracy(v) for k, v in directions.items()},
        },
    }
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")

    def pc(x):
        return "n/a" if x is None else f"{100 * x:.1f}%"

    base = report["resolved_by_pipeline"]
    print(f"Resolved cases the pipeline drafted: n={base.get('n')}, winner accuracy {pc(base.get('accuracy'))}, "
          f"respondent recall {pc(base.get('respondent_recall'))}")
    cr = report["criminal"]
    print(f"\nCRIMINAL trigger: {cr['flagged_cases_seen']} cases. Keywords: {cr['keyword_counts']}")
    print(f"  flagged ONLY on 'forged'/'forgery': {cr['flagged_on_forged_or_forgery_only']} "
          f"(corroborated by police/FIR/IPC/cheating words: {cr['of_which_corroborated_by_police_fir_ipc_etc']}; "
          f"uncorroborated: {cr['of_which_uncorroborated']})")
    for label, key in (("all held-back cases", "draft_accuracy_if_released_all"),
                       ("uncorroborated single-word cases", "draft_accuracy_if_released_uncorroborated_single_word")):
        a = cr[key]
        print(f"  if released, {label}: n={a.get('n')}, accuracy {pc(a.get('accuracy'))}")
    dg = report["agent_disagreement"]
    a = dg["draft_accuracy_if_released"]
    print(f"\nAGENT DISAGREEMENT trigger: {dg['held_back']} cases held back; draft accuracy if released n={a.get('n')}, "
          f"{pc(a.get('accuracy'))} (resolved cases: {pc(base.get('accuracy'))})")
    for k, v in dg["by_direction"].items():
        print(f"  {k}: n={v.get('n')}, accuracy {pc(v.get('accuracy'))}")
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
