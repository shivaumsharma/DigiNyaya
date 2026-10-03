"""Fill in the CLAIMED amount for cases whose sourced case_description doesn't
state one.

WHY: ~29% of the money-category cases describe the claim only as "a specified
principal sum", so run_real_judgment_eval._infer_claim_amount() finds no rupee
figure and falls back to a flat Rs. 50,000 -- against real awards whose median
in that group is ~Rs. 2.77 lakh. Every downstream amount is then meaningless.
3,525 of the 5,124 such cases DO have an amount in the judgment; it was simply
dropped when the description was written.

INTEGRITY CONSTRAINT (same as extract_case_signals.py): the amount extracted is
the sum the plaintiff CLAIMED in the plaint/prayer, never the amount the court
decreed. Only the head of the judgment (facts/pleadings/prayer) is shown to the
model -- not the tail, where the operative order lives -- and the prompt says
explicitly to ignore the decreed amount. Even so, when a suit is undefended
the claimed and decreed principal coincide -- see scripts/compare_eval_runs.py,
which reports how often the extracted claim equals the real decree, next to the
same rate for description-parsed claims, as a leakage sanity check.

Only cases with no parseable amount in case_description are sent to the LLM.
Result is written into case["claimed_amount_rupees"] (number or
null) of the dataset file being processed (DIGINYAYA_EVAL_DATASET, else the
signals file). Incremental: cases already carrying the key are skipped.

Run (from backend/): python -m scripts.extract_claim_amounts --workers 3
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import threading
import time

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from app import llm  # noqa: E402
from app.agents import nlp  # noqa: E402
from scripts.extract_case_signals import CACHE_DIR, _LLM_ATTEMPTS, _RETRY_BACKOFF_SECONDS  # noqa: E402
from scripts.run_real_judgment_eval import DATASET_PATH  # noqa: E402
from scripts.source_eval_judgments import html_to_text  # noqa: E402

_HEAD_CHARS = 6000
KEY = "claimed_amount_rupees"
CHECKPOINT_EVERY = 100
# Results are checkpointed to a small side file (case_id -> amount) and merged
# into the dataset at the end: a long run killed part-way (this environment
# does that) resumes instead of losing everything, and the ~50 MB dataset isn't
# rewritten every few cases.
PARTIAL_PATH = DATASET_PATH.with_name(DATASET_PATH.stem + ".claim_amounts.partial.json")


def extract_claimed_amount(case: dict) -> tuple[bool, float | None]:
    """(ok, amount). ok=False means the LLM call failed (retry next run);
    ok=True with amount None means the judgment genuinely states no figure."""
    cache_file = CACHE_DIR / f"{case['source']['docid']}.json"
    if not cache_file.exists():
        return True, None
    body = html_to_text(json.loads(cache_file.read_text(encoding="utf-8")).get("doc", ""))
    if len(body) < 200:
        return True, None
    prompt = (
        "Below is the BEGINNING of an Indian court judgment (parties, facts, pleadings, prayer). "
        "Extract ONE number: the total sum in rupees that the PLAINTIFF/CLAIMANT CLAIMED or PRAYED FOR "
        "in the suit -- the principal being sued for (add pre-suit interest only if the plaint itself "
        "states a single combined suit value). Do NOT use any amount the court decreed, awarded or "
        "ordered; do NOT use court fees, costs or later interest. Convert lakh/crore to a plain "
        "number (Rs. 4,00,000 -> 400000). If the text states no claimed sum, use null. "
        'Return JSON only: {"claimed_amount_rupees": <number or null>}\n\n'
        f"JUDGMENT TEXT:\n{body[:_HEAD_CHARS]}"
    )
    for attempt in range(_LLM_ATTEMPTS):
        data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=300)
        if data is not None:
            v = data.get(KEY)
            return True, float(v) if isinstance(v, (int, float)) and v > 0 else None
        if attempt < _LLM_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_SECONDS)
    return False, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract claimed amounts for cases lacking one in their description.")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    if not llm.is_available():
        print("ERROR: LLM unavailable. Aborting.")
        return 1

    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    done_amounts: dict[str, float | None] = (
        json.loads(PARTIAL_PATH.read_text(encoding="utf-8")) if PARTIAL_PATH.exists() else {}
    )
    for c in cases:
        if c["case_id"] in done_amounts and KEY not in c:
            c[KEY] = done_amounts[c["case_id"]]
    if done_amounts:
        print(f"Resumed {len(done_amounts)} result(s) from {PARTIAL_PATH.name}.")
    todo = [
        c for c in cases
        if not nlp.extract_amounts(c["case_description"])
        and KEY not in c
    ]
    print(f"{len(cases)} case(s) in {DATASET_PATH.name}; {len(todo)} lack a description-stated claim and need extraction.")

    lock = threading.Lock()
    tally = {"n": 0, "found": 0, "failed": 0}

    def process(case: dict) -> None:
        ok, amount = extract_claimed_amount(case)
        with lock:
            tally["n"] += 1
            if not ok:
                tally["failed"] += 1
                print(f"[{tally['n']}/{len(todo)}] {case['case_id']} FAILED (will retry on next run)")
                return
            # Top-level, not inside case["signals"]: a failed-extraction case has
            # signals=None, and _build_ctx treats a non-empty signals dict as
            # "real signals present" -- adding a lone key there would flip it off
            # the placeholder path as a side effect.
            case[KEY] = amount
            done_amounts[case["case_id"]] = amount
            if tally["n"] % CHECKPOINT_EVERY == 0:
                PARTIAL_PATH.write_text(json.dumps(done_amounts), encoding="utf-8")
            if amount:
                tally["found"] += 1
            print(f"[{tally['n']}/{len(todo)}] {case['case_id']} claimed={amount}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(process, todo))

    DATASET_PATH.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDone: {tally['found']} amount(s) found, {tally['n'] - tally['found'] - tally['failed']} genuinely absent, "
          f"{tally['failed']} LLM failure(s) -> {DATASET_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
