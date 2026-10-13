"""LLM-free pilot: how many candidate cases yield usable text, before any LLM call is spent.

It follows the real sourcing path (same candidate filter, same archive download, same `extract_case_text`) for a few
state-year slices and stops before `extract_and_classify`. The result is an UPPER BOUND on the sourcing yield, because
the LLM stage can only reject more. It also shows what the text looks like (length, decision words, criminal terms).

It does NOT measure: the real pass rate, category mix, correctness of extracted outcomes, or anything the gate checks on
LLM-written descriptions. For those, run the real pilot (scripts/plan_corpus_scale.py --pilot) with an LLM available.

Two limits to keep in mind when reading the output:
  * the largest small-archive courts are chosen, so the sample is not a random draw of all candidates;
  * "extraction failed" usually means a scanned PDF with no text layer. Without Sarvam Document AI or Tesseract it stays
    failed; with Sarvam configured, some of those are recovered, so this number is conservative on that point.

Run (from backend/):  python -m scripts.pilot_extractability
                      python -m scripts.pilot_extractability --slices 2:2022,29:2022 --complexes 4 --per-complex 15
Needs data_cache/district_supply.json (python -m scripts.district_supply_census). Archives are cached under
data_cache/pilot_tars (git-ignored).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, ".")

DATA = Path(__file__).resolve().parent.parent / "data_cache"

SHORT_ORDER_CHARS = 1500
MIN_TEXT_CHARS = 200
# Crude proxies, NOT the LLM's is_final_merits_decision / is_genuine_civil_dispute judgments.
DECISION_WORDS = re.compile(r"\b(decreed|dismissed|disposed of|suit is|decree|judgment)\b", re.I)
CRIMINAL_TERMS = re.compile(r"\b(IPC|Cr\.?P\.?C|N\.?I\.? Act|Section 138|accused|bail|N\.?D\.?P\.?S)\b", re.I)


def classify_text(text: str | None) -> dict:
    """One row per attempted case: its extraction status plus cheap text proxies."""
    if text is None:
        return {"status": "no_text_or_member", "chars": 0, "decision_words": False, "criminal_terms": False}
    if len(text) < MIN_TEXT_CHARS:
        return {"status": "text_under_200", "chars": len(text), "decision_words": False, "criminal_terms": False}
    return {"status": "ok", "chars": len(text), "decision_words": bool(DECISION_WORDS.search(text)),
            "criminal_terms": bool(CRIMINAL_TERMS.search(text))}


def choose_complexes(supply: list[dict], state: str, year: str, *, max_tar_mb: float, count: int, min_candidates: int = 15) -> list[dict]:
    """Largest complexes (by candidate count) whose archive exists and is small enough to fetch quickly."""
    pool = [g for g in supply if g["state"] == state and g["year"] == year and g.get("tar_bytes")
            and g["tar_bytes"] <= max_tar_mb * 1e6 and g["candidates"] >= min_candidates]
    return sorted(pool, key=lambda g: -g["candidates"])[:count]


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "ok"]
    n = len(rows)
    out = {"attempted": n, "by_status": {}, "extractable": len(ok), "extractable_rate": len(ok) / n if n else None}
    for r in rows:
        out["by_status"][r["status"]] = out["by_status"].get(r["status"], 0) + 1
    slices: dict[tuple, list[bool]] = {}
    for r in rows:
        slices.setdefault((r["state"], r["year"]), []).append(r["status"] == "ok")
    out["by_slice"] = {f"{s}/{y}": {"attempted": len(v), "extractable_rate": sum(v) / len(v)} for (s, y), v in sorted(slices.items())}
    if ok:
        chars = sorted(r["chars"] for r in ok)
        out["chars"] = {"median": int(statistics.median(chars)), "p25": chars[len(chars) // 4], "p75": chars[(3 * len(chars)) // 4]}
        out["short_order_share"] = sum(r["chars"] < SHORT_ORDER_CHARS for r in ok) / len(ok)
        out["decision_words_share"] = sum(r["decision_words"] for r in ok) / len(ok)
        out["criminal_terms_share"] = sum(r["criminal_terms"] for r in ok) / len(ok)
    return out


def _parse_slices(spec: str) -> list[tuple[str, str]]:
    return [tuple(part.strip().split(":", 1)) for part in spec.split(",") if ":" in part]  # type: ignore[misc]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--supply", default=str(DATA / "district_supply.json"))
    ap.add_argument("--slices", default="2:2022,29:2022,2:2023", help="comma-separated state:year pairs")
    ap.add_argument("--complexes", type=int, default=6, help="court complexes per slice")
    ap.add_argument("--per-complex", type=int, default=20, help="candidate cases tried per complex")
    ap.add_argument("--max-tar-mb", type=float, default=60.0)
    ap.add_argument("--cache-dir", default=str(DATA / "pilot_tars"))
    ap.add_argument("--out", default=str(DATA / "pilot_extractability.json"))
    args = ap.parse_args()

    supply_path = Path(args.supply)
    if not supply_path.exists():
        print(f"{supply_path} not found. Run: python -m scripts.district_supply_census")
        return 1
    supply = json.loads(supply_path.read_text(encoding="utf-8"))

    import scripts.source_district_court_judgments as src

    src.TAR_CACHE_DIR = Path(args.cache_dir)
    s3 = src.get_s3_client()
    rows: list[dict] = []
    for state, year in _parse_slices(args.slices):
        chosen = choose_complexes(supply, state, year, max_tar_mb=args.max_tar_mb, count=args.complexes)
        print(f"slice state={state} year={year}: {len(chosen)} complex(es) chosen", flush=True)
        if not chosen:
            continue
        meta = src.load_metadata(s3, state, year)
        if meta is None:
            continue
        ct = meta["case_type"].fillna("").astype(str).str.strip().str.upper()
        civil = meta[ct.isin(src._civil_codes_for_state(state)) & meta["has_pdf"] & (meta["final_orders"] > 0)]
        for g in chosen:
            sub = civil[(civil["district_code"].astype(str) == g["district"]) & (civil["complex_code"].astype(str) == g["complex"])]
            tar = src.download_complex_tar(s3, year, state, g["district"], g["complex"])
            for _, r in sub.head(args.per_complex).iterrows():
                if tar is None:
                    row = {"status": "tar_missing", "chars": 0, "decision_words": False, "criminal_terms": False}
                else:
                    try:
                        row = classify_text(src.extract_case_text(tar, str(r["case_no"])))
                    except Exception:
                        row = classify_text(None)
                rows.append({"state": state, "year": year, **row})

    if not rows:
        print("Nothing attempted.")
        return 1
    s = summarise(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(s, indent=2), encoding="utf-8")
    print(f"\nattempted: {s['attempted']}")
    for k, v in sorted(s["by_status"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:22s}{v:>6d}")
    print("\nshare extractable by slice:")
    for k, v in s["by_slice"].items():
        print(f"  {k:12s} n={v['attempted']:<4d} {100 * v['extractable_rate']:5.1f}%")
    print(f"\nUPPER BOUND on sourcing yield (text extractable): {100 * s['extractable_rate']:.1f}% "
          f"({s['extractable']} of {s['attempted']}). The LLM filter can only lower this.")
    if s["extractable"]:
        c = s["chars"]
        print(f"text length: median {c['median']:,} chars (p25 {c['p25']:,}, p75 {c['p75']:,})")
        print(f"under {SHORT_ORDER_CHARS:,} chars (likely short/interim order): {100 * s['short_order_share']:.1f}%")
        print(f"mentions decree/dismissed/disposed/judgment: {100 * s['decision_words_share']:.1f}%   (crude proxy)")
        print(f"criminal-procedure terms present: {100 * s['criminal_terms_share']:.1f}%")
    print(f"\nSaved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
