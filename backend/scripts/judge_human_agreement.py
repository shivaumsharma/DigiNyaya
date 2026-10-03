"""Score the LLM judge against a human's hand labels.

Reads the CSV filled in from scripts/build_human_label_sheet.py and the hidden
key of the judge's own answers, and reports raw agreement and Cohen's kappa
(chance-corrected -- raw agreement flatters a judge when one answer, e.g.
"claimant prevailed = Y", is common) for each of the judge's three questions,
plus agreement on the derived verdict (match / partial / mismatch).

relief_similar is only scored on rows where the human marked both
claimant_prevailed_* as Y (that is the only case the judge's verdict uses it).

Run (from backend/): python -m scripts.judge_human_agreement --labels data_cache/human_label_sheet.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data_cache"


def _yn(v: str):
    v = (v or "").strip().lower()
    if v in ("y", "yes", "true", "1"):
        return True
    if v in ("n", "no", "false", "0"):
        return False
    return None


def kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe < 1 else None


def verdict_of(ai: bool, real: bool, similar: bool | None) -> str:
    if ai != real:
        return "mismatch"
    return "match" if similar else "partial"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    ap.add_argument("--key", default=str(DATA / "human_label_key.json"))
    args = ap.parse_args()

    key = json.loads(Path(args.key).read_text(encoding="utf-8"))
    with open(args.labels, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    fields = [("claimant_prevailed_ai", "Did the claimant win under the AI decision"),
              ("claimant_prevailed_real", "Did the claimant win under the real decision")]
    labelled = 0
    verdict_pairs: list[tuple[str, str]] = []
    per_field: dict[str, list[tuple[bool, bool]]] = {f: [] for f, _ in fields}
    sim_pairs: list[tuple[bool, bool]] = []
    for r in rows:
        k = key.get(r["case_id"])
        h_ai, h_real = _yn(r["claimant_prevailed_ai"]), _yn(r["claimant_prevailed_real"])
        if not k or h_ai is None or h_real is None:
            continue
        labelled += 1
        per_field["claimant_prevailed_ai"].append((h_ai, k["claimant_prevailed_ai"]))
        per_field["claimant_prevailed_real"].append((h_real, k["claimant_prevailed_real"]))
        h_sim = _yn(r["relief_similar"]) if (h_ai and h_real) else None
        if h_ai and h_real and h_sim is not None:
            sim_pairs.append((h_sim, k["relief_similar"]))
        verdict_pairs.append((verdict_of(h_ai, h_real, h_sim), k["verdict"]))

    print(f"{labelled} labelled row(s) of {len(rows)}.\n")
    print(f"{'question':52s} {'n':>4s} {'agree%':>7s} {'kappa':>7s}")
    for f, label in fields:
        p = per_field[f]
        print(f"{label:52s} {len(p):4d} {100 * sum(a == b for a, b in p) / max(len(p), 1):6.1f}% "
              f"{(kappa(p) if kappa(p) is not None else float('nan')):7.2f}")
    print(f"{'Is the relief comparable (both won only)':52s} {len(sim_pairs):4d} "
          f"{100 * sum(a == b for a, b in sim_pairs) / max(len(sim_pairs), 1):6.1f}% "
          f"{(kappa(sim_pairs) if kappa(sim_pairs) is not None else float('nan')):7.2f}")
    agree_v = sum(a == b for a, b in verdict_pairs) / max(len(verdict_pairs), 1)
    print(f"\nDerived verdict (match/partial/mismatch) agreement: {100 * agree_v:.1f}% over {len(verdict_pairs)} rows")
    print("Rule of thumb: kappa >= 0.8 strong, 0.6-0.8 substantial, < 0.6 the judge is not reliable enough to lean on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
