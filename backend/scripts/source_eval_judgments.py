"""Source real Indian court judgments and format them as DigiNyaya eval cases.

Pulls candidate judgments from the Indian Kanoon API via their OFFICIAL
Python client (scripts/_vendor/ikapi.py -- vendored, unmodified), across 7
civil dispute categories plus 2 escalation-condition categories that are
deliberately NOT filtered to civil courts. For each judgment, fetches the
raw text and uses the local LLM to extract:
  - case_description  (from the facts/issues of the dispute)
  - expected_outcome  (from the court's conclusion/holding)
  - cited_precedent   (any authority the judgment itself relies on, if any)

IMPORTANT: Indian Kanoon's API has no "structural analysis" / paragraph
classification endpoint (verified against both their documentation and the
official client source) -- there is no way to request pre-split
Facts/Issues/Arguments/Precedent-Analysis/Conclusion sections from the API
itself. This script does that extraction the same way
scripts/ingest_judgments.py already does for the precedent corpus: fetch raw
judgment text, then one LLM pass per judgment with a JSON-schema prompt.
Every entry is marked verified=false pending human review, same convention.

This script only SOURCES AND FORMATS data -- it never calls the eval harness
or the pipeline. Run scripts/eval_cases.py (or a new consumer of this output)
separately once you're satisfied with the dataset.

SECURITY
  - The API token is read from an environment variable (default
    DIGINYAYA_INDIANKANOON_TOKEN, matching scripts/ingest_judgments.py) or
    backend/.env -- NEVER hardcoded.

COST
  - Indian Kanoon bills per search + per document fetch. Candidates are
    filtered by docsource (civil court signals) at the SEARCH-RESULT stage,
    before spending a fetch call on the full text, and raw docs are cached
    under backend/data_cache/indiankanoon/ (shared with ingest_judgments.py)
    so re-runs never re-pay for the same document.

USAGE (from the backend/ directory)
  # cheap: search only, show candidate counts per category, fetch nothing
  python -m scripts.source_eval_judgments --dry-run

  # full run: search, fetch, extract, redact, write the dataset
  python -m scripts.source_eval_judgments --per-category 6 --escalation-per-condition 3
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
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

from scripts._vendor.ikapi import IKApi  # noqa: E402

from app import llm  # noqa: E402
from app.llm.config import config  # noqa: E402

CACHE_DIR = _BACKEND / "data_cache" / "indiankanoon"  # shared cache with ingest_judgments.py
OUT_PATH = _BACKEND / "data_cache" / "eval_judgments.json"
TOKEN_ENV = "DIGINYAYA_INDIANKANOON_TOKEN"


# --------------------------------------------------------------------------- #
# Category + query configuration
# --------------------------------------------------------------------------- #
# Each civil category is searched with a couple of keyword variants; results
# are then filtered to CIVIL_COURT_SIGNALS via docsource before any full-text
# fetch happens (see _passes_court_filter). No doctypes filter is applied
# except "consumer" for consumer_complaints, which is the one doctype value
# actually documented/proven (scripts/ingest_judgments.py already uses it) --
# guessing at undocumented per-state district-court doctype codes risks
# silent zero-result queries, so district-court scoping is done via the
# docsource text filter instead.
CIVIL_CATEGORIES: dict[str, dict] = {
    "contract_disputes": {
        # At the original small target (6), keyword-only queries surfaced
        # enough on-topic multi-state civil-court hits. At a much larger
        # target (needed to scale the eval set up), the same landmark-
        # judgment-dominance problem already documented for tenancy/
        # employment/property/partnership below hits this category too --
        # confirmed empirically: 6 pagenum steps x 2 queries (~360 raw hits)
        # surfaced only 1 civil-court match without a doctype anchor. Added
        # a 3rd, Delhi-District-Court-scoped query rather than changing the
        # first two (which do still find genuine multi-state civil hits).
        "queries": [
            "breach of contract non-payment recovery suit civil",
            "failure to deliver goods breach of agreement compensation suit",
            "breach of contract suit for damages doctypes:delhidc",
        ],
        "doctypes": None,
    },
    "consumer_complaints": {
        "queries": ["consumer complaint defective goods deficiency of service refund"],
        "doctypes": "consumer",
    },
    # tenancy/employment/property/partnership: plain keyword queries kept
    # surfacing landmark Supreme Court / High Court precedent instead of
    # routine trial-level suits (Indian Kanoon's ranking favours "landmark"
    # judgments for doctrinal-sounding queries) -- confirmed empirically that
    # 0 candidates passed the civil-court filter without a doctype anchor.
    # `doctypes:delhidc` (Delhi District Court -- the one district-court code
    # actually documented/proven, see scripts/ingest_judgments.py's use of
    # `consumer`) fixes this reliably. Trade-off: these 4 categories end up
    # Delhi-only rather than geographically diverse; contract_disputes and
    # small_claims_debt_recovery already got good, on-topic, multi-state
    # results from keywords alone, so they're left as-is.
    "tenancy_disputes": {
        "queries": ["eviction rent arrears tenant landlord"],
        "doctypes": "delhidc",
    },
    "employment_disputes": {
        "queries": ["unpaid wages dues employee termination compensation"],
        "doctypes": "delhidc",
    },
    "property_neighbor_disputes": {
        "queries": ["property boundary encroachment injunction neighbour"],
        "doctypes": "delhidc",
    },
    "small_claims_debt_recovery": {
        "queries": [
            "suit for recovery of money order 37 CPC promissory note",
            "recovery of loan amount friendly loan civil suit",
        ],
        "doctypes": None,
    },
    "partnership_business_disputes": {
        "queries": ["partnership dispute accounts suit"],
        "doctypes": "delhidc",
    },
}

# Escalation categories -- deliberately NOT run through the civil-court filter.
ESCALATION_CATEGORIES: dict[str, dict] = {
    "criminal_matter": {
        "queries": [
            "cheating criminal breach of trust recovery of money criminal case",
            "assault criminal case compensation civil suit",
        ],
        "doctypes": None,
        "condition_type": "criminal_matter_detected",
    },
    "jurisdiction_mismatch": {
        "queries": [
            "child custody guardianship petition family court",
            "writ petition constitutional validity fundamental rights",
        ],
        "doctypes": None,
        "condition_type": "jurisdiction_scope_mismatch",
    },
}

CIVIL_COURT_SIGNALS = (
    "district court", "civil court", "civil judge", "junior civil judge",
    "senior civil judge", "small causes court", "rent control", "labour court",
    "industrial tribunal", "consumer disputes", "consumer forum", "ncdrc", "sdrc",
    "munsif", "additional district", "principal district",
)
EXCLUDE_COURT_SIGNALS = ("supreme court", "high court", "sessions court", "magistrate")


def _passes_court_filter(docsource: str) -> bool:
    src = (docsource or "").lower()
    if any(sig in src for sig in EXCLUDE_COURT_SIGNALS):
        return False
    return any(sig in src for sig in CIVIL_COURT_SIGNALS)


# --------------------------------------------------------------------------- #
# Indian Kanoon API (official client, subclassed only for a transport fix)
# --------------------------------------------------------------------------- #
class _IKApiWithBrowserUA(IKApi):
    """The official client's call_api_direct() sends no User-Agent, which
    Indian Kanoon's Cloudflare front-end has been observed to reject as a bot
    (Error 1010 / browser_signature_banned) -- scripts/ingest_judgments.py
    already discovered and worked around this. Overriding just the one method
    responsible for the HTTP call is the intended extension point (Indian
    Kanoon's own docs say the client is meant to be subclassed), not a
    hand-rolled replacement of the client.
    """

    _BROWSER_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    def call_api_direct(self, url: str) -> str:
        import http.client

        connection = http.client.HTTPSConnection(self.basehost)
        headers = dict(self.headers)
        headers["User-Agent"] = self._BROWSER_UA
        connection.request("POST", url, headers=headers)
        response = connection.getresponse()
        results = response.read()
        if isinstance(results, bytes):
            results = results.decode("utf8")
        return results


def _token(env_name: str) -> str:
    tok = os.getenv(env_name)
    if not tok:
        sys.exit(
            f"ERROR: API token not found. Set ${env_name} in your shell or add it to backend/.env:\n"
            f"  {env_name}=your-token-here"
        )
    return tok


def _make_client(token: str) -> _IKApiWithBrowserUA:
    # IKApi.__init__ expects a full argparse-style namespace; search()/
    # fetch_doc() never touch self.storage, so None is fine there.
    args = argparse.Namespace(
        token=token, maxcites=0, maxcitedby=0, orig=False, maxpages=1,
        pathbysrc=False, numworkers=1, addedtoday=False, fromdate=None,
        todate=None, sortby=None,
    )
    return _IKApiWithBrowserUA(args, storage=None)


def search_candidates(client: _IKApiWithBrowserUA, queries: list[str], doctypes: str | None,
                       *, civil_only: bool, target: int, exclude_tids: set[int] | None = None,
                       max_pagenum_steps: int = 6) -> list[dict]:
    """Search across query variants, dedupe by tid, filter by court signal
    at the search-result stage (before any full-text fetch is spent).

    exclude_tids lets an incremental re-run skip judgments already present in
    an existing dataset -- those don't count toward `target` and are never
    re-fetched/re-extracted, so growing the dataset never re-pays for cases
    already sourced. Paginates deeper (pagenum += maxpages, up to
    max_pagenum_steps rounds) when the first page doesn't surface enough NEW
    candidates, since a bigger target on a re-run will otherwise mostly
    re-discover the same top hits already excluded.
    """
    seen: set[int] = set(exclude_tids or ())
    pool: list[dict] = []
    for q in queries:
        if len(pool) >= target:
            break
        form = q if not doctypes else f"{q} doctypes:{doctypes}"
        pagenum = 0
        for _ in range(max_pagenum_steps):
            if len(pool) >= target:
                break
            try:
                raw = client.search(form, pagenum=pagenum, maxpages=3)
                obj = json.loads(raw)
            except Exception as e:
                print(f"  ! search failed for {q!r} @page{pagenum}: {e}")
                break
            docs = obj.get("docs", [])
            print(f"  >> {q!r} @page{pagenum}: {len(docs)} raw hits")
            if not docs:
                break
            new_this_page = 0
            for d in docs:
                tid = d.get("tid")
                if tid is None or tid in seen:
                    continue
                seen.add(tid)
                docsource = d.get("docsource", "")
                if civil_only and not _passes_court_filter(docsource):
                    continue
                pool.append(d)
                new_this_page += 1
                if len(pool) >= target:
                    break
            time.sleep(0.4)
            pagenum += 3
            if new_this_page == 0 and len(docs) < 3:
                break  # exhausted this query's results
    return pool


def fetch_doc(client: _IKApiWithBrowserUA, tid: int) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{tid}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    raw = client.fetch_doc(tid)
    data = json.loads(raw)
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    time.sleep(0.5)
    return data


# --------------------------------------------------------------------------- #
# Text + extraction + redaction
# --------------------------------------------------------------------------- #
def html_to_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _window(text: str, head: int = 6000, tail: int = 2500) -> str:
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n...\n" + text[-tail:]


_TITLE_PARTIES_RE = re.compile(r"^(.*?)\s+[Vv]s?\.?\s+(.*?)\s+on\s+\d", re.IGNORECASE)


def _party_names_from_title(title: str) -> list[str]:
    """Best-effort extraction of the two party names from an Indian Kanoon
    title, which is conventionally 'X vs Y on <date>'. Used as a fallback
    redaction net (see extract_fields' prompt for the primary mechanism)."""
    m = _TITLE_PARTIES_RE.match(html_to_text(title))
    if not m:
        return []
    names = [m.group(1).strip(), m.group(2).strip()]
    return [n for n in names if n and len(n) > 2]


def _redact_names(text: str, names: list[str]) -> str:
    """Defense-in-depth fallback: strip any of the judgment's own party
    names that survived into the LLM's generated description verbatim.
    The primary redaction mechanism is the extraction prompt itself (see
    extract_fields), which is instructed to never use real private-party
    names in the first place."""
    redacted = text
    for name in names:
        if not name:
            continue
        pattern = re.escape(name)
        redacted = re.sub(pattern, "[REDACTED PARTY NAME]", redacted, flags=re.IGNORECASE)
    return redacted


def extract_fields(body_text: str, category: str) -> dict | None:
    """LLM pass turning raw judgment prose into eval-case fields. Facts+Issues
    -> case_description, Conclusion/holding -> expected_outcome, any authority
    the judgment itself relies on -> cited_precedent. Explicitly instructed
    to never use real private-party names (judges/courts/public officials are
    fine) -- this is the primary redaction mechanism; _redact_names() above
    is a fallback net, not the main defense."""
    if len(body_text) < 200:
        return None
    schema = (
        "{"
        '"case_description": "<120-200 word neutral account of the facts and the legal issue(s) '
        "in dispute, written as a fresh case narrative a claimant might submit -- NOT a summary of "
        'the judgment itself. Refer to the parties generically (e.g. \\"the tenant\\", \\"the landlord\\", '
        '\\"the claimant\\", \\"the respondent\\") -- NEVER use the real names of the private individuals '
        "or private companies involved. You MAY name the court and any judge.>\", "
        '"expected_outcome": "<80-150 word account of what the court actually held/ordered -- the '
        'ground-truth outcome for this case, again using generic party references not real names>", '
        '"cited_precedent": "<any prior case/authority this judgment itself relies on, as citation + '
        'one-line principle, or null if none is cited>"'
        "}"
    )
    prompt = (
        f"This is a real Indian court judgment in the '{category}' dispute category. Extract the fields "
        "below. Return JSON only, matching the schema. Use ONLY facts present in the text; do not invent "
        "amounts, dates, or outcomes not stated in the judgment.\n\n"
        f"SCHEMA: {schema}\n\nJUDGMENT TEXT:\n{_window(body_text)}"
    )
    # Sarvam's models are reasoning models: they write a verbose internal
    # chain-of-thought (in a separate reasoning_content field) before the
    # actual answer, and that reasoning alone can exhaust a small max_tokens
    # budget, leaving content=null (finish_reason="length") -- confirmed
    # empirically: 650 failed every time. 4096 is the ceiling for sarvam-105b
    # on the "starter" subscription tier (a larger request gets rejected with
    # a 400, not silently clamped). This call is left on that full-size
    # reasoning model explicitly, not the default fast-tier model
    # (sarvam-105b-conversations, the replacement for the now-deprecated
    # sarvam-30b) -- the fast tier's max_tokens ceiling is only 2048 on this
    # subscription, well under the headroom this specific extraction task
    # was already measured to need.
    return llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=4096, model=config.sarvam_reasoning_model)


def build_case(
    doc: dict, fields: dict, *, category: str, escalation_expected: bool, condition_type: str | None
) -> dict | None:
    tid = doc.get("tid")
    title = html_to_text(doc.get("title", "")) or f"Indian Kanoon doc {tid}"
    pubdate = str(doc.get("publishdate") or doc.get("docdate") or "")
    year_match = re.search(r"(19|20)\d{2}", pubdate)
    year = int(year_match.group(0)) if year_match else None

    names = _party_names_from_title(doc.get("title", ""))
    description = str(fields.get("case_description", "")).strip()
    outcome = str(fields.get("expected_outcome", "")).strip()
    if not description or not outcome:
        return None
    description = _redact_names(description, names)
    outcome = _redact_names(outcome, names)

    cited = fields.get("cited_precedent")
    cited = str(cited).strip() if cited and str(cited).lower() != "null" else None

    return {
        "case_id": f"IK-EVAL-{tid}",
        "category": category,
        "language": "en",
        "case_description": description,
        "expected_outcome": outcome,
        "cited_precedent": cited,
        "escalation_expected": escalation_expected,
        "condition_type": condition_type,
        "source": {
            "docid": tid,
            "title": title[:160],
            "court": html_to_text(doc.get("docsource", "")),
            "year": year,
            "url": f"https://indiankanoon.org/doc/{tid}/",
        },
        "verified": False,
    }


# --------------------------------------------------------------------------- #
def _run_category(
    client: _IKApiWithBrowserUA, category: str, cfg: dict, *, target: int, civil_only: bool,
    escalation_expected: bool, condition_type: str | None, dry_run: bool,
    exclude_tids: set[int] | None = None,
) -> tuple[list[dict], int]:
    print(f"\n=== {category} === (need {target} new)")
    if target <= 0:
        print("  already have enough for this category, skipping")
        return [], 0
    candidates = search_candidates(
        client, cfg["queries"], cfg.get("doctypes"), civil_only=civil_only, target=target,
        exclude_tids=exclude_tids,
    )
    print(f"  {len(candidates)} candidate(s) passed the court filter" if civil_only else f"  {len(candidates)} candidate(s) found")
    if dry_run:
        for d in candidates:
            print(f"    - tid={d.get('tid')} [{d.get('docsource','')[:40]}] {html_to_text(d.get('title',''))[:80]}")
        return [], len(candidates)

    cases: list[dict] = []
    for d in candidates:
        tid = d.get("tid")
        if tid is None:
            continue
        try:
            doc = fetch_doc(client, tid)
        except Exception as e:
            print(f"  ! fetch {tid} failed: {e}")
            continue
        body = html_to_text(doc.get("doc", ""))
        fields = extract_fields(body, category)
        if not fields:
            print(f"  ! extraction failed for {tid} (skipped)")
            continue
        case = build_case(
            doc, fields, category=category, escalation_expected=escalation_expected,
            condition_type=condition_type,
        )
        if case is None:
            print(f"  ! incomplete extraction for {tid} (skipped)")
            continue
        cases.append(case)
        print(f"  + {case['case_id']}: {case['source']['title'][:70]}")
    return cases, len(candidates)


def main() -> int:
    ap = argparse.ArgumentParser(description="Source real Indian Kanoon judgments into DigiNyaya eval-case format.")
    ap.add_argument("--token-env", default=TOKEN_ENV)
    ap.add_argument("--per-category", type=int, default=6,
                    help="TOTAL candidate judgments wanted per civil category (not just new ones -- "
                         "existing entries for that category count toward this)")
    ap.add_argument("--escalation-per-condition", type=int, default=3,
                    help="TOTAL candidates wanted per escalation condition")
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--dry-run", action="store_true", help="search + filter only; no fetch/extraction (cheap)")
    ap.add_argument("--fresh", action="store_true",
                     help="ignore any existing --out dataset and start over (re-pays for everything)")
    args = ap.parse_args()

    token = _token(args.token_env)
    client = _make_client(token)

    if not args.dry_run and not llm.is_available():
        print("WARNING: local LLM unavailable -- extraction will fail for every candidate. Start Ollama first.")
        return 1

    existing: list[dict] = []
    if not args.fresh and Path(args.out).exists():
        existing = json.loads(Path(args.out).read_text(encoding="utf-8"))
        print(f"Loaded {len(existing)} existing case(s) from {args.out} -- running incrementally "
              "(use --fresh to ignore and start over).")

    existing_by_category: dict[str, list[dict]] = {}
    exclude_tids: set[int] = set()
    for c in existing:
        existing_by_category.setdefault(c["category"], []).append(c)
        docid = c.get("source", {}).get("docid")
        if docid is not None:
            exclude_tids.add(docid)

    all_cases: list[dict] = list(existing)
    summary: dict[str, dict] = {}

    for category, cfg in CIVIL_CATEGORIES.items():
        have = len(existing_by_category.get(category, []))
        need = max(0, args.per_category - have)
        cases, n_candidates = _run_category(
            client, category, cfg, target=need, civil_only=True,
            escalation_expected=False, condition_type=None, dry_run=args.dry_run,
            exclude_tids=exclude_tids,
        )
        all_cases.extend(cases)
        summary[category] = {"had": have, "candidates": n_candidates, "new": len(cases)}

    for condition_key, cfg in ESCALATION_CATEGORIES.items():
        cat_name = f"escalation__{condition_key}"
        have = len(existing_by_category.get(cat_name, []))
        need = max(0, args.escalation_per_condition - have)
        cases, n_candidates = _run_category(
            client, cat_name, cfg, target=need,
            civil_only=False, escalation_expected=True, condition_type=cfg["condition_type"],
            dry_run=args.dry_run, exclude_tids=exclude_tids,
        )
        all_cases.extend(cases)
        summary[cat_name] = {"had": have, "candidates": n_candidates, "new": len(cases)}

    if args.dry_run:
        print("\nDry run complete -- no documents fetched, no extraction, nothing written.")
        return 0

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(all_cases, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"Wrote {len(all_cases)} case(s) total ({len(all_cases) - len(existing)} new) -> {args.out}")
    print("Per-category summary:")
    for category, stats in summary.items():
        total = stats["had"] + stats["new"]
        flag = "  <-- FEWER THAN 4 TOTAL, needs more sourcing" if total < 4 else ""
        print(f"  {category:32s} had={stats['had']:3d}  new={stats['new']:3d}  total={total:3d}{flag}")
    print(
        "\nAll entries marked verified=false -- review case_description/expected_outcome/"
        "cited_precedent against the source URL before treating any of these as ground truth."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
