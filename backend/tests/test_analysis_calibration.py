"""The LLM's claimant-substance score must not move claimant strength unless a held-out result justifies it."""
from app.agents import analysis


def test_substance_has_no_effect_at_the_measured_weight():
    assert analysis._LLM_SUBSTANCE_WEIGHT == 0.0
    assert analysis._adjust_claimant(0.59, 0.0, 0.85) == 0.59
    assert analysis._adjust_claimant(0.59, 1.0, 0.85) == 0.59
    assert analysis._adjust_claimant(0.59, None, 0.85) == 0.59


def test_adjustment_is_centred_and_capped_when_enabled(monkeypatch):
    monkeypatch.setattr(analysis, "_LLM_SUBSTANCE_WEIGHT", 0.4)
    m = analysis._LLM_SUBSTANCE_MEAN
    assert analysis._adjust_claimant(0.6, m, 0.85) == 0.6          # average substance: unchanged
    assert analysis._adjust_claimant(0.6, 1.0, 0.85) > 0.6
    assert analysis._adjust_claimant(0.6, 0.0, 0.85) < 0.6
    assert analysis._adjust_claimant(0.8, 1.0, 0.85) == 0.85       # capped
    assert analysis._adjust_claimant(0.12, 0.0, 0.85) == 0.1       # floored
