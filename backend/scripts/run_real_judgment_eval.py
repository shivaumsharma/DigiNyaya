"""Run the real-judgment dataset (scripts/source_eval_judgments.py's output)
through DigiNyaya's actual 5-agent pipeline and report what happened.

SCOPE NOTE: the sourcing script pulled 7 real-world dispute categories, but
DigiNyaya today only has 4 REGISTERED dispute types (consumer_dispute,
money_recovery, contract_breach, cheque_bounce). tenancy/employment/property/
partnership disputes have no home yet. Rather than skip them, each case is
mapped to the closest registered type (see CATEGORY_TO_DISPUTE_TYPE) so the
pipeline can actually run -- but that mapping is a real approximation, not a
true category match, and is reported plainly rather than hidden.

This is NOT an automated "did the AI reach the same verdict as the real
court" grader -- comparing free-text outcomes rigorously is a separate,
harder evaluation-design problem (an LLM-as-judge pass, most likely) that
wasn't built here. What this DOES measure, with real ground truth already in
the sourced data:
  - For the 6 escalation test cases: did the safety gate correctly block the
    case, and for the RIGHT condition (criminal_matter_detected /
    jurisdiction_scope_mismatch), rather than an unrelated one?
  - For the 40 ordinary civil cases: did the pipeline run cleanly (no
    crashes), what tier did it land on, did it unexpectedly escalate, and
    what confidence/precedent-coverage did it produce?

Each escalation test case is assigned a PLAUSIBLE REGISTERED dispute_type
(not "escalation__criminal_matter" etc. literally) precisely so that an
escalation trigger reflects the safety gate's real keyword/heuristic
detection on the case text -- not a trivial "unregistered category name"
mismatch, which would test nothing.

Run (from backend/): python -m scripts.run_real_judgment_eval
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Scripted/deterministic mode, not the real LLM path: mediation.py/analysis.py/
# resolution.py use max_tokens in the 160-260 range, well below what's needed
# for Sarvam's reasoning models to get through their internal chain-of-thought
# and still answer (see scripts/source_eval_judgments.py's extract_fields
# docstring -- empirically needed ~4096 for a similar task). Until that's
# fixed at the call sites, DIGINYAYA_USE_LLM=1 here would mostly just spend
# real API calls that silently fail back to scripted output anyway. The
# safety gate itself has no LLM dependency, so this doesn't weaken the one
# thing this run actually has ground truth to check.
#
# Deliberately NOT set at module level: this module is also imported by
# scripts/judge_real_outcomes.py purely for _build_ctx()/CATEGORY_TO_DISPUTE_TYPE
# -- a module-level os.environ.setdefault() here would leak into that
# script's own process-wide LLM calls the moment it's imported, since
# app.llm.client._llm_disabled() checks this env var with no scoping of its
# own. Set only when this script is actually run as the entry point (below).
sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from app.agents import nlp  # noqa: E402
from app.core import graph  # noqa: E402
from app.core.context import CaseContext  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
_SIGNALS_PATH = _DATA_DIR / "eval_judgments_with_signals.json"
_PLAIN_PATH = _DATA_DIR / "eval_judgments.json"
# Prefer the signal-enriched dataset (scripts/extract_case_signals.py) when
# it exists -- real per-case evidence/defense signals instead of an
# identical placeholder for all 46 cases. Falls back to the plain sourced
# dataset if signal extraction hasn't been run yet.
DATASET_PATH = _SIGNALS_PATH if _SIGNALS_PATH.exists() else _PLAIN_PATH
OUT_PATH = _DATA_DIR / "real_judgment_eval_results.json"

# Best-fit mapping onto the 4 categories DigiNyaya actually has registered
# today (see app.models.DisputeType / app.data.loader.DISPUTE_TYPES).
# tenancy/employment have no real home -> money_recovery (arrears/wage dues
# ARE a recovery-of-money claim, even if the underlying law differs).
# property/partnership -> contract_breach (weakest fit of the four; property
# boundary disputes in particular don't really belong to any registered
# category yet). Escalation test cases get a plausible ordinary-looking
# category so their trigger (or non-trigger) reflects the safety gate's
# actual text-based detection, not a category-name coincidence.
CATEGORY_TO_DISPUTE_TYPE = {
    "contract_disputes": "contract_breach",
    "consumer_complaints": "consumer_dispute",
    "small_claims_debt_recovery": "money_recovery",
    "tenancy_disputes": "money_recovery",
    "employment_disputes": "money_recovery",
    "property_neighbor_disputes": "contract_breach",
    "partnership_business_disputes": "contract_breach",
    "escalation__criminal_matter": "money_recovery",
    "escalation__jurisdiction_mismatch": "consumer_dispute",
}

EXPECTED_CONDITION_BY_CATEGORY = {
    "escalation__criminal_matter": "criminal_matter_detected",
    "escalation__jurisdiction_mismatch": "jurisdiction_scope_mismatch",
}

DEFAULT_CLAIM_AMOUNT = 50_000.0
PLACEHOLDER_EVIDENCE = [{"filename": "source_judgment_reference.pdf", "kind": "document"}]

# Free (no extra LLM call), best-effort party labels for the resolution
# document -- generic "Claimant"/"Respondent" was found to confuse the paid
# judge in scripts/judge_real_outcomes.py: it couldn't reliably map the
# generic labels back to a real judgment's named parties (e.g. "the Bank"/
# "the Borrower"), and scored a couple of cases as mismatches even though the
# AI's actual computed relief direction was correct. Whichever role-word in
# a pair appears FIRST in the narrative is treated as the party who filed --
# matches how these judgments are almost always phrased ("The X ... against
# the Y", "X filed a suit against Y"). Falls back to the old generic labels
# when no known pair is found, same as before.
_PARTY_ROLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("bank", "borrower"),
    ("bank", "defendant"),  # "the plaintiff bank ... against the defendant" -- common phrasing that skips "borrower" entirely
    ("landlord", "tenant"),
    ("employer", "employee"),
    ("insurance company", "insured"),
    ("insurer", "policyholder"),
    ("builder", "buyer"),
    ("developer", "buyer"),
    ("complainant", "opposite party"),
    ("purchaser", "seller"),
    ("creditor", "debtor"),
)


def _infer_party_labels(description: str) -> tuple[str, str]:
    text = description.lower()
    for role_a, role_b in _PARTY_ROLE_PAIRS:
        pos_a, pos_b = text.find(role_a), text.find(role_b)
        if pos_a == -1 or pos_b == -1:
            continue
        first, second = (role_a, role_b) if pos_a < pos_b else (role_b, role_a)
        return f"the {first.title()}", f"the {second.title()}"
    return "Claimant", "Respondent"


def _infer_claim_amount(description: str) -> float:
    amounts = nlp.extract_amounts(description)
    return amounts[0] if amounts else DEFAULT_CLAIM_AMOUNT


def _build_ctx(case: dict) -> CaseContext:
    dispute_type = CATEGORY_TO_DISPUTE_TYPE.get(case["category"], "consumer_dispute")

    # Prefer real, case-specific signals (scripts/extract_case_signals.py --
    # extracted from the facts/arguments section of the actual judgment,
    # NEVER from its conclusion) over the generic placeholder. Every one of
    # these 46 cases is a REAL judgment that went to a full written decision
    # -- by definition contested, not a one-sided demand letter -- so
    # falling back to respondent_submission=None (uncontested) would be
    # wrong for all of them; the placeholder below is the fallback only for
    # cases where signal extraction itself failed.
    signals = case.get("signals")
    if signals:
        n_evidence = max(1, min(int(signals.get("claimant_evidence_count") or 1), 5))
        evidence = [dict(PLACEHOLDER_EVIDENCE[0]) for _ in range(n_evidence)]
        statement = signals.get("respondent_defense_summary") or "The respondent disputes the claim."
        # A real respondent typing their own defense would naturally name the
        # legal ground they're relying on in their own words (which is
        # exactly what app.agents.nlp.score_defense_substance scans free
        # text for) -- extract_case_signals.py's original 30-word compressed
        # defense_summary sometimes dropped the doctrine name even when the
        # judgment's own arguments section clearly stated it (e.g. "barred
        # by limitation"), understating a genuinely dispositive defense.
        # respondent_legal_ground is extracted separately, from the same
        # facts/arguments-only text, specifically to name the doctrine; folded
        # into the same free-text statement here (not a new schema field)
        # since that's the one input score_defense_substance actually reads.
        ground = signals.get("respondent_legal_ground")
        if ground and str(ground).strip().lower() not in ("null", "none", ""):
            # Naming a doctrine isn't evidence that it applies -- real courts
            # reject unsupported invocations constantly (see nlp.py's
            # _UNSUPPORTED_ASSERTION_MARKER, which score_defense_substance()
            # specifically checks for). Whether a concrete fact was actually
            # offered in support (an exact date/figure/document/registration
            # number, not just the doctrine's name) was extracted separately
            # so the strength score can react to it, the same way it already
            # reacts to the claimant's evidence_count.
            has_support = signals.get("respondent_ground_has_specific_support")
            if has_support:
                statement = (
                    f"{statement} The respondent's defense relies on: {ground}, "
                    "supported by specific facts in the record."
                )
            else:
                statement = (
                    f"{statement} The respondent's defense relies on: {ground}, "
                    "without citing any specific supporting facts."
                )
        respondent_submission = {
            "statement": statement,
            "accepts_liability": bool(signals.get("respondent_accepts_liability")),
            "counter_offer": signals.get("respondent_offered_settlement_amount"),
        }
    else:
        evidence = list(PLACEHOLDER_EVIDENCE)
        respondent_submission = {"statement": "The respondent disputes the claim.", "accepts_liability": False}

    claimant_name, respondent_name = _infer_party_labels(case["case_description"])

    return CaseContext(
        case_id=case["case_id"],
        owner_id="real_judgment_eval",
        dispute_type=dispute_type,
        claimant_name=claimant_name,
        respondent_name=respondent_name,
        claim_amount=_infer_claim_amount(case["case_description"]),
        description=case["case_description"],
        evidence=evidence,
        respondent_submission=respondent_submission,
    )


def run_one(case: dict) -> dict:
    ctx = _build_ctx(case)
    result = {
        "case_id": case["case_id"],
        "category": case["category"],
        "mapped_dispute_type": ctx.dispute_type,
        "escalation_expected": case["escalation_expected"],
        "expected_condition": EXPECTED_CONDITION_BY_CATEGORY.get(case["category"]),
        "error": None,
    }
    try:
        list(graph.run_pipeline(ctx))
    except Exception as exc:  # a crash is itself a critical finding, not something to hide
        result["error"] = f"pipeline crashed: {exc}"
        return result

    result["escalated"] = ctx.escalation is not None
    result["escalation_conditions"] = ctx.escalation["triggered_conditions"] if ctx.escalation else []
    result["escalation_checkpoint"] = ctx.escalation["checkpoint"] if ctx.escalation else None
    result["tier"] = ctx.tier if ctx.escalation is None else None
    result["ingestion_confidence"] = ctx.ingestion.confidence if ctx.ingestion else None

    if ctx.escalation is None and ctx.mediation is not None:
        try:
            list(graph.run_resolution(ctx, via_mediation=True))
        except Exception as exc:
            result["error"] = f"resolution crashed: {exc}"
            return result
        result["escalated_at_resolution"] = ctx.escalation is not None
        if ctx.escalation is not None:
            result["escalation_conditions"] = ctx.escalation["triggered_conditions"]
            result["escalation_checkpoint"] = ctx.escalation["checkpoint"]
        result["resolved"] = ctx.resolution is not None
        if ctx.resolution:
            result["composite_confidence"] = ctx.resolution.composite_confidence.get("score")
            result["precedents_retrieved"] = len(ctx.research.precedents) if ctx.research else 0
            result["requires_human_signoff"] = ctx.resolution.requires_human_signoff
    return result


def main() -> int:
    os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    print(f"Running {len(cases)} real-judgment case(s) through the DigiNyaya pipeline "
          f"(DIGINYAYA_USE_LLM={os.environ.get('DIGINYAYA_USE_LLM')})...")

    results = []
    for i, case in enumerate(cases):
        print(f"[{i + 1}/{len(cases)}] {case['case_id']} ({case['category']})...", end=" ", flush=True)
        r = run_one(case)
        results.append(r)
        if r.get("error"):
            print(f"CRASHED: {r['error']}")
        elif r["escalated"]:
            print(f"escalated ({', '.join(r['escalation_conditions'])})")
        else:
            print(f"tier={r['tier']} resolved={r.get('resolved')} confidence={r.get('composite_confidence')}")

    OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- summary ----
    escalation_cases = [r for r in results if r["escalation_expected"]]
    civil_cases = [r for r in results if not r["escalation_expected"]]
    crashed = [r for r in results if r.get("error")]

    print("\n" + "=" * 70)
    print(f"Wrote {len(results)} result(s) -> {OUT_PATH}\n")

    print(f"ESCALATION TEST CASES ({len(escalation_cases)} total, ground truth from sourcing):")
    correct = 0
    for r in escalation_cases:
        got_right_condition = r["expected_condition"] in r.get("escalation_conditions", [])
        status = "CORRECT" if r.get("escalated") and got_right_condition else (
            "ESCALATED (wrong condition)" if r.get("escalated") else "MISSED -- did NOT escalate"
        )
        if r.get("escalated") and got_right_condition:
            correct += 1
        print(f"  {r['case_id']}: expected={r['expected_condition']} -> {status} "
              f"(triggered: {r.get('escalation_conditions')})")
    print(f"  --> {correct}/{len(escalation_cases)} correctly escalated for the expected reason\n")

    print(f"ORDINARY CIVIL CASES ({len(civil_cases)} total):")
    unexpected_escalations = [r for r in civil_cases if r.get("escalated")]
    resolved = [r for r in civil_cases if r.get("resolved")]
    print(f"  {len(unexpected_escalations)} unexpectedly escalated (not necessarily wrong -- "
          f"these are real, sometimes messy judgments; but worth reviewing)")
    for r in unexpected_escalations:
        print(f"    - {r['case_id']} ({r['category']} -> {r['mapped_dispute_type']}): {r.get('escalation_conditions')}")
    print(f"  {len(resolved)}/{len(civil_cases) - len(unexpected_escalations)} of the non-escalated cases "
          f"reached a drafted resolution")
    confidences = [r["composite_confidence"] for r in resolved if r.get("composite_confidence") is not None]
    if confidences:
        print(f"  average composite confidence: {round(sum(confidences) / len(confidences), 3)}")
    precedent_counts = [r["precedents_retrieved"] for r in resolved if r.get("precedents_retrieved") is not None]
    if precedent_counts:
        print(f"  average precedents retrieved: {round(sum(precedent_counts) / len(precedent_counts), 1)}")

    if crashed:
        print(f"\nCRASHES: {len(crashed)} case(s) crashed the pipeline -- see result file for tracebacks:")
        for r in crashed:
            print(f"  - {r['case_id']}: {r['error']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
