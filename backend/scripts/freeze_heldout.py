"""Freeze a held-out TEST set of contested cases, once, before any architecture change is tuned.

Each case is assigned to dev or test by a hash of its case_id (plus a fixed salt), so the split is deterministic, stable as
the corpus grows, and independent of row order. Only contested cases (disposal decreed / dismissed, from
scripts.tag_disposal) are split; ex parte decrees and no-merits endings are listed separately and kept out of both sets.

The manifest (data_cache/heldout_manifest.json) records the salt, the fraction, the id lists and a digest of the test ids.
The script refuses to overwrite an existing manifest without --force, because re-freezing after looking at test results
defeats the purpose. Use `--split dev` while developing and touch `--split test` only for a final, reported comparison.

HONEST LIMIT: every number measured so far (README, docs/EVALUATION_FINDINGS.md) was computed on ALL cases, so the test
set is clean only for changes designed from now on. It does not retroactively make the existing figures held-out.

Run (from backend/):  python -m scripts.freeze_heldout
                      python -m scripts.accuracy_by_disposal --split dev     (or --split test)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"
CONTESTED = {"decreed", "dismissed"}
DEFAULT_SALT = "diginyaya-heldout-v1"


def is_test(case_id: str, fraction: float, salt: str = DEFAULT_SALT) -> bool:
    h = int(hashlib.sha256(f"{salt}|{case_id}".encode()).hexdigest()[:12], 16) / float(16**12)
    return h < fraction


def build_manifest(tagged_cases: list[dict], fraction: float, salt: str = DEFAULT_SALT) -> dict:
    test, dev, other = [], [], []
    by_cat: dict[str, dict[str, int]] = {}
    for c in tagged_cases:
        cid = c["case_id"]
        disposal = (c.get("source") or {}).get("disposal")
        if disposal not in CONTESTED:
            other.append(cid)
            continue
        split = "test" if is_test(cid, fraction, salt) else "dev"
        (test if split == "test" else dev).append(cid)
        by_cat.setdefault(c.get("category", "unknown"), {"dev": 0, "test": 0})[split] += 1
    test.sort()
    return {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "salt": salt, "fraction": fraction,
            "test_ids": test, "dev_ids": sorted(dev), "not_contested_ids": len(other),
            "by_category": by_cat, "test_digest": hashlib.sha256("\n".join(test).encode()).hexdigest()}


def load_split(manifest_path: Path, split: str) -> set[str]:
    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return set(m["test_ids"] if split == "test" else m["dev_ids"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(DATA / "eval_judgments.json"))
    ap.add_argument("--cache-dir", default=str(DATA / "indiankanoon"))
    ap.add_argument("--fraction", type=float, default=0.3, help="share of contested cases frozen as test")
    ap.add_argument("--out", default=str(DATA / "heldout_manifest.json"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} already exists. Not overwriting: re-freezing after seeing test results defeats the purpose.")
        return 1
    cp = Path(args.cases)
    if not cp.exists():
        print(f"Not found: {cp}")
        return 1
    from scripts.tag_disposal import tag_cases

    cases = [c for c in json.loads(cp.read_text(encoding="utf-8")) if str(c.get("case_id", "")).startswith("DC-EVAL-")]
    tagged, _ = tag_cases(cases, Path(args.cache_dir))
    m = build_manifest(tagged, args.fraction)
    out.write_text(json.dumps(m), encoding="utf-8")
    print(f"Contested cases: {len(m['test_ids']) + len(m['dev_ids']):,}  ->  test {len(m['test_ids']):,}, dev {len(m['dev_ids']):,}  "
          f"(ex parte / no-merits / unknown, in neither: {m['not_contested_ids']:,})")
    print(f"{'category':32s}{'dev':>7s}{'test':>7s}")
    for k, v in sorted(m["by_category"].items()):
        print(f"{k:32s}{v['dev']:>7d}{v['test']:>7d}" + ("   <- under 30 test cases: too thin to report alone" if v["test"] < 30 else ""))
    print(f"\nTest-set digest {m['test_digest'][:16]}...  Saved {out}")
    print("Develop against --split dev. Look at --split test only for the final comparison, and say so when you report it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
