"""Extract REAL per-case evidence/defense signals from the cached raw
judgment text, to replace the uniform placeholders run_real_judgment_eval.py
was using for every one of the 46 real cases.

WHY THIS EXISTS: after fixing the "always awards relief" bug (see
app/agents/analysis.py, mediation.py, resolution.py), re-testing against the
46 real judgments showed the pipeline COULD now dismiss cases correctly (3
genuine matches, up from 0) -- but overall accuracy was still low, because
every case was fed the exact same generic evidence count (1 placeholder
item) and the exact same generic respondent stance ("disputes the claim, no
counter-offer"). The heuristic had no way to tell a genuinely strong real
case from a genuinely weak one -- it was applying an identical template to
all 46, which can't be reliably right without real per-case signal.

INTEGRITY CONSTRAINT (read before touching this file): every signal
extracted here MUST come from the judgment's FACTS/ARGUMENTS section only --
NEVER from the court's actual holding/conclusion/outcome. Leaking the real
verdict into the pipeline's input would make any resulting "accuracy" number
meaningless (the model would effectively be shown the answer). This is the
same discipline scripts/source_eval_judgments.py already applied when it
split case_description (facts/issues) from expected_outcome (conclusion) --
this script extracts MORE facts-side detail, never outcome-side detail.

Uses the same cached raw judgment text scripts/source_eval_judgments.py
already fetched (backend/data_cache/indiankanoon/<docid>.json) -- no new
Indian Kanoon API calls, no new cost there. Only cost is one more LLM
extraction pass per case (~46 calls, same order of cost as the original
sourcing pass).

Run (from backend/): python -m scripts.extract_case_signals
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from app import llm  # noqa: E402
from scripts.source_eval_judgments import html_to_text, _window  # noqa: E402

DATASET_PATH = Path(__file__).resolve().parent.parent / "data_cache" / "eval_judgments.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "data_cache" / "eval_judgments_with_signals.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache" / "indiankanoon"


def extract_signals(case: dict) -> dict | None:
    docid = case["source"]["docid"]
    cache_file = CACHE_DIR / f"{docid}.json"
    if not cache_file.exists():
        return None
    doc = json.loads(cache_file.read_text(encoding="utf-8"))
    body = html_to_text(doc.get("doc", ""))
    if len(body) < 200:
        return None

    schema = (
        "{"
        '"claimant_evidence_count": <int 0-5, distinct evidence items/documents/witnesses the '
        "CLAIMANT relies on, per the facts section only. A specific quantified claim resting on a "
        "clear transactional instrument (an invoice, loan/hypothecation agreement, cheque, signed "
        "contract) counts as at least 2, even if the facts section doesn't itemize every document "
        "individually -- a quantified sum with a named instrument implies the paperwork behind it "
        "was actually placed before the court, which a vague/unquantified grievance does not.>, "
        '"respondent_defense_summary": "<=30 words: what the respondent argued in their defense, '
        'from the facts/arguments section only>", '
        '"respondent_legal_ground": "<the SPECIFIC named legal doctrine or procedural ground the '
        "respondent's defense rests on, if any was actually argued -- e.g. 'limitation', 'lack of "
        "jurisdiction', 'no privity of contract', 'res judicata', 'arbitration clause', 'non-joinder "
        "of necessary parties', 'estoppel', 'protected/statutory tenant', 'landlord failed to prove "
        "ownership', 'plaintiff lacks locus standi', 'claim barred', 'bona fide requirement not "
        "proved'. Name the DOCTRINE/GROUND itself, not just the underlying facts -- e.g. if the "
        "respondent argued the suit was filed too late, write 'limitation', not a restatement of the "
        "dates. null if the defense is a plain factual denial with no such named ground.>\", "
        '"respondent_ground_has_specific_support": <true ONLY if the arguments section states the '
        "SPECIFIC FACT THAT WOULD SATISFY the named ground's own conditions -- not merely that a rule "
        "or doctrine was invoked BY NAME. Naming 'Order 23 Rule 1' is NOT support by itself; stating "
        "that this suit follows an earlier WITHDRAWN suit on the same claim IS. Naming 'limitation' is "
        "NOT support; stating the specific date the cause of action arose and the date of filing IS. "
        "Naming 'lack of jurisdiction' is NOT support; stating the specific claimed amount against the "
        "specific pecuniary threshold IS. If the respondent's own defense effort itself collapsed "
        "procedurally (e.g. they sought an extension/condonation but then failed to complete a "
        "required step, like paying costs or filing on time), treat that as false, NOT true -- a "
        "defense that failed on its own terms is not 'supported'. false if only a doctrine's NAME or "
        "a general legal conclusion is stated with no underlying fact satisfying its own conditions. "
        "null if no ground was named at all (respondent_legal_ground is null).>, "
        '"respondent_accepts_liability": <true only if the respondent explicitly admitted/conceded '
        "the claim in their pleadings -- not if the court later ruled against them>, "
        '"respondent_offered_settlement_amount": <number or null -- ONLY if the facts state the '
        "respondent offered a specific lesser amount during the dispute (not a court-ordered amount)>"
        "}"
    )
    prompt = (
        "Read this Indian court judgment's FACTS AND ARGUMENTS ONLY -- ignore and do not reference "
        "the court's holding, conclusion, or final order anywhere in your answer. Extract what each "
        "side argued BEFORE the court decided. Return JSON only, matching this schema: "
        f"{schema}\n\nJUDGMENT TEXT:\n{_window(body)}"
    )
    # max_tokens=2000, not 4096: this resolves to the fast-tier model
    # (sarvam_fast_model), and Sarvam's newer sarvam-105b-conversations --
    # the replacement for the now-deprecated sarvam-30b -- caps max_tokens
    # at 2048 on the starter subscription tier (a hard 400, not a graceful
    # truncation, confirmed via a raw API call). See judge_real_outcomes.py
    # for the same fix and full explanation.
    return llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, max_tokens=2000)


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract per-case signals for the real-judgment eval dataset.")
    ap.add_argument("--fresh", action="store_true",
                     help="re-extract every case, ignoring any already-signalled entries in the existing output")
    args = ap.parse_args()

    if not llm.is_available():
        print("ERROR: LLM unavailable -- signal extraction needs a real LLM call. Aborting.")
        return 1

    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    # Incremental by default: a prior run's signals are real per-case facts
    # extracted from a judgment's text, which never change on re-run -- only
    # NEWLY added cases (from an incremental scripts/source_eval_judgments.py
    # run) need a fresh extraction call, so re-paying for the ones a previous
    # run already signalled would be pure waste.
    already_signalled: dict[str, dict] = {}
    if not args.fresh and OUT_PATH.exists():
        prior = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        already_signalled = {c["case_id"]: c for c in prior if c.get("signals")}
        print(f"Loaded {len(already_signalled)} already-signalled case(s) from {OUT_PATH} -- "
              "only extracting for new/missing cases (use --fresh to redo everything).")

    enriched = []
    failures = []
    to_extract = [c for c in cases if c["case_id"] not in already_signalled]
    print(f"{len(cases)} total case(s), {len(to_extract)} need extraction.")
    for i, case in enumerate(cases):
        if case["case_id"] in already_signalled:
            enriched.append(already_signalled[case["case_id"]])
            continue
        idx = [c["case_id"] for c in to_extract].index(case["case_id"]) + 1
        print(f"[{idx}/{len(to_extract)}] {case['case_id']}...", end=" ", flush=True)
        signals = extract_signals(case)
        if signals is None:
            print("FAILED (no signals extracted -- will fall back to placeholder defaults)")
            failures.append(case["case_id"])
            case["signals"] = None
        else:
            print(
                f"evidence={signals.get('claimant_evidence_count')} "
                f"accepts_liability={signals.get('respondent_accepts_liability')} "
                f"counter={signals.get('respondent_offered_settlement_amount')} "
                f"ground_supported={signals.get('respondent_ground_has_specific_support')}"
            )
            case["signals"] = signals
        enriched.append(case)

    OUT_PATH.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(enriched)} case(s) -> {OUT_PATH}")
    if failures:
        print(f"{len(failures)} case(s) failed extraction and will use placeholder defaults: {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
