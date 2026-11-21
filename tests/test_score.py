"""The F1 scorer (evaluation/score.py)."""

from evaluation.score import Scorer, norm


def test_norm_numbers_and_spaces():
    assert norm(200) == norm(200.0) == "200"
    assert norm("  Dostawa   sprzętu \n") == "Dostawa sprzętu"
    assert norm("") is None
    assert norm("„Abc”, def", lenient=True) == "abc def"


def test_counts_tp_fp_fn():
    s = Scorer()
    gold = {"x": {"a": "1", "b": "2", "c": None, "d": "4"}}
    pred = {"x": {"a": "1", "b": "X", "c": "3", "d": None}}
    c = s.compare(pred, gold, "t", "case", "f.json")
    # a: TP · b: FP+FN · c: FP · d: FN
    assert (c.tp, c.fp, c.fn) == (1, 2, 2)


def test_list_alignment_ignores_order():
    s = Scorer()
    gold = {"p": [{"n": "Anna"}, {"n": "Jan"}]}
    pred = {"p": [{"n": "Jan"}, {"n": "Anna"}]}
    c = s.compare(pred, gold, "t", "case", "f.json")
    assert (c.tp, c.fp, c.fn) == (2, 0, 0)
