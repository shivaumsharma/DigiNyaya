"""Source MORE real Indian judgments into the eval dataset for $0, from
India's own eCourts district-court records via a third-party AWS Open Data
mirror (github.com/vanga/indian-district-court-judgments, MIT-licensed
scraper, bucket s3://indian-district-court-judgments-test/) -- civil-suit-
coded case records (CS, CS (COMM), Civ Suit, RC ARC, etc.), not Indian
Kanoon's paid per-call API (scripts/source_eval_judgments.py) and not the
much smaller, already largely-exhausted HuggingFace mirror
(scripts/source_free_judgments.py -- 4 of 177 candidates accepted on its
last run, none in the categories that actually needed help).

WHY THIS IS SAFE TO CALL "FREE": the bucket is served via AWS Open Data
(no requester-pays) and readable anonymously (boto3 UNSIGNED signature,
same as `aws s3 ... --no-sign-request`) -- confirmed empirically by directly
listing and downloading real objects from it, not just trusting the repo's
README. It never touches api.indiankanoon.org or any billed service. The
underlying content is the same class of public district-court order that
Indian Kanoon itself republishes (and that this repo's OWN paid script
already treats as fair game).

HONESTY NOTE ON DATA MATURITY: the source repo self-describes as "WIP" and
the bucket name carries a "-test" suffix -- unlike its sibling High Court/
Supreme Court AWS Open Data listings (registered, stable, CC-BY-4.0),
this one is not yet a mature, officially-registered dataset. Coverage is
real but uneven: metadata parquet exists for most (state, year) combos, but
the raw PDF tar for a given (state, district, complex, year) is sometimes
missing even when the metadata says has_pdf=True -- confirmed empirically
(Delhi/2024 has full metadata coverage, zero raw PDF tars synced, as of this
script's writing). This script treats a missing tar as "skip this complex
and try the next one", never as an error worth stopping the run over.

WHAT'S FILTERED IN: case_type codes that denote an ordinary civil suit (see
CIVIL_CASE_TYPE_CODES below) -- the same population source_eval_judgments.py
already targets via doctypes:delhidc/consumer -- AND has_pdf AND
final_orders > 0 (an actual decided case with a retrievable order, not just
a filing or an interim order). Like the other two sourcing scripts, this is
only a coarse pre-filter: the real is_genuine_civil_dispute /
is_final_merits_decision / category work is still done by the same combined
LLM pass scripts/source_free_judgments.py already built
(extract_and_classify, imported and reused here verbatim, not
reimplemented) -- expect a real rejection rate; that's the filter working.

NO PARTY-NAME REDACTION FALLBACK: unlike the other two sourcing scripts,
this source's metadata carries no party-name field to build a title-based
redaction safety net from (contrast source_eval_judgments.py's
_redact_names). Redaction here relies solely on the LLM extraction prompt's
own instruction to never use real private-party names -- one fewer layer of
defense-in-depth than the other two sources. Review verified=false entries
from this source at least as carefully as the others before trusting them.

COST: $0 -- AWS Open Data egress is free, and the only spend is Sarvam LLM
calls (prepaid, same as the other sourcing scripts). The real cost is
bandwidth/disk: each targeted court complex's PDF archive is downloaded in
FULL (tens to ~200MB observed) to extract the handful of cases needed from
it, then cached locally so a re-run never re-downloads the same complex.
Complexes are processed in descending order of how many candidate cases
they contribute, so each download is spent where it pays off most.

Run (from backend/):
  python -m scripts.source_district_court_judgments --dry-run
      # metadata only -- no PDF download, no LLM calls, shows candidate counts
  python -m scripts.source_district_court_judgments --target 60
      # extract up to 60 new cases, default: all states, year 2024
  python -m scripts.source_district_court_judgments --states 29,3,26 --years 2024,2023 --target 100
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tarfile
from collections import Counter
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

import boto3  # noqa: E402
import pandas as pd  # noqa: E402
from botocore import UNSIGNED  # noqa: E402
from botocore.config import Config  # noqa: E402

from app import llm  # noqa: E402
from app.documents.extraction import extract_document  # noqa: E402
from scripts.source_free_judgments import OUR_CATEGORIES, extract_and_classify  # noqa: E402

BUCKET = "indian-district-court-judgments-test"
OUT_PATH = _BACKEND / "data_cache" / "eval_judgments.json"
TAR_CACHE_DIR = _BACKEND / "data_cache" / "district_court_tars"
IK_CACHE_DIR = _BACKEND / "data_cache" / "indiankanoon"  # shared cache, see extract_case_signals.py
SOURCED_VIA_TAG = "ecourts_district_court (AWS Open Data, free, github.com/vanga/indian-district-court-judgments)"

# Persistent across runs: complexes whose data.tar 404'd (or otherwise
# errored) last time. The ranked candidate list is sorted purely by
# candidate COUNT and is otherwise deterministic run to run, so without this
# cache every repeat run re-spends its --max-complexes budget re-attempting
# the same large-but-tarless complexes at the top of the ranking before ever
# reaching a working one -- wasteful when this script is meant to be re-run
# many times in a row to grow the corpus toward a large target.
BAD_COMPLEX_CACHE_PATH = _BACKEND / "data_cache" / "district_court_bad_complexes.json"


def _load_bad_complexes() -> set:
    if BAD_COMPLEX_CACHE_PATH.exists():
        return set(json.loads(BAD_COMPLEX_CACHE_PATH.read_text(encoding="utf-8")))
    return set()


def _save_bad_complexes(bad: set) -> None:
    BAD_COMPLEX_CACHE_PATH.write_text(
        json.dumps(sorted(bad), indent=2), encoding="utf-8"
    )

# Case-type codes (as they appear in the eCourts metadata, normalized
# upper/stripped for matching) that denote an ordinary CIVIL SUIT -- the
# same population source_eval_judgments.py targets via doctypes:delhidc.
# Deliberately conservative: ambiguous codes seen in a real Delhi/2024
# sample ("TC", "Ct Cases", "SC", "MC", "LCA" -- unclear/inconsistent
# meaning across states) are left OUT rather than guessed at. "CC NI ACT"
# (cheque-dishonour, criminal despite monetary relief), "MACT" (motor
# accident tribunal), "EX"/execution (enforcing an already-decided case, not
# a merits decision), and anything Cr*/Bail/Criminal are excluded on
# purpose -- same reasoning extract_and_classify's own schema already uses
# to reject Section 138 NI Act cases.
CIVIL_CASE_TYPE_CODES = {
    "CS", "CS SCJ", "CS DJ", "CS (COMM)", "CS (COMM.)", "CS DJ ADJ",
    "CIV SUIT", "RC ARC",
}

# eCourts case-type strings are NOT nationally standardized -- confirmed
# empirically by sampling real metadata from multiple states: Telangana/
# Andhra Pradesh use "OS" (Original Suit) for what Delhi/Haryana/Punjab call
# "CS", and never use the Delhi-style codes at all. Per-state additions to
# the base CIVIL_CASE_TYPE_CODES set above, keyed by state_code. Deliberately
# does NOT add "SC" anywhere despite it appearing as a plausible-looking
# civil code in some states' top case-type counts -- in Telangana/AP it
# means Small Cause (civil), but in Delhi's own metadata "SC" is a top-20
# code too and there is no way to confirm from the abbreviation alone that
# it doesn't mean Sessions Case (criminal) there. Same reasoning
# extract_and_classify's own schema already applies to Section 138 NI Act
# cases: when an abbreviation is genuinely ambiguous across states, leave it
# out rather than risk feeding criminal matters into a civil eval set -- the
# LLM classify step is a second gate, not an excuse to skip this one.
STATE_CIVIL_CASE_TYPE_CODES: dict[str, set[str]] = {
    "29": {"OS"},  # Telangana
    "2": {"OS"},   # Andhra Pradesh
    "8": {"TITLE SUIT"},  # Bihar
}


def _civil_codes_for_state(state_code: str) -> set[str]:
    return CIVIL_CASE_TYPE_CODES | STATE_CIVIL_CASE_TYPE_CODES.get(state_code, set())


# All state/UT codes this repo's courts.csv uses (from its README).
ALL_STATE_CODES = [str(c) for c in (
    list(range(1, 31)) + [33, 34, 35, 36, 37, 38]
) if c not in (31, 32)]


def get_s3_client():
    return boto3.client("s3", config=Config(signature_version=UNSIGNED))


def load_metadata(s3, state_code: str, year: str) -> pd.DataFrame | None:
    key = f"metadata/parquet/year={year}/state={state_code}/cases.parquet"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))
    except s3.exceptions.NoSuchKey:
        return None  # expected and common -- most states don't have every year synced
    except Exception as e:
        # Anything OTHER than "this key doesn't exist" (network error,
        # throttling, a malformed/corrupt parquet file) was previously
        # swallowed identically to the expected case above, with zero
        # visibility -- a real, sustained problem partway through a
        # multi-state scan would look indistinguishable from "these states
        # just don't have data yet." Matches download_complex_tar()'s own
        # convention of surfacing the exception type rather than the full
        # traceback (this runs once per state per year, not per case, so
        # the extra line isn't noisy).
        print(f"  ! metadata load failed for state={state_code} year={year}: {type(e).__name__}: {e}")
        return None


def download_complex_tar(
    s3, year: str, state: str, district: str, complex_code: str, bad_complexes: set | None = None,
) -> Path | None:
    TAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = TAR_CACHE_DIR / f"{year}_{state}_{district}_{complex_code}.tar"
    if cache_path.exists():
        return cache_path
    key = f"data/tar/year={year}/state={state}/district={district}/complex={complex_code}/data.tar"
    try:
        s3.download_file(BUCKET, key, str(cache_path))
        return cache_path
    except Exception as e:
        print(f"    ! tar unavailable for district={district} complex={complex_code}: {type(e).__name__}")
        if cache_path.exists():
            cache_path.unlink()  # don't leave a partial download behind
        if bad_complexes is not None:
            bad_complexes.add(f"{year}_{state}_{district}_{complex_code}")
        return None


def _part_number(member: tarfile.TarInfo) -> int:
    # Filenames observed as "orders_<filing_year>_<case_no>_<part>.pdf" --
    # sort by the trailing PART NUMBER, not the filename string. A plain
    # string sort would put "..._10.pdf" before "..._2.pdf" for any case
    # with 10+ order parts, concatenating them out of chronological order.
    match = re.search(r"_(\d+)\.\w+$", member.name)
    return int(match.group(1)) if match else 0


def extract_case_text(tar_path: Path, case_no: str) -> str | None:
    """Find every archive member for this case's case_no (there can be more
    than one order/part per case), OCR/extract each via the same
    app.documents.extraction pipeline live document uploads use, and
    concatenate in part-number order. Returns None if no member matched or
    nothing extractable came out of the ones that did.

    Matches case_no as an EXACT underscore/dot-delimited token, not a raw
    substring -- a substring check risks silently matching a DIFFERENT
    case whose (also numeric, similarly long) case_no happens to contain
    this one, pulling in wrong or mixed text."""
    with tarfile.open(tar_path) as tar:
        members = [m for m in tar.getmembers() if case_no in re.split(r"[_.]", m.name)]
        if not members:
            return None
        members.sort(key=_part_number)
        texts = []
        for m in members:
            f = tar.extractfile(m)
            if f is None:
                continue
            raw = f.read()
            result = extract_document(raw, "application/pdf")
            if result.error or not result.cleaned_text:
                continue
            texts.append(result.cleaned_text)
    return "\n\n".join(texts) if texts else None


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

    cited = fields.get("cited_precedent")
    cited = str(cited).strip() if cited and str(cited).lower() != "null" else None

    reg_year = row.get("reg_year")
    return {
        "case_id": f"DC-EVAL-{row['cino']}",
        "category": category,
        "language": "en",
        "case_description": description,
        "expected_outcome": outcome,
        "cited_precedent": cited,
        "escalation_expected": False,
        "condition_type": None,
        "source": {
            "docid": row["cino"],
            "title": f"{row.get('case_type', '')} {row.get('case_no', '')}".strip(),
            "court": f"{row.get('complex_name', '')}, {row.get('district_name', '')}, {row.get('state_name', '')}",
            "year": int(reg_year) if reg_year is not None else None,
            "url": None,
        },
        "verified": False,
        "sourced_via": SOURCED_VIA_TAG,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Source more real judgments for free from eCourts district-court data.")
    ap.add_argument("--states", default="all", help="comma-separated state codes, or 'all' (default)")
    ap.add_argument("--years", default="2024", help="comma-separated years (default: 2024)")
    ap.add_argument("--target", type=int, default=40, help="how many NEW cases to add")
    ap.add_argument("--max-complexes", type=int, default=30,
                     help="cap on distinct court complexes to download tars for, to bound bandwidth/disk (default 30)")
    ap.add_argument("--max-per-complex", type=int, default=25,
                     help="cap on ACCEPTED cases pulled from any single court complex, so a large --target "
                          "doesn't end up drawing the whole batch from one district court's docket (default 25)")
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--dry-run", action="store_true", help="metadata only -- no PDF download, no LLM calls")
    args = ap.parse_args()

    if not args.dry_run and not llm.is_available():
        print("WARNING: LLM unavailable -- extraction will fail for every candidate.")
        return 1

    existing: list[dict] = []
    if Path(args.out).exists():
        existing = json.loads(Path(args.out).read_text(encoding="utf-8"))
    exclude_docids = {
        str(c.get("source", {}).get("docid"))
        for c in existing
        if c.get("source", {}).get("docid") is not None
    }
    print(f"Loaded {len(existing)} existing case(s), {len(exclude_docids)} docid(s) excluded from re-sourcing.")

    s3 = get_s3_client()
    states = ALL_STATE_CODES if args.states == "all" else [s.strip() for s in args.states.split(",")]
    years = [y.strip() for y in args.years.split(",")]

    all_candidates: list[dict] = []
    for year in years:
        for state in states:
            meta = load_metadata(s3, state, year)
            if meta is None:
                continue
            ct = meta["case_type"].fillna("").astype(str).str.strip().str.upper()
            civil = meta[ct.isin(_civil_codes_for_state(state)) & meta["has_pdf"] & (meta["final_orders"] > 0)]
            civil = civil[~civil["cino"].astype(str).isin(exclude_docids)]
            if len(civil) == 0:
                continue
            print(f"  year={year} state={state} ({civil.iloc[0]['state_name']}): {len(civil)} candidate(s)")
            for _, r in civil.iterrows():
                d = r.to_dict()
                d["year"] = year
                all_candidates.append(d)

    print(f"\nTotal candidates across all state/year combos: {len(all_candidates)}")
    if args.dry_run:
        by_state = Counter(c["state_name"] for c in all_candidates)
        by_type = Counter(c["case_type"] for c in all_candidates)
        print("By state:", dict(by_state.most_common(15)))
        print("By case_type:", dict(by_type.most_common(10)))
        print("\nDry run complete -- no PDFs downloaded, no LLM calls, nothing written.")
        return 0

    groups: dict[tuple, list[dict]] = {}
    for c in all_candidates:
        key = (c["year"], c["state_code"], c["district_code"], c["complex_code"])
        groups.setdefault(key, []).append(c)
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    bad_complexes = _load_bad_complexes()
    skipped_known_bad = [
        kv for kv in ranked if f"{kv[0][0]}_{kv[0][1]}_{kv[0][2]}_{kv[0][3]}" in bad_complexes
    ]
    ranked = [
        kv for kv in ranked if f"{kv[0][0]}_{kv[0][1]}_{kv[0][2]}_{kv[0][3]}" not in bad_complexes
    ]
    print(f"Grouped into {len(ranked) + len(skipped_known_bad)} court complex(es), ranked by candidate count "
          f"({len(skipped_known_bad)} skipped -- known tar-unavailable from a prior run).\n")

    all_cases = list(existing)
    added = rejected = extract_failed = classify_failed = tar_missing = 0
    complexes_tried = 0

    for (year, state, district, complex_code), cases in ranked:
        if added >= args.target or complexes_tried >= args.max_complexes:
            break
        complexes_tried += 1
        print(f"[complex {complexes_tried}/{min(len(ranked), args.max_complexes)}] "
              f"year={year} state={state} district={district} complex={complex_code} "
              f"({len(cases)} candidate case(s))")
        tar_path = download_complex_tar(s3, year, state, district, complex_code, bad_complexes)
        if tar_path is None:
            tar_missing += len(cases)
            _save_bad_complexes(bad_complexes)
            continue

        added_this_complex = 0
        for row in cases:
            if added >= args.target or added_this_complex >= args.max_per_complex:
                break
            case_no = str(row["case_no"])
            body_text = extract_case_text(tar_path, case_no)
            if not body_text or len(body_text) < 200:
                extract_failed += 1
                print(f"    - {case_no}: no extractable text (skipped)")
                continue
            fields = extract_and_classify(body_text, row["case_type"])
            if not fields:
                classify_failed += 1
                print(f"    - {case_no}: LLM extraction failed")
                continue
            case = build_case(row, fields)
            if case is None:
                rejected += 1
                print(f"    - {case_no}: rejected (is_genuine={fields.get('is_genuine_civil_dispute')}, "
                      f"is_final={fields.get('is_final_merits_decision')}, category={fields.get('category')})")
                continue

            # Cache the extracted text in the exact shape
            # source_eval_judgments.py's/source_free_judgments.py's own
            # cache uses (data_cache/indiankanoon/<docid>.json, {"doc":
            # ..., "title": ...}) so extract_case_signals.py picks these
            # cases up with zero code changes on its next incremental run --
            # same convention those two scripts document for exactly this
            # reason. Missing this the first time silently sent all 220
            # cases from this source through extract_case_signals.py's
            # placeholder-default fallback (verified via a real run before
            # this fix landed) -- not a hypothetical risk.
            IK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (IK_CACHE_DIR / f"{row['cino']}.json").write_text(
                json.dumps({"doc": body_text, "title": case["source"]["title"]}, ensure_ascii=False),
                encoding="utf-8",
            )

            all_cases.append(case)
            added += 1
            added_this_complex += 1
            print(f"    + [{added}/{args.target}] {case['category']}: {case['source']['title']}")
            Path(args.out).write_text(json.dumps(all_cases, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"Done. added={added} rejected={rejected} extraction_failed={extract_failed} "
          f"classify_failed={classify_failed} tar_missing_for={tar_missing} cases "
          f"complexes_tried={complexes_tried}")
    print(f"Total dataset size now: {len(all_cases)} -> {args.out}")
    print("All new entries marked verified=false -- same convention as the other sourcing scripts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
