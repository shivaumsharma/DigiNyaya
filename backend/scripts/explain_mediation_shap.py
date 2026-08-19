"""SHAP explainability for the mediation agent's SCRIPTED (deterministic)
relief decision -- app/agents/mediation.py's own default proposal, and the
validator ceiling every engine's output (scripted or LLM) is ultimately
clamped into via `if r_strength >= c_strength: clamped_ratio = 0.0` and the
`[0, min(prec_max,1)]` band. That makes the scripted path the honest thing
to explain: it's not a hypothetical simplification, it's what actually runs
whenever DIGINYAYA_USE_LLM=0 (most eval runs this whole project) or Sarvam
is unavailable, AND it's the ceiling the LLM-influenced path can't exceed.

WHY NOT SHAP THE WHOLE LIVE PIPELINE: mediation.py isn't a single trained
model -- it's an LLM call (free text in, free text out, not a fixed feature
vector) plus deterministic rules. SHAP needs a well-defined function from a
feature vector to an output that can be called many times; the LLM call
can't be replayed that way without literally re-prompting hundreds of times
per explained instance (slow, costly, and each "feature perturbation" would
require inventing a fake case narrative to match it, which is its own
ill-posed problem). The scripted core has none of these issues -- it's a
pure, fast, already-tested function of a few real numbers.

METHOD: builds synthetic-but-grounded CaseContext instances by sampling
claimant/respondent strength from tiers analysis.py's own formula actually
produces this session (0.15-0.97, observed directly, not invented), and
precedent relief ratios from the REAL corpus (app/data/precedents.json) for
a randomly chosen registered dispute type -- not arbitrary numbers. Each
sample is run through the real, unmodified app.agents.mediation.run()
(scripted mode, no LLM call) and the resulting relief AMOUNT recorded
(claim_amount held fixed across all samples so amount is directly
proportional to the relief ratio -- explaining amount is equivalent to
explaining ratio, just in a more legible unit).

Run (from backend/): python -m scripts.explain_mediation_shap
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

os.environ["DIGINYAYA_USE_LLM"] = "0"  # scripted path only -- see module docstring

import numpy as np  # noqa: E402
import shap  # noqa: E402

from app.agents import mediation  # noqa: E402
from app.core.context import AnalysisResult, CaseContext, IngestionResult, ResearchResult, RetrievedPrecedent  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
PRECEDENTS_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "precedents.json"
OUT_PATH = _DATA_DIR / "mediation_shap_report.json"

# Directly observed across this session's real-judgment eval runs -- the
# actual discrete values analysis.py's scripted strength-scoring formula
# produces, not an invented range.
_STRENGTH_TIERS = [0.15, 0.2, 0.33, 0.46, 0.59, 0.65, 0.7, 0.75, 0.81, 0.85, 0.97]

_CLAIM_AMOUNT = 100_000.0  # fixed across all samples -- see module docstring
_FEATURE_NAMES = ["claimant_strength", "respondent_strength", "precedent_mean", "precedent_min", "precedent_max"]


def _precedent_ratios_by_type() -> dict[str, list[float]]:
    precedents = json.loads(PRECEDENTS_PATH.read_text(encoding="utf-8"))
    by_type: dict[str, list[float]] = {}
    for p in precedents:
        by_type.setdefault(p["category"], []).append(p["relief_amount_ratio"])
    return by_type


def _make_ctx(c_strength: float, r_strength: float, ratios: list[float], dispute_type: str) -> CaseContext:
    ctx = CaseContext(
        case_id="shap-synthetic",
        owner_id="shap",
        dispute_type=dispute_type,
        claimant_name="Claimant",
        respondent_name="Respondent",
        claim_amount=_CLAIM_AMOUNT,
        description="Synthetic case constructed for SHAP explanation of the scripted mediation decision.",
        respondent_submission={"statement": "The respondent disputes the claim.", "accepts_liability": False},
    )
    ctx.ingestion = IngestionResult(
        dispute_subtype="general", claim_amount=_CLAIM_AMOUNT, claim_amount_display=f"Rs. {_CLAIM_AMOUNT:,.0f}",
        evidence_count=2, respondent_responded=True, tier1_eligible=True, recommended_tier=1,
        confidence=0.8, relief_type_requested="monetary",
    )
    ctx.research = ResearchResult(
        precedents=[
            RetrievedPrecedent(
                id=f"p{i}", title="Synthetic precedent", court="Synthetic Court", year=2020,
                citation="Synthetic v. Case", summary="", principle="", outcome="", outcome_detail="",
                relief_amount_ratio=r, compliance_days=30, relevance=1.0,
            )
            for i, r in enumerate(ratios)
        ],
        corpus_size=len(ratios), coverage_score=0.8, coverage_label="strong", method="semantic",
    )
    ctx.analysis = AnalysisResult(strength_score={"claimant": c_strength, "respondent": r_strength})
    return ctx


def _sample_features(rng: random.Random, ratios_by_type: dict[str, list[float]]) -> tuple[list[float], str]:
    c = rng.choice(_STRENGTH_TIERS)
    r = rng.choice(_STRENGTH_TIERS)
    dispute_type = rng.choice(list(ratios_by_type.keys()))
    ratios = rng.choices(ratios_by_type[dispute_type], k=3)
    return [c, r, min(ratios), max(ratios), sum(ratios) / len(ratios)], dispute_type


def _predict(X: np.ndarray) -> np.ndarray:
    """X columns: [claimant_strength, respondent_strength, precedent_mean,
    precedent_min, precedent_max] -- note the reorder vs. the sampler above;
    SHAP explainers call this with whatever column order the background
    matrix was built in, which _FEATURE_NAMES/rows below keep consistent."""
    out = np.zeros(len(X))
    for i, row in enumerate(X):
        c_strength, r_strength, prec_mean, prec_min, prec_max = row
        ratios = sorted({round(float(prec_min), 4), round(float(prec_mean), 4), round(float(prec_max), 4)})
        ctx = _make_ctx(float(c_strength), float(r_strength), ratios, "consumer_dispute")
        result = mediation.run(ctx)
        out[i] = result.output.amount
    return out


def main() -> int:
    rng = random.Random(42)
    ratios_by_type = _precedent_ratios_by_type()

    n_background = 150
    rows = []
    for _ in range(n_background):
        feats, _dispute_type = _sample_features(rng, ratios_by_type)
        c, r, prec_min, prec_max, prec_mean = feats
        rows.append([c, r, prec_mean, prec_min, prec_max])
    X_background = np.array(rows)

    print(f"Built {n_background} grounded synthetic samples (real precedent ratios, "
          f"observed strength tiers). Running the scripted mediation decision on each...")
    y_background = _predict(X_background)
    print(f"Relief amount range observed: Rs. {y_background.min():,.0f} - Rs. {y_background.max():,.0f} "
          f"(claim fixed at Rs. {_CLAIM_AMOUNT:,.0f})")

    print("Computing SHAP values (permutation explainer over the scripted decision function)...")
    explainer = shap.explainers.Permutation(_predict, X_background, feature_names=_FEATURE_NAMES, seed=42)
    shap_values = explainer(X_background[:40])  # explain a subset -- each one re-evaluates _predict many times

    mean_abs = np.abs(shap_values.values).mean(axis=0)
    ranked = sorted(zip(_FEATURE_NAMES, mean_abs), key=lambda t: -t[1])

    print("\n" + "=" * 60)
    print("Global feature importance (mean |SHAP value|, in Rs. of relief amount):")
    for name, val in ranked:
        print(f"  {name:22s} {val:>10,.0f}")

    report = {
        "n_background_samples": n_background,
        "n_explained": len(shap_values.values),
        "claim_amount_fixed_at": _CLAIM_AMOUNT,
        "global_importance_mean_abs_shap": {name: round(float(val), 2) for name, val in ranked},
        "base_value": round(float(np.mean(shap_values.base_values)), 2),
        "example_explanations": [
            {
                "features": dict(zip(_FEATURE_NAMES, X_background[i].tolist())),
                "predicted_amount": round(float(y_background[i]), 2),
                "shap_values": {name: round(float(v), 2) for name, v in zip(_FEATURE_NAMES, shap_values.values[i])},
            }
            for i in range(min(10, len(shap_values.values)))
        ],
    }
    OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull report -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
