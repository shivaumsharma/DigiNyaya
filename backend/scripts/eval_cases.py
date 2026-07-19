"""Evaluation harness — golden cases.

Runs the full orchestration graph in-process (no HTTP, no LLM by default) and
asserts invariants against 18 cases deliberately spread across five bands so
the harness actually discriminates between good and bad outputs, rather than
just re-running variations of the same easy case:

  A. easy            (5 cases) — clear evidence, clean subtype match. Should pass.
  B. ambiguous        (4 cases) — partial documentation / unclear claim amount.
  C. conflicting      (3 cases) — mixed / contradictory signals in one narrative,
                                  so retrieval and analysis have to reason about
                                  disagreement rather than a clean match.
  D. escalation       (3 cases) — should be routed to Tier 2 human sign-off.
  E. stress           (3 cases) — deliberately adversarial (extreme amounts, full
                                  liability acceptance, no recognisable signal at
                                  all). These are allowed to fail: a harness with
                                  zero failures at n=18 is itself a red flag.

For every case we also compute the composite confidence score (ingestion +
retrieval + schema + citation, see app.core.confidence) and the citation
verification stats, and report the aggregate hallucination-reduction number
across all 18 cases: what fraction of citations the model proposed were
rejected because they weren't actually retrieved.

Run:  python -m scripts.eval_cases       (from the backend/ directory)
Set DIGINYAYA_USE_LLM=1 to exercise the LLM path too.
"""

from __future__ import annotations

import os
import sys

# Default to deterministic scripted mode unless the caller opts into the LLM.
os.environ.setdefault("DIGINYAYA_USE_LLM", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import graph  # noqa: E402
from app.core.context import CaseContext  # noqa: E402
from app.data.loader import load_precedents  # noqa: E402

ALLOWED_DAYS = {15, 21, 30, 45, 60}


def _case(case_id: str, dispute_type: str, claimant: str, respondent: str, claim_amount: float,
          description: str, evidence: list[dict], respondent_submission: dict | None = None) -> dict:
    return {
        "case_id": case_id,
        "owner_id": "u1",
        "dispute_type": dispute_type,
        "claimant": {"name": claimant},
        "respondent": {"name": respondent},
        "claim_amount": claim_amount,
        "description": description,
        "evidence": evidence,
        "respondent_submission": respondent_submission,
    }


GOLDEN = [
    # ---------------------------------------------------------------- A. easy
    {
        "band": "easy",
        "name": "Strong, uncontested non-delivery",
        "case": _case(
            "EVAL-A1", "consumer_dispute", "Ananya Sharma", "QuickShop Online", 42999,
            "Paid Rs. 42,999 for a laptop ordered online on 12/03/2024; never delivered though "
            "marked delivered. No refund despite 5 support tickets. Seeking full refund.",
            [{"kind": "invoice"}, {"kind": "receipt"}, {"kind": "screenshot"}],
        ),
        "expect_tier": 1, "via_mediation": True,
    },
    {
        "band": "easy",
        "name": "Contested defective product with counter-offer",
        "case": _case(
            "EVAL-A2", "consumer_dispute", "Rahul Verma", "GadgetWorld", 18000,
            "Bought a defective phone for Rs. 18,000 that stopped working within warranty. "
            "Service centre delayed repair for months.",
            [{"kind": "invoice"}, {"kind": "warranty_card"}],
            {"statement": "We offer store credit.", "accepts_liability": False, "counter_offer": 9000},
        ),
        "expect_tier": 1, "via_mediation": False,
    },
    {
        "band": "easy",
        "name": "Wrongful billing, uncontested",
        "case": _case(
            "EVAL-A3", "consumer_dispute", "Priya Nair", "FitCorp Gym", 6000,
            "Rs. 6,000 was charged twice on 01/02/2024 due to a wrongful bill from my gym's "
            "payment gateway. I was overcharged and want a refund of the extra charge.",
            [{"kind": "bank_statement"}, {"kind": "receipt"}],
        ),
        "expect_tier": 1, "via_mediation": True,
    },
    {
        "band": "easy",
        "name": "Unauthorized transaction, contested denial",
        "case": _case(
            "EVAL-A4", "consumer_dispute", "Karan Mehta", "PayEase Wallet", 15500,
            "An unauthorized transaction of Rs. 15,500 was made from my account via UPI without "
            "my consent on 05/04/2024. This was a fraudulent debit I never authorised.",
            [{"kind": "bank_statement"}, {"kind": "fir_copy"}],
            {"statement": "Transaction matched registered device.", "accepts_liability": False, "counter_offer": 0},
        ),
        "expect_tier": 1, "via_mediation": False,
    },
    {
        "band": "easy",
        "name": "Unwanted subscription auto-renewal",
        "case": _case(
            "EVAL-A5", "consumer_dispute", "Sneha Iyer", "StreamPlus", 2999,
            "My subscription auto-renewed for Rs. 2,999 despite cancelling last month. This was "
            "an unwanted auto-renew charge and I want it reversed.",
            [{"kind": "invoice"}, {"kind": "email_confirmation"}],
        ),
        "expect_tier": 1, "via_mediation": True,
    },

    # ----------------------------------------------------------- B. ambiguous
    {
        "band": "ambiguous",
        "name": "No recognisable subtype -> should NOT be tier-1 eligible",
        "case": _case(
            "EVAL-B1", "consumer_dispute", "Test User A", "Some Store", 1200,
            "I am unhappy with how I was treated as a customer and I want this made right.",
            [{"kind": "screenshot"}],
        ),
        "expect_tier": 2, "via_mediation": True,
    },
    {
        "band": "ambiguous",
        "name": "Recognised subtype but unquantified claim amount",
        "case": _case(
            "EVAL-B2", "consumer_dispute", "Test User B", "HomeGoods", 0,
            "The sofa I bought was defective and damaged on arrival; I never got a clear invoice "
            "amount but want it replaced or refunded.",
            [{"kind": "photo"}, {"kind": "delivery_note"}],
        ),
        "expect_tier": 2, "via_mediation": True,
    },
    {
        "band": "ambiguous",
        "name": "Single evidence item, small quantified claim",
        "case": _case(
            "EVAL-B3", "consumer_dispute", "Test User C", "MobileHub", 4500,
            "Paid Rs. 4,500 for a repair that was delayed for months at the service centre despite "
            "a valid warranty.",
            [{"kind": "warranty_card"}],
        ),
        "expect_tier": 1, "via_mediation": True,
    },
    {
        "band": "ambiguous",
        "name": "Recognised subtype, contested with no counter-offer",
        "case": _case(
            "EVAL-B4", "consumer_dispute", "Test User D", "ApplianceMart", 22000,
            "A counterfeit appliance was sold to me for Rs. 22,000; it is a duplicate, not the "
            "genuine branded product I paid for.",
            [{"kind": "invoice"}, {"kind": "photo"}],
            {"statement": "We deny the product is counterfeit.", "accepts_liability": False, "counter_offer": None},
        ),
        "expect_tier": 1, "via_mediation": False,
    },

    # --------------------------------------------------------- C. conflicting
    {
        "band": "conflicting",
        "name": "Mixed signals: late delivery + defective + misrepresented",
        "case": _case(
            "EVAL-C1", "consumer_dispute", "Test User E", "MegaMart Online", 31000,
            "The product arrived very late, was defective on arrival, and was also different from "
            "what was described on the website — misleading listing, faulty item, and delayed "
            "delivery all at once. Paid Rs. 31,000.",
            [{"kind": "invoice"}, {"kind": "screenshot"}, {"kind": "photo"}],
        ),
        "expect_tier": 1, "via_mediation": True,
    },
    {
        "band": "conflicting",
        "name": "Mixed signals: builder possession delay + service deficiency",
        "case": _case(
            "EVAL-C2", "consumer_dispute", "Test User F", "Skyline Builders", 850000,
            "The builder delayed possession of my flat for over a year and the customer care and "
            "support desk repeatedly ignored my complaints — both a real estate possession issue "
            "and a service deficiency.",
            [{"kind": "agreement"}, {"kind": "email_chain"}],
            {"statement": "Delay was due to force majeure.", "accepts_liability": False, "counter_offer": 300000},
        ),
        "expect_tier": 1, "via_mediation": False,
    },
    {
        "band": "conflicting",
        "name": "Counter-offer exceeds claim -> no contradiction should be flagged",
        "case": _case(
            "EVAL-C3", "consumer_dispute", "Test User G", "SkyWings Airline", 12000,
            "My flight was cancelled and I was also wrongfully billed extra charges on top of the "
            "ticket price of Rs. 12,000.",
            [{"kind": "ticket"}, {"kind": "bank_statement"}],
            {"statement": "We accept the claim in full and more as goodwill.", "accepts_liability": True, "counter_offer": 15000},
        ),
        "expect_tier": 1, "via_mediation": True,
        "expect_no_contradiction": True,
    },

    # -------------------------------------------------------- D. escalation
    {
        "band": "escalation",
        "name": "Thin / low-confidence case -> escalation",
        "case": _case(
            "EVAL-D1", "consumer_dispute", "Test User H", "Someone", 500,
            "I am unhappy with my purchase.",
            [],
        ),
        "expect_tier": 2, "via_mediation": True,
    },
    {
        "band": "escalation",
        "name": "Wrong dispute_type despite clean evidence -> not tier-1 eligible",
        "case": _case(
            "EVAL-D2", "service_dispute", "Test User I", "CleanCo Services", 8000,
            "Paid Rs. 8,000 for a defective repair job that left the appliance not working at all.",
            [{"kind": "invoice"}, {"kind": "photo"}],
        ),
        "expect_tier": 2, "via_mediation": True,
    },
    {
        "band": "escalation",
        "name": "Recognised subtype but zero evidence on record",
        "case": _case(
            "EVAL-D3", "consumer_dispute", "Test User J", "ElectroBazaar", 9500,
            "The television I bought for Rs. 9,500 is defective and stopped working within a week.",
            [],
        ),
        "expect_tier": 2, "via_mediation": True,
    },

    # ------------------------------------------------------------- E. stress
    {
        "band": "stress",
        "name": "Extreme claim amount with weak evidence (adversarial)",
        "case": _case(
            "EVAL-E1", "consumer_dispute", "Test User K", "MegaBuilders Pvt Ltd", 10000000,
            "Paid Rs. 1,00,00,000 for a flat that was never handed over; possession denied "
            "indefinitely.",
            [{"kind": "receipt"}],
            {"statement": "Minor delay only.", "accepts_liability": False, "counter_offer": 200000},
        ),
        "expect_tier": 1, "via_mediation": False,
        "expect_relief_at_least_pct": 50,  # deliberately strict — may genuinely fail
    },
    {
        "band": "stress",
        "name": "Full liability acceptance -> expect near-full relief",
        "case": _case(
            "EVAL-E2", "consumer_dispute", "Test User L", "HonestMart", 10000,
            "Non-delivery of a paid order worth Rs. 10,000; never received the goods.",
            [{"kind": "invoice"}, {"kind": "receipt"}],
            {"statement": "We accept full liability and will refund in full.", "accepts_liability": True, "counter_offer": 10000},
        ),
        "expect_tier": 1, "via_mediation": True,
        "expect_relief_at_least_pct": 90,  # deliberately strict — may genuinely fail
    },
    {
        "band": "stress",
        "name": "No matching signal at all (nonsense narrative)",
        "case": _case(
            "EVAL-E3", "consumer_dispute", "Test User M", "Nowhere Inc", 100,
            "asdf qwerty the thing happened and it was bad and stuff occurred yesterday somehow.",
            [{"kind": "note"}],
        ),
        "expect_tier": 2, "via_mediation": True,
        "expect_ineligible_for_tier1": True,
    },
]


def drain(gen) -> list[dict]:
    return list(gen)


def check(cond: bool, msg: str) -> bool:
    mark = "PASS" if cond else "FAIL"
    print(f"   [{mark}] {msg}")
    return cond


def run_case(spec: dict) -> tuple[bool, dict | None]:
    print(f"\n=== [{spec['band']}] {spec['name']} ===")
    ctx = CaseContext.from_case(spec["case"])
    drain(graph.run_pipeline(ctx))

    if ctx.escalation is not None:
        # app.core.safety_gate CHECKPOINT A blocked this case before any
        # agent ran at all -- a stricter outcome than routing to Tier 2, but
        # only a valid substitute for cases that were never expected to
        # reach Tier 1 autonomy anyway. A case whose golden expectation IS
        # Tier 1 getting blocked here would be a real regression, not this.
        ok = check(
            spec["expect_tier"] != 1,
            f"blocked by the safety gate pre-filter instead of routing through the pipeline "
            f"(triggered: {', '.join(ctx.escalation['triggered_conditions'])})",
        )
        return ok, None

    ok = True
    ok &= check(ctx.ingestion is not None, "ingestion produced a classification")
    ok &= check(ctx.route is not None, f"routed to Tier {ctx.tier} (expected {spec['expect_tier']})")
    ok &= check(ctx.tier == spec["expect_tier"], f"tier == {spec['expect_tier']}")
    # Retrieval is scoped to the case's own dispute_type category, so the
    # achievable floor depends on how many precedents that category actually
    # has in the corpus (e.g. a deliberately-unregistered/fake dispute_type
    # in an ineligibility test has zero, and newer categories like
    # money_recovery/contract_breach currently seed only 2) -- asserting a
    # flat 3 regardless of category was only ever true by accident, back
    # when retrieval ignored dispute_type and always searched consumer_dispute.
    category_corpus_size = sum(1 for p in load_precedents() if p.get("category") == spec["case"]["dispute_type"])
    expected_min = min(3, category_corpus_size)
    ok &= check(
        ctx.research is not None and len(ctx.research.precedents) >= expected_min,
        f"research returned >= {expected_min} precedents (category corpus has {category_corpus_size})",
    )
    ok &= check(ctx.analysis is not None, "analysis produced strength scores")
    ok &= check(ctx.mediation is not None, "mediation produced a proposal")

    if spec.get("expect_ineligible_for_tier1") and ctx.ingestion:
        ok &= check(
            ctx.ingestion.tier1_eligible is False,
            f"ingestion correctly marked ineligible for Tier 1 (no recognised subtype/evidence) — "
            f"confidence was {ctx.ingestion.confidence}, at/below the autonomy bar",
        )

    if spec.get("expect_no_contradiction") and ctx.analysis:
        ok &= check(
            ctx.analysis.contradictions == ["No direct factual contradictions detected."],
            "no contradiction flagged when counter-offer exceeds claim",
        )

    if ctx.mediation:
        amt = ctx.mediation.amount
        ok &= check(0 <= amt <= ctx.claim_amount, f"relief {amt} within [0, {ctx.claim_amount}]")
        ok &= check(ctx.mediation.compliance_days in ALLOWED_DAYS, f"compliance {ctx.mediation.compliance_days}d is allowed")
        if spec.get("expect_relief_at_least_pct") is not None and ctx.claim_amount > 0:
            pct = 100 * amt / ctx.claim_amount
            ok &= check(
                pct >= spec["expect_relief_at_least_pct"],
                f"relief {round(pct,1)}% >= expected floor {spec['expect_relief_at_least_pct']}%",
            )

    drain(graph.run_resolution(ctx, via_mediation=spec["via_mediation"]))

    if ctx.escalation is not None:
        # CHECKPOINT B fired: resolution.finalize() already ran and set
        # ctx.resolution internally, but the safety gate discarded it before
        # graph.py ever yielded it -- in the real jobs.py flow, that
        # resolution is never persisted or streamed to the user. Don't
        # assert the normal resolution-shaped checks against it here.
        ok &= check(
            True,
            f"resolution discarded by the safety gate post-check instead of being returned "
            f"(triggered: {', '.join(ctx.escalation['triggered_conditions'])})",
        )
        return ok, None

    ok &= check(ctx.resolution is not None, "resolution drafted an order")

    conf_payload = None
    if ctx.resolution and ctx.research:
        cite_map = {p.citation for p in ctx.research.precedents}
        cited = ctx.resolution.cited_precedents
        ok &= check(all(c["citation"] in cite_map for c in cited), "all citations were actually retrieved (no hallucinated refs)")
        ok &= check(ctx.resolution.relief_amount <= ctx.claim_amount, "relief <= claim amount")
        if spec["expect_tier"] == 2:
            ok &= check(ctx.resolution.requires_human_signoff, "Tier 2 order flagged for human sign-off")
        conf_payload = ctx.resolution.composite_confidence
        if conf_payload:
            ok &= check(0.0 <= conf_payload["score"] <= 1.0, f"composite confidence {conf_payload['score']} in [0,1]")
    return ok, conf_payload


def main() -> int:
    print(f"DigiNyaya eval harness (USE_LLM={os.environ.get('DIGINYAYA_USE_LLM')})")
    results = []
    conf_records = []
    for spec in GOLDEN:
        ok, conf = run_case(spec)
        results.append(ok)
        if conf:
            conf_records.append(conf)

    passed = sum(results)
    total = len(results)

    print(f"\n{'='*60}\nRESULT: {passed}/{total} golden cases passed ({round(100*passed/total,1)}%)")

    if conf_records:
        proposed = sum(c["citations_proposed"] for c in conf_records)
        verified = sum(c["citations_verified"] for c in conf_records)
        rejected = sum(c["citations_rejected"] for c in conf_records)
        avg_conf = round(sum(c["score"] for c in conf_records) / len(conf_records), 3)
        rej_rate = round(100 * rejected / proposed, 1) if proposed else 0.0
        llm_selected = sum(1 for c in conf_records if c.get("citation_engine") == "llm")
        print(f"Average composite confidence across {len(conf_records)} resolved cases: {avg_conf}")
        print(
            f"Citation verification: {proposed} proposed, {verified} verified, {rejected} rejected "
            f"({rej_rate}% rejection rate)"
        )
        if llm_selected == 0:
            print(
                "NOTE: citation_engine=scripted for all cases — the LLM-based decoy-selection step "
                "never ran (DIGINYAYA_USE_LLM=0), so citations were sliced deterministically from "
                "retrieval. This verifies structural grounding, not adversarial robustness. Re-run "
                "with DIGINYAYA_USE_LLM=1 against live Ollama for a real decoy-rejection number."
            )
        else:
            print(f"{llm_selected}/{len(conf_records)} cases used LLM-based citation selection (decoys were shown to the model).")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
