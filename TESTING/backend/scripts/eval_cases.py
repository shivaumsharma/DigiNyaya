"""Evaluation harness — golden cases.

Runs the full orchestration graph in-process (no HTTP, no LLM by default) and
asserts invariants that must hold for every case:
  • the case is classified and routed,
  • research returns a usable set of precedents,
  • the mediation quantum is clamped to a defensible band (0..claim),
  • the compliance window is one of the allowed values,
  • the resolution cites only precedents that were actually retrieved,
  • a low-confidence case escalates to Tier 2 (human sign-off).

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

ALLOWED_DAYS = {15, 21, 30, 45, 60}

GOLDEN = [
    {
        "name": "Strong, uncontested non-delivery",
        "case": {
            "case_id": "EVAL-1",
            "owner_id": "u1",
            "dispute_type": "consumer_dispute",
            "claimant": {"name": "Ananya Sharma"},
            "respondent": {"name": "QuickShop Online"},
            "claim_amount": 42999,
            "description": "Paid Rs. 42,999 for a laptop ordered online on 12/03/2024; never delivered though marked delivered. No refund despite 5 support tickets. Seeking full refund.",
            "evidence": [
                {"kind": "invoice"}, {"kind": "receipt"}, {"kind": "screenshot"},
            ],
            "respondent_submission": None,
        },
        "expect_tier": 1,
        "via_mediation": True,
    },
    {
        "name": "Contested defective product with counter-offer",
        "case": {
            "case_id": "EVAL-2",
            "owner_id": "u1",
            "dispute_type": "consumer_dispute",
            "claimant": {"name": "Rahul Verma"},
            "respondent": {"name": "GadgetWorld"},
            "claim_amount": 18000,
            "description": "Bought a defective phone for Rs. 18,000 that stopped working within warranty. Service centre delayed repair for months.",
            "evidence": [{"kind": "invoice"}, {"kind": "warranty_card"}],
            "respondent_submission": {"statement": "We offer store credit.", "accepts_liability": False, "counter_offer": 9000},
        },
        "expect_tier": 1,
        "via_mediation": False,
    },
    {
        "name": "Thin / low-confidence case -> escalation",
        "case": {
            "case_id": "EVAL-3",
            "owner_id": "u1",
            "dispute_type": "consumer_dispute",
            "claimant": {"name": "Test User"},
            "respondent": {"name": "Someone"},
            "claim_amount": 500,
            "description": "I am unhappy with my purchase.",
            "evidence": [],
            "respondent_submission": None,
        },
        "expect_tier": 2,
        "via_mediation": True,
    },
]


def drain(gen) -> list[dict]:
    return list(gen)


def check(name: str, cond: bool, msg: str) -> bool:
    mark = "PASS" if cond else "FAIL"
    print(f"   [{mark}] {msg}")
    return cond


def run_case(spec: dict) -> bool:
    print(f"\n=== {spec['name']} ===")
    ctx = CaseContext.from_case(spec["case"])
    drain(graph.run_pipeline(ctx))

    ok = True
    ok &= check("classified", ctx.ingestion is not None, "ingestion produced a classification")
    ok &= check("routed", ctx.route is not None, f"routed to Tier {ctx.tier} (expected {spec['expect_tier']})")
    ok &= check("tier", ctx.tier == spec["expect_tier"], f"tier == {spec['expect_tier']}")
    ok &= check("research", ctx.research is not None and len(ctx.research.precedents) >= 3, "research returned >= 3 precedents")
    ok &= check("analysis", ctx.analysis is not None, "analysis produced strength scores")
    ok &= check("mediation", ctx.mediation is not None, "mediation produced a proposal")

    if ctx.mediation:
        amt = ctx.mediation.amount
        ok &= check("amount_band", 0 <= amt <= ctx.claim_amount, f"relief {amt} within [0, {ctx.claim_amount}]")
        ok &= check("days", ctx.mediation.compliance_days in ALLOWED_DAYS, f"compliance {ctx.mediation.compliance_days}d is allowed")

    drain(graph.run_resolution(ctx, via_mediation=spec["via_mediation"]))
    ok &= check("resolution", ctx.resolution is not None, "resolution drafted an order")

    if ctx.resolution and ctx.research:
        retrieved = {p.id for p in ctx.research.precedents}
        cited = ctx.resolution.cited_precedents
        # cited_precedents store citation strings; verify the citation map is a subset.
        cite_map = {p.citation for p in ctx.research.precedents}
        ok &= check("citations", all(c["citation"] in cite_map for c in cited), "all citations were actually retrieved (no hallucinated refs)")
        ok &= check("relief_le_claim", ctx.resolution.relief_amount <= ctx.claim_amount, "relief <= claim amount")
        if spec["expect_tier"] == 2:
            ok &= check("signoff", ctx.resolution.requires_human_signoff, "Tier 2 order flagged for human sign-off")
    return ok


def main() -> int:
    print(f"DigiNyaya eval harness (USE_LLM={os.environ.get('DIGINYAYA_USE_LLM')})")
    results = [run_case(s) for s in GOLDEN]
    passed = sum(results)
    print(f"\n{'='*40}\nRESULT: {passed}/{len(results)} golden cases passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
