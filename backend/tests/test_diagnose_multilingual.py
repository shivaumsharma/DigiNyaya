"""Pure-logic tests for scripts/diagnose_multilingual.py."""
from scripts.diagnose_multilingual import decompose, number_recall, numbers_in, signal_changes


def test_numbers_ignore_single_digits_and_separators():
    assert numbers_in("Rs. 4,14,481 on 17-9-2008, item 2") == {"414481", "17", "2008"}


def test_number_recall():
    assert number_recall("paid 20,00,000 in 2019", "paid 2000000 in 2019") == 1.0
    assert number_recall("paid 20,00,000 in 2019", "paid money") == 0.0
    assert number_recall("no figures here", "x") is None


def test_signal_changes_compares_text_on_presence_only():
    a = {"claimant_evidence_count": 3, "respondent_defense_summary": "denied", "respondent_accepts_liability": False}
    b = {"claimant_evidence_count": 2, "respondent_defense_summary": "rejected the claim", "respondent_accepts_liability": False}
    assert signal_changes(a, b) == ["claimant_evidence_count"]
    assert signal_changes(a, None) == []


def test_decompose_splits_flips_by_stage():
    def rec(real, e0, e1, e2, t):
        w = lambda x: {"escalated": False, "winner_claimant": x}  # noqa: E731
        return {"real_claimant": real, "E0": w(e0), "E1": w(e1), "E2": w(e2), "T": {"hi-IN": w(t)}}
    recs = [rec(True, True, True, True, True), rec(False, False, True, True, True), rec(False, False, False, True, False)]
    d = decompose(recs, "hi-IN")
    assert d["n"] == 3
    assert abs(d["flip_rate"]["E0->E1"] - 1 / 3) < 1e-9 and abs(d["flip_rate"]["E1->E2"] - 1 / 3) < 1e-9
    assert d["flip_rate"]["E1->T"] == 0.0


def test_aggregate_signals_takes_median_and_majority():
    from scripts.diagnose_multilingual import aggregate_signals
    s = [{"claimant_evidence_count": 2, "respondent_ground_has_specific_support": True, "respondent_accepts_liability": False,
          "respondent_defense_summary": "denied"},
         {"claimant_evidence_count": 5, "respondent_ground_has_specific_support": False, "respondent_accepts_liability": False,
          "respondent_defense_summary": "denied"},
         {"claimant_evidence_count": 3, "respondent_ground_has_specific_support": True, "respondent_accepts_liability": True,
          "respondent_defense_summary": ""}, None]
    a = aggregate_signals(s)
    assert a["claimant_evidence_count"] == 3 and a["respondent_ground_has_specific_support"] is True
    assert a["respondent_accepts_liability"] is False and a["respondent_defense_summary"] == "denied"
    assert aggregate_signals([None]) is None
