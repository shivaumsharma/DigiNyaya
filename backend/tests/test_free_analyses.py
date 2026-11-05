"""Pure-logic tests for state_category_analysis, small_claims_analysis and hybrid_analysis."""
from scripts.hybrid_analysis import rules, score
from scripts.small_claims_analysis import failure_mode, half, macro_f1
from scripts.state_category_analysis import diff_ci, standardise, state_of


def test_state_of_takes_the_last_comma_part():
    assert state_of({"source": {"court": "District Court Complex Kadapa, Kadapa, Andhra Pradesh"}}) == "Andhra Pradesh"
    assert state_of({"source": {}}) is None


def test_diff_ci_contains_zero_for_equal_rates_and_orders_correctly():
    d, lo, hi = diff_ci(50, 100, 50, 100)
    assert d == 0 and lo < 0 < hi
    d, lo, hi = diff_ci(80, 100, 50, 100)
    assert 0 < lo < d < hi


def test_standardise_renormalises_missing_categories():
    assert abs(standardise({"a": 0.5, "b": 1.0}, {"a": 0.5, "b": 0.25, "c": 0.25}) - (0.25 + 0.25) / 0.75) < 1e-9


def test_failure_modes():
    base = {"claimant_prevailed_ai": True, "claimant_prevailed_real": False, "verdict": "mismatch"}
    assert failure_mode(base).startswith("too generous")
    assert failure_mode({**base, "claimant_prevailed_ai": False, "claimant_prevailed_real": True}).startswith("wrongly dismissed")
    assert failure_mode({**base, "claimant_prevailed_real": True, "verdict": "match"}).endswith("full match")
    assert "wrong relief" in failure_mode({**base, "claimant_prevailed_real": True, "verdict": "partial"})


def test_macro_f1_and_split_are_stable():
    assert abs(macro_f1([True, True, False, False], [True, True, False, False]) - 1.0) < 1e-9
    assert half("x") == half("x") and half("x") in (0, 1)


def test_hybrid_rules_and_abstention():
    r = rules(0.9, False)  # InLegalBERT says claimant (confident), pipeline says respondent
    assert r["respondent only if BOTH say respondent"] is True   # claimant-favouring
    assert r["respondent if EITHER says respondent"] is False
    assert r["InLegalBERT when confident (<=0.2 or >=0.8), else pipeline"] is True
    assert r["decide only when they AGREE"] is None
    s = score([(True, True), (False, None), (False, False)])
    assert s["coverage"] == 2 / 3 and s["accuracy"] == 1.0
