"""Pure-logic tests for scripts/multilingual_roundtrip.py (translation, gateway and LLM are faked)."""
from scripts.multilingual_roundtrip import choose_cases, round_trip_case, summarise


def _case(i):
    return {"case_id": f"c{i}", "case_description": f"desc {i}", "signals": {"x": 1}, "control": {"a": 1}}


def test_choose_cases_puts_respondent_wins_first():
    cases = [_case(i) for i in range(10)]
    eng = {f"c{i}": {"claimant_prevailed_ai": True, "claimant_prevailed_real": i >= 3} for i in range(10)}
    chosen = choose_cases(cases, eng, 5, seed=1)
    assert {c["case_id"] for c in chosen} >= {"c0", "c1", "c2"}  # all 3 respondent wins are in
    assert len(chosen) == 5


def test_round_trip_replaces_description_and_signals():
    status, rt, info = round_trip_case(_case(1), "hi-IN", translate=lambda t, lg: ("नमस्ते " + t, True),
                                       inbound=lambda t, lg: ("hello " + t, True), signals_of=lambda t: {"y": 2})
    assert status == "ok" and rt["signals"] == {"y": 2} and rt["case_description"].startswith("hello ")
    assert "control" not in rt and rt["case_id"] == "c1"


def test_round_trip_reports_each_failure_stage():
    c = _case(1)
    assert round_trip_case(c, "hi-IN", translate=lambda t, lg: ("", False), inbound=None, signals_of=None)[0] == "translate_failed"
    assert round_trip_case(c, "hi-IN", translate=lambda t, lg: ("x", True), inbound=lambda t, lg: ("x", False),
                           signals_of=None)[0] == "inbound_failed"
    assert round_trip_case(c, "hi-IN", translate=lambda t, lg: ("x", True), inbound=lambda t, lg: ("y", True),
                           signals_of=lambda t: None)[0] == "signals_failed"


def test_summarise_consistency_and_mcnemar():
    def row(real, en, xx, esc=False):
        return {"status": "ok", "escalated": esc, "real_claimant": real, "english_claimant_ai": en, "claimant_ai": xx}
    rows = [row(True, True, True), row(False, False, True), row(False, False, False), row(True, True, True), row(True, True, None, esc=True)]
    s = summarise(rows)
    assert s["round_trips_ok"] == 5 and s["escalated"] == 1 and s["judged_in_both"] == 4
    assert s["winner_consistency"] == 0.75 and s["worsened"] == 1 and s["improved"] == 0
    assert s["recall_respondent_english"] == 1.0 and s["recall_respondent_language"] == 0.5
