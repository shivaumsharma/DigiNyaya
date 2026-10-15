"""Find real cases where the OLD judge marked a mismatch that the FIXED judge no longer does, and whose old explanation
mentions the provisional / non-binding wording. This is the evidence for the "judge read the disclaimer as a refusal to
commit" bug, taken from your own verdict files, not from memory.

Needs two verdict files over the same cases, one scored before the judge-prompt fix and one after (the files written by
scripts.judge_real_outcomes; each row carries `judge_prompt_hash`, `reason`, `ai_relief`, `real_outcome`, `ai_hash`).
Only cases whose AI output is identical in both runs (same `ai_hash`) are considered, so a change of verdict can only come
from the judge, not from the pipeline.

It prints, per candidate: the case id, the old verdict and reason, the new verdict and reason, the real court outcome and the
AI relief. Read each one yourself: a verdict flip is a candidate, and only a case where the AI's order really did give
the same winner as the court is a clean example of the bug.

Run (from backend/):
  python -m scripts.find_disclaimer_misjudgments --before data_cache\\<old verdicts>.json --after data_cache\\<new verdicts>.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

KEYWORDS = re.compile(r"provisional|non-?binding|recommend|not (?:a )?(?:final|binding|enforceable)|pending (?:human|counter)|tier 2|sign-?off", re.I)
GOOD = ("match", "partial", "mismatch")


def find_candidates(before: list[dict], after: list[dict]) -> dict:
    b = {r["case_id"]: r for r in before if r.get("verdict") in GOOD}
    a = {r["case_id"]: r for r in after if r.get("verdict") in GOOD}
    common = sorted(set(b) & set(a))
    same_output = [c for c in common if b[c].get("ai_hash") and b[c].get("ai_hash") == a[c].get("ai_hash")]
    flipped = [c for c in same_output if b[c]["verdict"] == "mismatch" and a[c]["verdict"] != "mismatch"]
    mentions = [c for c in flipped if KEYWORDS.search(b[c].get("reason") or "")]
    return {"common": len(common), "same_ai_output": len(same_output), "flipped_mismatch_to_agree": flipped,
            "flipped_and_old_reason_mentions_disclaimer": mentions, "before": b, "after": a,
            "prompt_hash_before": sorted({r.get("judge_prompt_hash") for r in b.values()} - {None}),
            "prompt_hash_after": sorted({r.get("judge_prompt_hash") for r in a.values()} - {None})}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--show", type=int, default=5)
    args = ap.parse_args()
    for p in (args.before, args.after):
        if not Path(p).exists():
            print(f"Not found: {p}")
            return 1
    res = find_candidates(json.loads(Path(args.before).read_text(encoding="utf-8")),
                          json.loads(Path(args.after).read_text(encoding="utf-8")))
    print(f"judge prompt hash before: {res['prompt_hash_before']}   after: {res['prompt_hash_after']}")
    if res["prompt_hash_before"] == res["prompt_hash_after"]:
        print("WARNING: same judge prompt hash in both files, so these runs did not differ in the judge prompt.")
    print(f"Cases in both: {res['common']:,}; identical AI output in both: {res['same_ai_output']:,}")
    f, m = res["flipped_mismatch_to_agree"], res["flipped_and_old_reason_mentions_disclaimer"]
    print(f"Old 'mismatch' that the new judge no longer calls a mismatch (same AI output): {len(f):,}")
    print(f"  ...of which the OLD reason mentions provisional / non-binding / recommendation wording: {len(m):,}\n")
    shown = m[: args.show] or f[: args.show]
    label = "old reason mentions the disclaimer" if m else "NO old reason mentions it; showing plain flips (not evidence of this bug)"
    print(f"Examples ({label}):")
    for cid in shown:
        o, n = res["before"][cid], res["after"][cid]
        print(f"\n- {cid}")
        print(f"  OLD {o['verdict']}: {o.get('reason')}")
        print(f"  NEW {n['verdict']}: {n.get('reason')}")
        print(f"  real court : {(o.get('real_outcome') or '')[:200]}")
        print(f"  AI relief  : {o.get('ai_relief')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
