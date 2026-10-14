"""Tag already-sourced district-court cases with how they ended (ex parte decree, contested decree, dismissed, ...).

Uses the raw judgment text already cached in data_cache/indiankanoon/<docid>.json by the sourcing scripts, so it needs no
LLM and no network. The tag is the same keyword heuristic the sourcing script now applies to new cases
(scripts.source_district_court_judgments.classify_disposal). It answers one question about any corpus: what share of
the cases are ex parte decrees, where the defendant never appeared and the claimant wins almost by default?

Nothing canonical is modified. The report goes to data_cache/disposal_report.json; --write-tagged writes a NEW copy of
the cases with source.disposal filled in.

Run (from backend/):
  python -m scripts.tag_disposal                                  # the canonical eval_judgments.json
  python -m scripts.tag_disposal --cases data_cache\\dc_slices\\pilot_2_2023.json --write-tagged data_cache\\pilot_tagged.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
CACHE = DATA / "indiankanoon"


def tag_cases(cases: list[dict], cache_dir: Path = CACHE) -> tuple[list[dict], dict]:
    """Returns (cases with source.disposal where the cached text exists, summary). Cases without cached text stay
    untagged and are counted, so the shares are never computed over a silently smaller set."""
    from scripts.source_district_court_judgments import classify_disposal

    out, counts, by_cat, no_text = [], collections.Counter(), collections.defaultdict(collections.Counter), 0
    for c in cases:
        c = {**c, "source": dict(c.get("source") or {})}
        path = cache_dir / f"{c['source'].get('docid')}.json"
        text = None
        if path.exists():
            try:
                text = json.loads(path.read_text(encoding="utf-8")).get("doc")
            except (OSError, ValueError):
                text = None
        if not text:
            no_text += 1
        else:
            c["source"]["disposal"] = classify_disposal(text)
            counts[c["source"]["disposal"]] += 1
            by_cat[c.get("category", "unknown")][c["source"]["disposal"]] += 1
        out.append(c)
    tagged = sum(counts.values())
    return out, {"cases": len(cases), "tagged": tagged, "no_cached_text": no_text, "counts": dict(counts),
                 "shares": {k: v / tagged for k, v in counts.items()} if tagged else {},
                 "by_category": {k: dict(v) for k, v in by_cat.items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", nargs="*", default=[str(DATA / "eval_judgments.json")])
    ap.add_argument("--cache-dir", default=str(CACHE))
    ap.add_argument("--only-district", action="store_true", default=True,
                    help="only DC-EVAL-* cases (eCourts district courts); the IK-EVAL cases have no such disposal wording")
    ap.add_argument("--write-tagged", default=None, help="write a NEW file with source.disposal filled in")
    ap.add_argument("--report", default=str(DATA / "disposal_report.json"))
    args = ap.parse_args()

    cases: list[dict] = []
    for p in args.cases:
        path = Path(p)
        if not path.exists():
            print(f"Not found: {p}")
            return 1
        cases += json.loads(path.read_text(encoding="utf-8"))
    if args.only_district:
        cases = [c for c in cases if str(c.get("case_id", "")).startswith("DC-EVAL-")]
    if not cases:
        print("No district-court (DC-EVAL-*) cases found.")
        return 1

    tagged, s = tag_cases(cases, Path(args.cache_dir))
    print(f"{s['cases']} district-court case(s); {s['tagged']} tagged; {s['no_cached_text']} without cached text.\n")
    for k, v in sorted(s["counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:20s}{v:>7,}  {100 * s['shares'][k]:5.1f}%")
    ex = s["shares"].get("ex_parte_decreed", 0)
    print(f"\nEx parte decrees: {100 * ex:.1f}% of tagged cases."
          + (" Above 40%: report accuracy on contested cases separately." if ex > 0.4 else ""))
    print("\nBy category (ex parte share):")
    for cat, d in sorted(s["by_category"].items()):
        n = sum(d.values())
        print(f"  {cat:32s} n={n:<6d} ex parte {100 * d.get('ex_parte_decreed', 0) / n:5.1f}%")
    Path(args.report).write_text(json.dumps(s, indent=2), encoding="utf-8")
    if args.write_tagged:
        out = Path(args.write_tagged)
        if out.resolve() in {Path(p).resolve() for p in args.cases}:
            print("Refusing to overwrite an input file.")
            return 1
        out.write_text(json.dumps(tagged, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote tagged copy to {out}")
    print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
