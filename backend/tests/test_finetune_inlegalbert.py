"""Pure-logic tests for scripts/finetune_inlegalbert.py (no model, no GPU)."""
from scripts.finetune_inlegalbert import bucket, make_split, metrics


def _cases(n=500):
    return [{"case_id": f"c{i}", "case_description": "x"} for i in range(n)]


def test_control_cases_are_never_trained_on():
    cases = _cases()
    control = {"c3", "c77", "c400"}
    train, test = make_split(cases, control, 0.15, 7)
    assert control.isdisjoint({c["case_id"] for c in train})
    assert control <= {c["case_id"] for c in test}
    assert len(train) + len(test) == len(cases)


def test_split_is_deterministic_and_roughly_the_requested_size():
    a = make_split(_cases(), set(), 0.15, 7)
    b = make_split(_cases(), set(), 0.15, 7)
    assert [c["case_id"] for c in a[1]] == [c["case_id"] for c in b[1]]
    assert 0.08 < len(a[1]) / 500 < 0.22
    assert 0.0 <= bucket(7, "c1") < 1.0


def test_metrics_known_values():
    m = metrics([1, 1, 1, 0], [1, 1, 0, 0])
    assert m["accuracy"] == 0.75 and m["recall_claimant"] == 2 / 3 and m["recall_respondent"] == 1.0
    assert m["majority_rate"] == 0.75
