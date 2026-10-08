"""Summarise every manual spot-check run saved by scripts/spot_check_verdicts.py.

Each run of that script wrote data_cache/spot_check_results_<UTC timestamp>.json. This
prints, per file, how many cases it covered and how many of your labels agreed with the
grader, then the distinct cases across all files (a later run replaces an earlier answer
for the same case) and every disagreement.

Run (from backend/): python -m scripts.summarise_spot_checks
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data_cache"


def main() -> int:
    files = sorted(glob.glob(str(DATA / "spot_check_results_*.json")))
    if not files:
        print(f"No spot_check_results_*.json files found in {DATA}.")
        return 1
    all_rows: dict[str, dict] = {}
    for f in files:
        rows = json.loads(Path(f).read_text(encoding="utf-8"))
        ag = sum(r["agree"] for r in rows)
        print(f"{Path(f).name}: {len(rows)} cases, {ag} agree ({100 * ag / max(len(rows), 1):.0f}%)")
        for r in rows:
            all_rows[r["case_id"]] = r
    ag = sum(r["agree"] for r in all_rows.values())
    print(f"\nDISTINCT cases across {len(files)} file(s): {len(all_rows)}, "
          f"agree: {ag} ({100 * ag / max(len(all_rows), 1):.0f}%)")
    print("\nDisagreements:")
    for r in all_rows.values():
        if not r["agree"]:
            print(f"  {r['case_id']} ({r.get('category')}): you={r['manual_label']}  grader={r['automated_verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
