"""Source MORE real Indian judgments into the eval dataset for $0, from
opennyaiorg/InJudgements_dataset on HuggingFace (Apache 2.0) instead of
Indian Kanoon's paid per-call API (scripts/source_eval_judgments.py).

WHY THIS IS SAFE TO CALL "FREE": the HF dataset is a pre-scraped, publicly
licensed research corpus of the SAME underlying Indian Kanoon judgments
(confirmed: every row's Doc_url is a real indiankanoon.org/doc/<tid>/ link) --
downloading it costs nothing (HuggingFace Hub, a free account + read token),
and it never touches api.indiankanoon.org, so it never bills. Because the
tid is recoverable from Doc_url, new cases use the SAME "IK-EVAL-<tid>" id
scheme as the existing 46 -- this is one unified dataset, not two -- and
dedupes against the existing eval_judgments.json automatically.

WHAT'S FILTERED IN: rows where Court_Type == "District_And_Tribunals" (first-
instance courts -- the same population source_eval_judgments.py targets via
doctypes:delhidc/consumer, not appellate High/Supreme Court judgments) AND
Case_Type in {Civil, Financial, Land&Property, Industrial&Labour} (the 4 of
8 HF case types that can plausibly contain DigiNyaya's 7 registered
categories -- Tax/Constitution/Criminal/Motorvehicles are out of scope).
That population is only ~400 rows -- small, and NOISY (title field parsing
in the source scrape sometimes grabbed a citation string instead of the
actual party names, and many are writ petitions against government bodies,
not private-party civil suits). A single combined LLM pass per candidate
both (a) classifies into DigiNyaya's actual 7-category taxonomy -- the HF
Case_Type labels don't map 1:1 -- and (b) rejects anything that isn't a
genuine two-private-party civil dispute, alongside the same fact/outcome
extraction scripts/source_eval_judgments.py already does. Expect a real
rejection rate; that's the filter working, not a bug.

Writes extracted judgment text into data_cache/indiankanoon/<tid>.json in
the exact format scripts/source_eval_judgments.py's own cache uses, so
scripts/extract_case_signals.py needs ZERO changes to pick up these new
cases on its next incremental run.

Run (from backend/):
  python -m scripts.source_free_judgments --dry-run          # filter only, no LLM calls, no cost
  python -m scripts.source_free_judgments --target 50         # extract up to 50 new cases
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND / ".env")
except Exception:
    pass

import pandas as pd  # noqa: E402
from huggingface_hub import hf_hub_download  # noqa: E402

from app import llm  # noqa: E402
from app.llm.config import config  # noqa: E402
from scripts.source_eval_judgments import (  # noqa: E402
    _party_names_from_title,
    _redact_names,
    _window,
    html_to_text,
)

HF_REPO = "opennyaiorg/InJudgements_dataset"
HF_FILES = [
    "data/train-00000-of-00002-add4caaf8fbc6a8c.parquet",
    "data/train-00001-of-00002-09ac6bd45d6b3658.parquet",
]
LOCAL_DIR = _BACKEND / "data_cache" / "hf_injudgements"
IK_CACHE_DIR = _BACKEND / "data_cache" / "indiankanoon"  # shared with source_eval_judgments.py
OUT_PATH = _BACKEND / "data_cache" / "eval_judgments.json"
# tids the classifier let through once but were manually found to be
# mislabeled (e.g. Section 138 NI Act cheque-bounce prosecutions -- criminal
# cases despite seeking monetary relief, see the is_genuine_civil_dispute
# schema note below). Permanent, separate from `existing` dedup: those tids
# would otherwise re-enter the candidate pool the moment they're removed
# from eval_judgments.json, since normal dedup only excludes tids CURRENTLY
# in the dataset.
EXCLUDED_TIDS_PATH = _BACKEND / "data_cache" / "free_sourcing_excluded_tids.json"

RELEVANT_CASE_TYPES = {"Civil", "Financial", "Land&Property", "Industrial&Labour"}
OUR_CATEGORIES = (
    "contract_disputes", "consumer_complaints", "small_claims_debt_recovery",
    "tenancy_disputes", "employment_disputes", "property_neighbor_disputes",
    "partnership_business_disputes",
)

_DOC_URL_TID_RE = re.compile(r"/doc/(\d+)/?")
# Looser than source_eval_judgments.py's _TITLE_PARTIES_RE (which requires
# capturing clean party-name groups) -- here it's only a cheap pre-filter to
# skip obvious non-judgment rows before spending an LLM call, not the
# primary quality gate (that's the LLM's is_genuine_civil_dispute check).
_TITLE_OK_RE = re.compile(r".{2,}\bvs?\.?\b.{2,}\bon\b\s+\d", re.IGNORECASE)
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _tid_from_url(url: str) -> int | None:
    m = _DOC_URL_TID_RE.search(url or "")
    return int(m.group(1)) if m else None


def load_pool(exclude_tids: set[int]) -> list[dict]:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    frames = []
    for f in HF_FILES:
        path = hf_hub_download(
            repo_id=HF_REPO, repo_type="dataset", filename=f,
            local_dir=str(LOCAL_DIR), token=os.environ.get("HUGGINGFACE_TOKEN"),
        )
        frames.append(pd.read_parquet(path))
    df = pd.concat(frames, ignore_index=True)
    df = df[
        (df["Court_Type"] == "District_And_Tribunals")
        & (df["Case_Type"].isin(RELEVANT_CASE_TYPES))
        & (df["Text"].str.len() >= 200)
        & (df["Titles"].str.match(_TITLE_OK_RE, na=False))
    ]

    rows = []
    for _, r in df.iterrows():
        tid = _tid_from_url(r["Doc_url"])
        if tid is None or tid in exclude_tids:
            continue
        rows.append({
            "tid": tid, "title": r["Titles"], "court": r["Court_Name"],
            "case_type": r["Case_Type"], "text": r["Text"], "url": r["Doc_url"],
        })

    # Interleave by case_type instead of exhausting one type first, so a
    # modest --target still samples across categories rather than e.g. only
    # ever reaching the (largest) Financial bucket.
    by_type: dict[str, list[dict]] = {}
    for row in rows:
        by_type.setdefault(row["case_type"], []).append(row)
    interleaved: list[dict] = []
    while any(by_type.values()):
        for k in list(by_type.keys()):
            if by_type[k]:
                interleaved.append(by_type[k].pop(0))
    return interleaved


def extract_and_classify(body_text: str, case_type_hint: str) -> dict | None:
    if len(body_text) < 200:
        return None
    schema = (
        "{"
        '"is_genuine_civil_dispute": <true ONLY if this is an ordinary CIVIL lawsuit between private '
        "parties (individuals and/or companies) over money, property, contract, tenancy, employment, "
        "or consumer/service issues -- false for writ petitions against the State/government "
        "authorities, criminal matters, tax assessments, motor-accident-claims-tribunal cases, "
        "matrimonial/family/custody matters, or anything that is not a straightforward two-private-"
        "party civil suit. IMPORTANT: cheque-dishonour / Section 138 Negotiable Instruments Act cases "
        "are CRIMINAL PROSECUTIONS under Indian law even though the relief sought is monetary -- if "
        "the judgment uses words like 'accused', 'complainant' (in the criminal sense), 'convicted', "
        "'acquitted', or cites Section 138 NI Act, this is a criminal case: set this to false. Only a "
        "genuine civil money-recovery suit between a 'plaintiff' and 'defendant' (not "
        '"accused"/"convicted"/"acquitted") counts as civil.>, '
        '"is_final_merits_decision": <true ONLY if this judgment actually DECIDES who wins the '
        "underlying dispute -- false for PROCEDURAL/INTERLOCUTORY rulings that leave the merits "
        "undecided, e.g. granting/denying \"leave to defend\" in a summary suit (Order 37 CPC -- this "
        "means the case proceeds to trial, NOT that either side won), remanding the case back to a "
        "lower court for fresh hearing, condoning a delay in filing, or otherwise sending the dispute "
        "back for further proceedings. A real-judgment eval comparing an AI's merits decision against "
        "a court record that never reached the merits is an unfair, meaningless comparison -- if the "
        "court's actual holding is procedural rather than substantive, set this to false regardless "
        'of how confident the case otherwise looks.>, '
        f'"category": "<the SINGLE best-fit label from this exact list: {", ".join(OUR_CATEGORIES)} -- '
        'or null if none fit or is_genuine_civil_dispute is false>, '
        '"case_description": "<120-200 word neutral account of the facts and the legal issue(s) in '
        "dispute, written as a fresh case narrative a claimant might submit -- NOT a summary of the "
        'judgment itself. Refer to parties generically (e.g. "the tenant", "the landlord", "the '
        'claimant", "the respondent") -- NEVER use the real names of the private individuals or '
        "private companies involved. You MAY name the court and any judge. null if "
        'is_genuine_civil_dispute is false.>", '
        '"expected_outcome": "<80-150 word account of what the court actually held/ordered -- the '
        'ground-truth outcome for this case, again using generic party references not real names. '
        'null if is_genuine_civil_dispute is false.>", '
        '"cited_precedent": "<any prior case/authority this judgment itself relies on, as citation + '
        'one-line principle, or null if none is cited>"'
        "}"
    )
    prompt = (
        f"This is a real Indian court judgment, loosely tagged '{case_type_hint}' by its source corpus "
        "-- that tag is coarse and may not be the right fit; decide the actual category yourself from "
        "the schema's list. Extract the fields below. Return JSON only, matching the schema. Use ONLY "
        "facts present in the text; do not invent amounts, dates, or outcomes not stated in the "
        f"judgment.\n\nSCHEMA: {schema}\n\nJUDGMENT TEXT:\n{_window(body_text)}"
    )
    return llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=4096, model=config.sarvam_reasoning_model)


def build_case(row: dict, fields: dict) -> dict | None:
    category = fields.get("category")
    description = str(fields.get("case_description") or "").strip()
    outcome = str(fields.get("expected_outcome") or "").strip()
    if (
        not fields.get("is_genuine_civil_dispute")
        or not fields.get("is_final_merits_decision")
        or category not in OUR_CATEGORIES
        or not description
        or not outcome
    ):
        return None

    names = _party_names_from_title(row["title"])
    description = _redact_names(description, names)
    outcome = _redact_names(outcome, names)

    cited = fields.get("cited_precedent")
    cited = str(cited).strip() if cited and str(cited).lower() != "null" else None

    year_match = _YEAR_RE.search(row["title"])
    return {
        "case_id": f"IK-EVAL-{row['tid']}",
        "category": category,
        "language": "en",
        "case_description": description,
        "expected_outcome": outcome,
        "cited_precedent": cited,
        "escalation_expected": False,
        "condition_type": None,
        "source": {
            "docid": row["tid"],
            "title": html_to_text(row["title"])[:160],
            "court": html_to_text(row["court"]),
            "year": int(year_match.group(0)) if year_match else None,
            "url": row["url"],
        },
        "verified": False,
        "sourced_via": "opennyaiorg/InJudgements_dataset (HuggingFace, free)",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Source more real judgments for free via HuggingFace.")
    ap.add_argument("--target", type=int, default=40, help="how many NEW cases to add")
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--dry-run", action="store_true", help="filter + show pool only, no LLM calls")
    args = ap.parse_args()

    if not args.dry_run and not llm.is_available():
        print("WARNING: LLM unavailable -- extraction will fail for every candidate.")
        return 1

    existing: list[dict] = []
    if Path(args.out).exists():
        existing = json.loads(Path(args.out).read_text(encoding="utf-8"))
    exclude_tids = {c["source"]["docid"] for c in existing if c.get("source", {}).get("docid") is not None}
    if EXCLUDED_TIDS_PATH.exists():
        blocklist = json.loads(EXCLUDED_TIDS_PATH.read_text(encoding="utf-8"))
        exclude_tids |= {int(t) for t in blocklist}
    print(f"Loaded {len(existing)} existing case(s), {len(exclude_tids)} tid(s) excluded from re-sourcing "
          f"(includes any permanent blocklist entries).")

    pool = load_pool(exclude_tids)
    print(f"Candidate pool after filtering: {len(pool)}")
    if args.dry_run:
        from collections import Counter
        print("By case_type:", Counter(r["case_type"] for r in pool))
        for r in pool[:15]:
            print(f"  tid={r['tid']} [{r['case_type']}] {html_to_text(r['title'])[:90]}")
        return 0

    IK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    all_cases = list(existing)
    added = 0
    rejected = 0
    failed = 0

    for i, row in enumerate(pool):
        if added >= args.target:
            break
        print(f"[{i + 1}/{len(pool)}, added={added}/{args.target}] tid={row['tid']} "
              f"({row['case_type']})...", end=" ", flush=True)
        fields = extract_and_classify(row["text"], row["case_type"])
        if not fields:
            print("EXTRACTION FAILED")
            failed += 1
            continue
        case = build_case(row, fields)
        if case is None:
            print(f"rejected (is_genuine={fields.get('is_genuine_civil_dispute')}, "
                  f"is_final={fields.get('is_final_merits_decision')}, "
                  f"category={fields.get('category')})")
            rejected += 1
            continue

        # Cache in the exact shape source_eval_judgments.py's own cache
        # uses, so scripts/extract_case_signals.py works on these new
        # entries with zero code changes on its next incremental run.
        (IK_CACHE_DIR / f"{row['tid']}.json").write_text(
            json.dumps({"doc": row["text"], "title": row["title"]}, ensure_ascii=False),
            encoding="utf-8",
        )

        all_cases.append(case)
        added += 1
        print(f"+ {case['category']}: {case['source']['title'][:70]}")

        # Write incrementally -- a crash or Sarvam outage mid-run shouldn't
        # lose progress already paid for in LLM call time.
        Path(args.out).write_text(json.dumps(all_cases, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"Done. added={added} rejected={rejected} extraction_failed={failed} "
          f"pool_exhausted={added < args.target}")
    print(f"Total dataset size now: {len(all_cases)} -> {args.out}")
    print("All new entries marked verified=false -- same convention as source_eval_judgments.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
