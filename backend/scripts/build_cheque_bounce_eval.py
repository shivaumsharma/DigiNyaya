"""Build a small, DEDICATED real-judgment eval for the cheque_bounce
(Section 138 Negotiable Instruments Act) dispute type -- deliberately kept
separate from the main civil eval (scripts/run_real_judgment_eval.py /
judge_real_outcomes.py), which correctly excludes these cases since mixing
a criminal-prosecution outcome (convicted/acquitted) into a civil win/lose
comparison would be an unfair, meaningless measurement for both.

WHY THIS EXISTS: cheque_bounce is already a registered DigiNyaya dispute
type (app/data/loader.py, 33 precedents) that app/agents/ingestion.py
already excludes from Tier-1 autonomy pending "precedent coverage and eval
results" -- but no eval has ever actually been run against it. This uses
the 9 real Section 138 judgments already found and cached this session
(data_cache/free_sourcing_excluded_tids.json for the tid list,
data_cache/indiankanoon/<tid>.json for the already-fetched text) to build
that missing eval, reusing the same extraction machinery
scripts/source_eval_judgments.py already has (facts -> case_description,
holding -> expected_outcome, never leaking the verdict into the input).

Run (from backend/): python -m scripts.build_cheque_bounce_eval
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from app import llm  # noqa: E402
from scripts.source_eval_judgments import (  # noqa: E402
    _party_names_from_title,
    _redact_names,
    extract_fields,
    html_to_text,
)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
IK_CACHE_DIR = _DATA_DIR / "indiankanoon"
BLOCKLIST_PATH = _DATA_DIR / "free_sourcing_excluded_tids.json"
OUT_PATH = _DATA_DIR / "eval_judgments_cheque_bounce.json"
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def main() -> int:
    blocklist = json.loads(BLOCKLIST_PATH.read_text(encoding="utf-8"))
    tids = [int(t) for t, reason in blocklist.items() if "cheque" in reason.lower()]
    print(f"Found {len(tids)} cheque_bounce tid(s) in the blocklist: {tids}")

    if not llm.is_available():
        print("ERROR: LLM unavailable -- extraction needs a real LLM call.")
        return 1

    cases: list[dict] = []
    for tid in tids:
        cache_file = IK_CACHE_DIR / f"{tid}.json"
        if not cache_file.exists():
            print(f"  ! {tid}: no cached text, skipping")
            continue
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        body = html_to_text(cached.get("doc", ""))
        title = cached.get("title", "")
        print(f"[{tid}] extracting...", end=" ", flush=True)
        fields = extract_fields(body, "cheque_bounce (Section 138 Negotiable Instruments Act)")
        if not fields:
            print("EXTRACTION FAILED")
            continue

        names = _party_names_from_title(title)
        description = _redact_names(str(fields.get("case_description") or "").strip(), names)
        outcome = _redact_names(str(fields.get("expected_outcome") or "").strip(), names)
        if not description or not outcome:
            print("incomplete extraction, skipped")
            continue

        year_match = _YEAR_RE.search(title)
        cases.append({
            "case_id": f"CB-EVAL-{tid}",
            "category": "cheque_bounce",
            "language": "en",
            "case_description": description,
            "expected_outcome": outcome,
            "cited_precedent": (
                str(fields.get("cited_precedent")).strip()
                if fields.get("cited_precedent") and str(fields.get("cited_precedent")).lower() != "null"
                else None
            ),
            "escalation_expected": False,
            "condition_type": None,
            "source": {
                "docid": tid,
                "title": html_to_text(title)[:160],
                "court": "Metropolitan Magistrate / NI Act Court",
                "year": int(year_match.group(0)) if year_match else None,
                "url": f"https://indiankanoon.org/doc/{tid}/",
            },
            "verified": False,
        })
        print(f"ok ({len(description.split())}w description)")

    OUT_PATH.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(cases)} case(s) -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
