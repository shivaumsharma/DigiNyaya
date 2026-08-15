"""Ingest REAL Indian consumer-court judgments into the precedent corpus.

Pulls judgments from the Indian Kanoon API, caches the raw documents locally
(so you never pay twice for the same doc), then uses the local LLM to extract
the structured fields the agents need (outcome, relief ratio, compliance window,
principle, tags) — turning unstructured judgment prose into the schema in
`app/data/precedents.json`.

SECURITY
  - The API token is read from an environment variable (default
    DIGINYAYA_INDIANKANOON_TOKEN) or from backend/.env — NEVER hardcoded.
  - Raw judgments are cached under backend/data_cache/ (git-ignored).

COST
  - The Indian Kanoon API is billed per search + per document fetch. This script
    caches aggressively and supports --dry-run (search only) and a small default
    --limit so you can validate cheaply before scaling up.

USAGE (run from the backend/ directory)
  # cheap: just search, list what would be fetched, fetch nothing
  python -m scripts.ingest_judgments --dry-run --category contract_breach --all-topics

  # pull + extract 25 consumer judgments and merge into the corpus (default category)
  python -m scripts.ingest_judgments --limit 25 --all-topics

  # grow an under-covered dispute type -- doctypes defaults to unfiltered
  # (all courts) for anything other than consumer_dispute, since these come
  # from ordinary civil/criminal courts, not the consumer forum
  python -m scripts.ingest_judgments --category contract_breach --all-topics --limit 30
  python -m scripts.ingest_judgments --category money_recovery --all-topics --limit 30
  python -m scripts.ingest_judgments --category cheque_bounce --all-topics --limit 30

  # rebuild the corpus from scratch (drops synthetic entries)
  python -m scripts.ingest_judgments --limit 50 --replace
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

# Windows consoles default to cp1252 and crash on non-Latin-1 glyphs; force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

# Load backend/.env if present (so the token can live there, not in the shell).
try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND / ".env")
except Exception:
    pass

from app import llm  # noqa: E402
from app.agents import nlp  # noqa: E402
from app.data.loader import load_precedents, validate_precedent  # noqa: E402

API_BASE = "https://api.indiankanoon.org"
CACHE_DIR = _BACKEND / "data_cache" / "indiankanoon"
OUT_PATH = _BACKEND / "app" / "data" / "precedents.json"

# Controlled tag vocabulary = the signals the agents/retrieval understand.
TAG_VOCAB = sorted(nlp.SIGNAL_LEXICON.keys())
ALLOWED_OUTCOMES = ["full_refund", "partial_refund", "replacement", "compensation", "dismissed"]

DEFAULT_QUERY = "consumer protection deficiency of service refund defective goods"

# Broad coverage across the major dispute categories, one topic-query set per
# DigiNyaya dispute type. Used with --all-topics so a single ingestion builds
# a diverse corpus (better decisions) instead of 20 near-duplicate hits from
# one query. Deduped by document id.
#
# Originally consumer_dispute-only (this corpus started at 120 consumer
# precedents vs. 2-3 each for the other three types -- see the real-judgment
# eval, where that imbalance directly explained why contract_breach cases
# retrieved an identical, capped 2 precedents every time). Generalized so the
# same tool can grow money_recovery/contract_breach/cheque_bounce coverage.
TOPIC_QUERIES_BY_CATEGORY: dict[str, list[str]] = {
    "consumer_dispute": [
        "deficiency of service refund consumer",
        "defective goods replacement warranty consumer",
        "e-commerce online purchase non-delivery refund consumer",
        "insurance claim repudiation deficiency consumer",
        "builder possession delay flat refund consumer",
        "medical negligence compensation consumer",
        "bank unauthorised transaction deficiency consumer",
        "airline flight cancellation refund consumer",
        "telecom mobile broadband billing deficiency consumer",
        "electricity water utility deficiency of service consumer",
        "automobile vehicle manufacturing defect consumer",
        "unfair trade practice misleading advertisement consumer",
        "education coaching fee refund deficiency consumer",
        "tour travel package deficiency refund consumer",
        "real estate RERA possession compensation consumer",
    ],
    "cheque_bounce": [
        "cheque dishonour section 138 negotiable instruments act",
        "cheque bounce complaint conviction acquittal",
        "cheque dishonour compounding settlement",
        "post dated cheque security advance payment dishonour",
        "cheque dishonour legally enforceable debt presumption section 139",
        "cheque bounce insufficient funds complaint",
        "section 138 negotiable instruments act appeal sentence",
    ],
    "money_recovery": [
        "money recovery suit summary judgment order 37 CPC",
        "loan recovery friendly loan promissory note",
        "recovery of money suit decree interest",
        "unpaid dues recovery civil suit",
        "recovery suit written agreement default",
        "recovery of loan guarantor liability suit",
        "recovery suit dishonoured cheque debt",
    ],
    "contract_breach": [
        "breach of contract suit specific performance damages",
        "breach of agreement compensation section 74 contract act",
        "earnest money forfeiture breach of contract",
        "non-performance of contract damages suit",
        "cancellation of agreement breach compensation",
        "breach of contract liquidated damages penalty clause",
        "specific performance agreement to sell breach",
    ],
}


# --------------------------------------------------------------------------- #
# Indian Kanoon API
# --------------------------------------------------------------------------- #
def _token(env_name: str) -> str:
    tok = os.getenv(env_name)
    if not tok:
        sys.exit(
            f"ERROR: API token not found. Set ${env_name} in your shell or add it to backend/.env:\n"
            f"  {env_name}=your-token-here"
        )
    return tok


# Status codes the API returns under transient throttling/load — retried with backoff.
_RETRYABLE = {401, 403, 429, 500, 502, 503, 504}
_MAX_RETRIES = 4


def _post(path: str, token: str) -> dict:
    req = urllib.request.Request(
        API_BASE + path,
        data=b"",
        method="POST",
        headers={
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            # Indian Kanoon sits behind Cloudflare, which blocks the default
            # Python-urllib agent (Error 1010 / browser_signature_banned).
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        },
    )
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in _RETRYABLE or attempt == _MAX_RETRIES - 1:
                raise
        except urllib.error.URLError as e:
            last_err = e
            if attempt == _MAX_RETRIES - 1:
                raise
        backoff = 2 ** attempt  # 1s, 2s, 4s, 8s
        print(f"  … transient API error ({last_err}); retrying in {backoff}s")
        time.sleep(backoff)
    raise last_err  # pragma: no cover


def search(token: str, query: str, doctypes: str | None, pages: int) -> list[dict]:
    docs: list[dict] = []
    form = query if not doctypes else f"{query} doctypes:{doctypes}"
    for page in range(pages):
        path = f"/search/?formInput={urllib.parse.quote(form)}&pagenum={page}"
        try:
            data = _post(path, token)
        except urllib.error.HTTPError as e:
            print(f"  ! search page {page} failed: {e.code} {e.reason}")
            break
        batch = data.get("docs", [])
        if not batch:
            break
        docs.extend(batch)
        print(f"  · page {page}: {len(batch)} results (total {len(docs)})")
        time.sleep(0.4)
    return docs


_CASE_TITLE_RE = re.compile(r"\bvs\.?\b|\bversus\b", re.IGNORECASE)


def _looks_like_case_judgment(title: str) -> bool:
    """Indian Kanoon's search also indexes bare Acts/statutes and law-review
    articles alongside actual case judgments -- a search for e.g. "breach of
    contract" can surface "The Maharashtra Universities Act, 1994" right next
    to a real case. Those have no holding to extract an outcome from, so the
    LLM extraction step silently defaults them to "dismissed" -- which is
    actively harmful here, not just noise: it would quietly bias the
    corpus's under-covered dispute types toward dismissal, the opposite of
    what growing the corpus is meant to fix. Every real judgment title on
    Indian Kanoon follows "<party> vs <party> on <date>"; bare Acts and
    articles don't -- cheap, reliable filter, applied before any paid fetch.
    """
    return bool(_CASE_TITLE_RE.search(title or ""))


def collect_candidates(
    token: str, queries: list[str], doctypes: str | None, pages: int, target: int
) -> list[dict]:
    """Search multiple topic queries, deduping by document id, until we reach
    `target` unique candidates (or exhaust the queries)."""
    seen: set = set()
    pool: list[dict] = []
    skipped_non_judgments = 0
    for q in queries:
        if len(pool) >= target:
            break
        print(f"  >> topic: {q}")
        for d in search(token, q, doctypes, pages):
            tid = d.get("tid")
            if tid is None or tid in seen:
                continue
            seen.add(tid)
            if not _looks_like_case_judgment(d.get("title", "")):
                skipped_non_judgments += 1
                continue
            pool.append(d)
            if len(pool) >= target:
                break
    if skipped_non_judgments:
        print(f"  (skipped {skipped_non_judgments} non-judgment result(s): bare Acts/articles, not case titles)")
    return pool


def fetch_doc(token: str, tid: int) -> dict:
    """Fetch a judgment, caching the raw response so we never re-pay."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{tid}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    data = _post(f"/doc/{tid}/", token)
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    time.sleep(0.5)
    return data


# --------------------------------------------------------------------------- #
# Text + extraction
# --------------------------------------------------------------------------- #
def html_to_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _window(text: str, head: int = 6000, tail: int = 2500) -> str:
    """Judgments are long; the operative order is usually near the end."""
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n...\n" + text[-tail:]


def extract_fields(doc: dict) -> dict | None:
    """Use the local LLM to turn judgment prose into structured precedent fields."""
    body = html_to_text(doc.get("doc", ""))
    if len(body) < 200:
        return None
    schema = (
        "{"
        '"summary": "<=40 word neutral summary of the dispute and holding", '
        '"principle": "<=35 word legal principle established", '
        f'"outcome": "one of {ALLOWED_OUTCOMES}", '
        '"outcome_detail": "<=25 word description of the relief granted", '
        '"relief_amount_ratio": <number 0..1 = awarded / claimed; 1.0 full relief, 0.0 dismissed>, '
        '"compliance_days": <int days to comply, 30 if unspecified>, '
        f'"tags": ["<=5 tags from this list only: {TAG_VOCAB}>"]'
        "}"
    )
    prompt = (
        "Extract structured fields from this Indian court judgment. Return JSON only, "
        "matching the schema. Use ONLY facts present in the text; if a number is absent use the "
        "stated default. Do not invent amounts.\n\n"
        f"SCHEMA: {schema}\n\nJUDGMENT TEXT:\n{_window(body)}"
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=800)
    if not data:
        return None
    return data


_DEFAULT_COURT_BY_CATEGORY = {
    "consumer_dispute": "Consumer Disputes Redressal Commission",
    "cheque_bounce": "Court (Section 138 Negotiable Instruments Act)",
    "money_recovery": "Civil Court",
    "contract_breach": "Civil Court",
}
_DEFAULT_TAG_BY_CATEGORY = {
    "consumer_dispute": "service_deficiency",
    "cheque_bounce": "cheque_dishonour",
    "money_recovery": "unpaid_dues",
    "contract_breach": "breach_of_agreement",
}


def build_precedent(doc: dict, fields: dict, category: str) -> dict:
    tid = doc.get("tid")
    title = html_to_text(doc.get("title", "")) or f"Indian Kanoon doc {tid}"
    pubdate = str(doc.get("publishdate") or doc.get("docdate") or "")
    year_match = re.search(r"(19|20)\d{2}", pubdate)
    year = int(year_match.group(0)) if year_match else 0

    ratio = fields.get("relief_amount_ratio", 0.0)
    try:
        ratio = max(0.0, min(float(ratio), 1.0))
    except (TypeError, ValueError):
        ratio = 0.0
    try:
        days = int(fields.get("compliance_days", 30))
    except (TypeError, ValueError):
        days = 30

    tags = [t for t in (fields.get("tags") or []) if t in TAG_VOCAB][:5]
    outcome = fields.get("outcome") if fields.get("outcome") in ALLOWED_OUTCOMES else "compensation"

    return {
        "id": f"IK-{tid}",
        "title": title[:140],
        "court": html_to_text(doc.get("docsource", "")) or _DEFAULT_COURT_BY_CATEGORY.get(category, "Civil Court"),
        "year": year,
        "category": category,
        "tags": tags or [_DEFAULT_TAG_BY_CATEGORY.get(category, "service_deficiency")],
        "summary": str(fields.get("summary", ""))[:600],
        "principle": str(fields.get("principle", ""))[:400],
        "outcome": outcome,
        "outcome_detail": str(fields.get("outcome_detail", ""))[:300],
        "relief_amount_ratio": round(ratio, 3),
        "compliance_days": days,
        "citation": html_to_text(doc.get("title", ""))[:160] + (f", ({year})" if year else ""),
        # provenance (auditability)
        "source": "indiankanoon",
        "source_url": f"https://indiankanoon.org/doc/{tid}/",
        "docid": tid,
        "judgment_date": pubdate,
        "verified": False,  # set True after a human reviews the extraction
    }


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest Indian Kanoon judgments into the precedent corpus.")
    ap.add_argument("--token-env", default="DIGINYAYA_INDIANKANOON_TOKEN")
    ap.add_argument(
        "--category",
        default="consumer_dispute",
        choices=sorted(TOPIC_QUERIES_BY_CATEGORY.keys()),
        help="DigiNyaya dispute type these judgments are ingested as (default: consumer_dispute).",
    )
    ap.add_argument("--query", default=DEFAULT_QUERY)
    ap.add_argument(
        "--all-topics",
        action="store_true",
        help="search the full topic-query set for --category (broad, diverse corpus) instead of --query",
    )
    ap.add_argument(
        "--doctypes",
        default=None,
        help="Indian Kanoon doctype filter (e.g. ncdrc). Defaults to 'ncdrc' for consumer_dispute, "
        "unfiltered (all courts) for every other category -- cheque_bounce/money_recovery/"
        "contract_breach judgments come from ordinary civil/criminal courts, not the consumer forum.",
    )
    ap.add_argument("--pages", type=int, default=2, help="search result pages to scan per query")
    ap.add_argument("--limit", type=int, default=10, help="max judgments to fetch + extract")
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--replace", action="store_true", help="replace corpus instead of merging")
    ap.add_argument("--dry-run", action="store_true", help="search only; fetch/extract nothing (cheap)")
    ap.add_argument("--no-llm", action="store_true", help="cache docs but skip LLM extraction")
    args = ap.parse_args()
    if args.doctypes is None:
        args.doctypes = "ncdrc" if args.category == "consumer_dispute" else ""

    token = _token(args.token_env)
    topic_queries = TOPIC_QUERIES_BY_CATEGORY[args.category]
    queries = topic_queries if args.all_topics else [args.query]
    mode = f"{len(queries)} topics" if args.all_topics else f"query='{args.query}'"
    print(f"Indian Kanoon ingestion · category={args.category} · {mode} doctypes='{args.doctypes}' limit={args.limit}")

    # Over-collect when merging so that docs already in the corpus (resume) don't
    # eat into the requested count of NEW precedents.
    pool_target = args.limit if (args.replace or args.dry_run) else args.limit + 30
    results = collect_candidates(token, queries, args.doctypes or None, args.pages, pool_target)
    print(f"Found {len(results)} candidate documents.")
    if args.dry_run:
        for d in results[: args.limit]:
            print(f"  - tid={d.get('tid')}  {html_to_text(d.get('title',''))[:90]}")
        print("Dry run complete (no documents fetched, no extraction).")
        return 0

    if not llm.is_available() and not args.no_llm:
        print("WARNING: local LLM unavailable — extraction will be skipped. Start Ollama or pass --no-llm.")

    # Resume-aware: docs already in the corpus are skipped (no re-extraction).
    existing = [] if args.replace else load_precedents()
    corpus_map: dict[str, dict] = {p["id"]: p for p in existing}
    already = set(corpus_map)

    def checkpoint() -> None:
        # Persist after every extraction so an interrupted run never loses work.
        Path(args.out).write_text(
            json.dumps(list(corpus_map.values()), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    new_count = 0
    start = time.time()
    for d in results:
        if new_count >= args.limit:
            break
        tid = d.get("tid")
        if tid is None:
            continue
        pid = f"IK-{tid}"
        if pid in already:
            print(f"  = {pid} already in corpus (resume skip)")
            continue
        elapsed = time.time() - start
        eta = (elapsed / new_count * (args.limit - new_count)) if new_count else 0
        print(f"[{new_count + 1}/{args.limit}] doc {tid}  (elapsed {elapsed:.0f}s, eta ~{eta:.0f}s)")
        try:
            doc = fetch_doc(token, tid)
        except urllib.error.HTTPError as e:
            print(f"  ! fetch {tid} failed: {e.code}")
            continue
        if args.no_llm:
            print(f"  · cached {tid}")
            continue
        fields = extract_fields(doc)
        if not fields:
            print(f"  ! extraction failed for {tid} (skipped)")
            continue
        prec = build_precedent(doc, fields, args.category)
        ok, why = validate_precedent(prec)
        if not ok:
            print(f"  ! invalid precedent {tid}: {why} (skipped)")
            continue
        corpus_map[pid] = prec
        new_count += 1
        checkpoint()
        print(f"  + {pid}: {prec['outcome']} ratio={prec['relief_amount_ratio']} · {prec['title'][:60]}")

    if args.no_llm:
        print("Cached raw documents only. Re-run without --no-llm to extract.")
        return 0

    print(f"\nWrote {new_count} new · corpus now {len(corpus_map)} precedents -> {args.out}")
    print("NOTE: new entries are marked verified=false — review the extractions before relying on them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
