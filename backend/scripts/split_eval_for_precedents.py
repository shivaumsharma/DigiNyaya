"""Split the real-judgment eval corpus into a HELD-OUT evaluation set and a PRECEDENT-SOURCE set.

Phase 2 of the civil expansion builds native precedent corpora (tenancy, employment, property) from real
judgments. If a case can be both retrieved as a precedent AND scored in the eval, the pipeline can "find" the
very judgment it is being tested on, and every number is contaminated. This script fixes the partition BEFORE
any precedent is built:

  * deterministic AND stable: a case's side is decided by a hash of (seed, case_id) alone, compared with a fixed
    threshold. It does not depend on any other case, so re-running, reordering, running on another machine, or
    ADDING more cases later never moves an existing case across the line (rank- or quota-based splitting cannot
    promise that, which is why it is not used)
  * applied per category: each named category contributes about --source-fraction of its cases (the exact count
    varies a little, like any random split; a category that ends up below --min-source is reported so you can
    decide whether it is big enough to build a corpus from)
  * only the categories you name are split (default: the three expansion categories); everything else is left
    entirely in the held-out set, i.e. nothing about the existing evaluation changes

Writes two JSON files next to the input plus a manifest with counts and the seed. Refuses to overwrite existing
outputs unless --force, because moving cases between the sets after precedents were built would re-introduce
the contamination.

Run (from backend/):
  python -m scripts.split_eval_for_precedents --input data_cache/eval_judgments_with_signals.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
DEFAULT_CATEGORIES = ("tenancy_disputes", "employment_disputes", "property_neighbor_disputes")


def bucket(seed: int, case_id: str) -> float:
    """A stable number in [0, 1) for this case, independent of every other case."""
    digest = hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16 ** 12)


def split_cases(cases: list[dict], categories: tuple[str, ...], source_fraction: float, seed: int):
    """Return (heldout, source). A case is precedent-source iff its category is being split and its hash bucket is
    below `source_fraction`; every other case stays held-out. Depends on nothing but (seed, case_id, category)."""
    cats = set(categories)
    source = [c for c in cases if c["category"] in cats and bucket(seed, c["case_id"]) < source_fraction]
    source_ids = {c["case_id"] for c in source}
    heldout = [c for c in cases if c["case_id"] not in source_ids]
    return heldout, source


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=str(DATA / "eval_judgments_with_signals.json"))
    ap.add_argument("--heldout-out", default=str(DATA / "eval_heldout.json"))
    ap.add_argument("--source-out", default=str(DATA / "precedent_source.json"))
    ap.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    ap.add_argument("--source-fraction", type=float, default=0.3)
    ap.add_argument("--min-source", type=int, default=30, help="warn when a category has fewer precedent-source cases than this")
    ap.add_argument("--seed", type=int, default=20261004)
    ap.add_argument("--force", action="store_true", help="overwrite existing outputs (this can re-introduce contamination)")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"Not found: {src}")
        return 1
    outs = [Path(args.heldout_out), Path(args.source_out), Path(args.heldout_out).with_suffix(".manifest.json")]
    existing = [o for o in outs if o.exists()]
    if existing and not args.force:
        print("Refusing to overwrite: " + ", ".join(str(o) for o in existing)
              + "\nMoving cases between the sets after precedents were built re-introduces contamination. Use --force only if you will rebuild every precedent.")
        return 1

    cases = json.loads(src.read_text(encoding="utf-8"))
    heldout, source = split_cases(cases, tuple(args.categories), args.source_fraction, args.seed)
    assert not ({c["case_id"] for c in heldout} & {c["case_id"] for c in source})
    assert len(heldout) + len(source) == len(cases)

    outs[0].write_text(json.dumps(heldout, indent=2, ensure_ascii=False), encoding="utf-8")
    outs[1].write_text(json.dumps(source, indent=2, ensure_ascii=False), encoding="utf-8")
    counts = lambda rows: {k: sum(1 for r in rows if r["category"] == k) for k in sorted({r["category"] for r in rows})}  # noqa: E731
    outs[2].write_text(json.dumps({"seed": args.seed, "source_fraction": args.source_fraction, "min_source": args.min_source,
                                   "categories_split": args.categories, "input": str(src), "n_input": len(cases),
                                   "heldout": counts(heldout), "precedent_source": counts(source)}, indent=2), encoding="utf-8")
    print(f"{len(cases)} cases -> {len(heldout)} held-out, {len(source)} precedent-source")
    for cat in args.categories:
        n_src = counts(source).get(cat, 0)
        note = f"   <-- fewer than --min-source ({args.min_source}); may be too small to build a corpus from" if n_src < args.min_source else ""
        print(f"  {cat:30s} source={n_src:5d}  held-out={counts(heldout).get(cat, 0):5d}{note}")
    print(f"\nWrote {outs[0]}\n      {outs[1]}\n      {outs[2]}")
    print("Point evaluation at the held-out file: DIGINYAYA_EVAL_DATASET=" + str(outs[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
