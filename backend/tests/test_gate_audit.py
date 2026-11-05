"""Pure-logic tests for scripts/gate_audit.py."""
from scripts.gate_audit import accuracy, corroborated, snippet, text_label


def test_text_label_is_conservative():
    assert text_label("The court dismissed the plaintiff's suit.") is False
    assert text_label("The court decreed the suit and directed payment.") is True
    assert text_label("The court partly allowed the suit and dismissed the counter-claim.") is None  # both signals: not guessed
    assert text_label("") is None


def test_accuracy_ignores_unlabelled_and_reports_respondent_recall():
    rows = [{"real": True, "pred": True}, {"real": False, "pred": False}, {"real": False, "pred": True}, {"real": None, "pred": True}]
    a = accuracy(rows)
    assert a["n"] == 3 and abs(a["accuracy"] - 2 / 3) < 1e-9 and a["respondent_recall"] == 0.5
    assert accuracy([{"real": None, "pred": True}]) == {"n": 0}


def test_corroboration_words():
    assert corroborated("an FIR was lodged and the documents were forged")
    assert corroborated("charged under section 420 IPC")
    assert not corroborated("the defendant said the sale deed was forged")


def test_snippet_centres_on_the_word():
    s = snippet("a" * 200 + " forged " + "b" * 200, "forged", width=10)
    assert "forged" in s and len(s) <= 40
