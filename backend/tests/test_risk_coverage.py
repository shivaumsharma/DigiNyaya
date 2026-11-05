"""Tests for scripts/risk_coverage.py and the LLM call-outcome counters the live-LLM eval relies on."""
from __future__ import annotations

import random

from app import llm
from scripts import risk_coverage as rc


def _rows(n=400, seed=1, informative=True):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        score = rng.random()
        # informative selector: higher score -> more likely correct; uninformative: independent of correctness
        p_correct = 0.55 + 0.4 * score if informative else 0.75
        rows.append({"case_id": f"c{i}", "category": "x", "correct": rng.random() < p_correct,
                     "composite_confidence": rng.random(), "strength_gap": score, "real_claimant": rng.random() < 0.8})
    return rows


def test_curve_ends_at_full_coverage_with_overall_risk():
    rows = _rows()
    pts = rc.curve(rows, "strength_gap")
    assert pts[-1][0] == 1.0
    assert abs(pts[-1][1] - sum(0 if r["correct"] else 1 for r in rows) / len(rows)) < 1e-9


def test_informative_selector_beats_random_and_uninformative_does_not():
    good, bad = _rows(informative=True), _rows(informative=False)
    base_good = sum(0 if r["correct"] else 1 for r in good) / len(good)
    assert rc.aurc(good, "strength_gap") < base_good - 0.03
    base_bad = sum(0 if r["correct"] else 1 for r in bad) / len(bad)
    assert abs(rc.aurc(bad, "strength_gap") - base_bad) < 0.05


def test_ties_are_kept_together():
    rows = [{"correct": c, "s": 1.0} for c in (True, False, True, True)]
    assert rc.curve(rows, "s") == [(1.0, 0.25)]


def test_risk_at_coverage_is_lower_for_stricter_cut():
    rows = _rows()
    assert rc.risk_at(rows, "strength_gap", 0.1) < rc.risk_at(rows, "strength_gap", 1.0)


def test_guarantee_holds_on_held_out_half_for_informative_selector():
    rows = _rows(n=4000)
    s = rc.summarise_guarantee(rows, "strength_gap", alpha=0.25, delta=0.05, trials=30)
    assert s["certified_share"] > 0.5
    assert s["mean_test_risk"] <= 0.25 + 0.02


def test_guarantee_declines_when_unreachable():
    rows = _rows(n=2000, informative=False)  # ~25% error everywhere, so alpha=5% can never be certified
    s = rc.summarise_guarantee(rows, "strength_gap", alpha=0.05, delta=0.05, trials=10)
    assert s["certified_share"] == 0.0


def test_respondent_recall_guard_ignores_claimant_wins():
    rows = [{"correct": True, "real_claimant": True, "s": 1.0}, {"correct": False, "real_claimant": False, "s": 0.9},
            {"correct": True, "real_claimant": False, "s": 0.8}, {"correct": True, "real_claimant": True, "s": 0.1}]
    assert rc.respondent_recall_at(rows, "s", 0.75) == 0.5


def test_llm_call_stats_count_failures_per_thread(monkeypatch):
    monkeypatch.setenv("DIGINYAYA_USE_LLM", "1")
    from app.llm import client

    class Boom:
        def generate_json(self, **kw):
            raise RuntimeError("down")

        def generate(self, **kw):
            return "text"

        def last_usage(self):
            return None

    monkeypatch.setattr(client, "get_provider", lambda: Boom())
    monkeypatch.setattr(client._breaker, "allow", lambda: True)
    llm.reset_call_stats()
    assert llm.generate_json("p") is None
    assert llm.generate("p") == "text"
    assert llm.get_call_stats() == {"ok": 1, "failed": 1}
    llm.reset_call_stats()
    assert llm.get_call_stats() == {"ok": 0, "failed": 0}
