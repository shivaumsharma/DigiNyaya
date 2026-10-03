"""Draw a stratified random sample from the full real-judgment eval corpus, for
fast before/after testing of a pipeline change without paying for another
23,841-case run (~4 hours of judging).

Stratified by category so the small categories (tenancy, consumer, employment,
partnership) aren't drowned out: each category contributes its proportional
share, but never fewer than --min-per-category cases (or all of them, if the
category is smaller than that). Seeded, so the same sample is reproducible.

Reads eval_judgments_with_signals.json (the signal-enriched full corpus) and
writes eval_sample.json next to it. Point the eval scripts at it with
DIGINYAYA_EVAL_DATASET=<path> -- the canonical files are never touched.

Run (from backend/): python -m scripts.build_eval_sample --size 1600
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, ".")

DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
FULL_PATH = DATA_DIR / "eval_judgments_with_signals.json"
SAMPLE_PATH = DATA_DIR / "eval_sample.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a stratified eval sample.")
    ap.add_argument("--size", type=int, default=1600, help="approximate total sample size")
    ap.add_argument("--min-per-category", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    cases = json.loads(FULL_PATH.read_text(encoding="utf-8"))
    by_cat: dict[str, list[dict]] = collections.defaultdict(list)
    for c in cases:
        by_cat[c["category"]].append(c)

    rng = random.Random(args.seed)
    sample: list[dict] = []
    for cat, group in sorted(by_cat.items()):
        share = round(args.size * len(group) / len(cases))
        take = min(len(group), max(share, args.min_per_category))
        sample.extend(rng.sample(group, take))
        print(f"{cat:34s} {len(group):6d} in corpus -> {take:5d} sampled")

    rng.shuffle(sample)
    SAMPLE_PATH.write_text(json.dumps(sample, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(sample)} case(s) -> {SAMPLE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
