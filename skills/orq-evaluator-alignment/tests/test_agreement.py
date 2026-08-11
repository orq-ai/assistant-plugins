"""Unit tests for lib.agreement — pure human-vs-judge agreement metrics (RES-978 §2.5).

The retest's honesty signal (b): does the *new* evaluator's verdict match the
human label on the same confuser? Boolean → TPR/TNR (+ accuracy); categorical →
exact-match accuracy; numeric → MAE + within-tolerance rate. Like lib.instability
this module is pure stdlib, so it imports safely on the Windows host where the
heavy deps abort at import time.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

from lib import agreement  # noqa: E402


# --- boolean: TPR / TNR / accuracy over a known confusion set ---


def test_boolean_known_confusion():
    # human, judge pairs. Positive class = True.
    #   TP: human True,  judge True
    #   FN: human True,  judge False
    #   TN: human False, judge False
    #   FP: human False, judge True
    pairs = [
        (True, True),    # TP
        (True, True),    # TP
        (True, False),   # FN
        (False, False),  # TN
        (False, False),  # TN
        (False, False),  # TN
        (False, True),   # FP
    ]
    r = agreement.boolean_agreement(pairs)
    # TP=2 FN=1 → TPR = 2/3
    assert r['tpr'] == pytest.approx(2 / 3)
    # TN=3 FP=1 → TNR = 3/4
    assert r['tnr'] == pytest.approx(3 / 4)
    # accuracy = (TP+TN)/N = 5/7
    assert r['accuracy'] == pytest.approx(5 / 7)
    assert r['tp'] == 2 and r['fn'] == 1 and r['tn'] == 3 and r['fp'] == 1
    assert r['n'] == 7


def test_boolean_all_agree_is_perfect():
    pairs = [(True, True), (False, False), (True, True), (False, False)]
    r = agreement.boolean_agreement(pairs)
    assert r['tpr'] == pytest.approx(1.0)
    assert r['tnr'] == pytest.approx(1.0)
    assert r['accuracy'] == pytest.approx(1.0)


def test_boolean_no_positives_tpr_none():
    # No human-positive rows → TPR is undefined (None), TNR still defined.
    pairs = [(False, False), (False, True)]
    r = agreement.boolean_agreement(pairs)
    assert r['tpr'] is None
    assert r['tnr'] == pytest.approx(0.5)


def test_boolean_no_negatives_tnr_none():
    pairs = [(True, True), (True, False)]
    r = agreement.boolean_agreement(pairs)
    assert r['tnr'] is None
    assert r['tpr'] == pytest.approx(0.5)


def test_boolean_coerces_truthy_labels():
    # Human/judge values may arrive as 'true'/'false' strings or 0/1.
    pairs = [('true', True), ('false', 0), (1, 'yes'), ('no', False)]
    r = agreement.boolean_agreement(pairs)
    assert r['accuracy'] == pytest.approx(1.0)


def test_boolean_empty_raises():
    with pytest.raises(ValueError):
        agreement.boolean_agreement([])


# --- categorical: exact-match accuracy ---


def test_categorical_accuracy():
    pairs = [
        ('safe', 'safe'),    # match
        ('abuse', 'abuse'),  # match
        ('spam', 'safe'),    # miss
        ('safe', 'safe'),    # match
    ]
    r = agreement.categorical_agreement(pairs)
    assert r['accuracy'] == pytest.approx(3 / 4)
    assert r['n_correct'] == 3
    assert r['n'] == 4


def test_categorical_all_agree_is_perfect():
    pairs = [('a', 'a'), ('b', 'b'), ('c', 'c')]
    r = agreement.categorical_agreement(pairs)
    assert r['accuracy'] == pytest.approx(1.0)


def test_categorical_normalizes_case_and_whitespace():
    # Labels compared case-insensitively after trimming.
    pairs = [('Safe', 'safe '), (' ABUSE', 'abuse')]
    r = agreement.categorical_agreement(pairs)
    assert r['accuracy'] == pytest.approx(1.0)


def test_categorical_empty_raises():
    with pytest.raises(ValueError):
        agreement.categorical_agreement([])


# --- numeric: MAE + within-tolerance rate ---


def test_numeric_mae_and_within_tol():
    pairs = [
        (3.0, 3.0),  # err 0.0  → within
        (3.0, 3.4),  # err 0.4  → within (<= 0.5)
        (3.0, 4.0),  # err 1.0  → outside
        (5.0, 4.0),  # err 1.0  → outside
    ]
    r = agreement.numeric_agreement(pairs, tol=0.5)
    # MAE = (0 + 0.4 + 1.0 + 1.0) / 4 = 0.6
    assert r['mae'] == pytest.approx(0.6)
    # within tol (<=0.5): 2 of 4
    assert r['within_tolerance_rate'] == pytest.approx(0.5)
    assert r['n_within'] == 2
    assert r['tol'] == pytest.approx(0.5)
    assert r['n'] == 4


def test_numeric_all_exact_is_perfect():
    pairs = [(1.0, 1.0), (2.0, 2.0), (5.0, 5.0)]
    r = agreement.numeric_agreement(pairs, tol=0.5)
    assert r['mae'] == pytest.approx(0.0)
    assert r['within_tolerance_rate'] == pytest.approx(1.0)


def test_numeric_boundary_is_within():
    # Exactly at tolerance counts as within (<=).
    pairs = [(3.0, 3.5)]
    r = agreement.numeric_agreement(pairs, tol=0.5)
    assert r['n_within'] == 1
    assert r['within_tolerance_rate'] == pytest.approx(1.0)


def test_numeric_default_tolerance():
    # Default tol is 0.5 on the raw scale.
    pairs = [(3.0, 3.4)]
    r = agreement.numeric_agreement(pairs)
    assert r['tol'] == pytest.approx(0.5)
    assert r['within_tolerance_rate'] == pytest.approx(1.0)


def test_numeric_coerces_string_values():
    pairs = [('3', '3.4'), ('5', '4')]
    r = agreement.numeric_agreement(pairs, tol=0.5)
    assert r['mae'] == pytest.approx((0.4 + 1.0) / 2)


def test_numeric_empty_raises():
    with pytest.raises(ValueError):
        agreement.numeric_agreement([], tol=0.5)


# --- dispatcher ---


def test_agreement_dispatch_boolean():
    pairs = [(True, True), (False, False)]
    r = agreement.agreement('boolean', pairs)
    assert r['accuracy'] == pytest.approx(1.0)
    assert 'tpr' in r and 'tnr' in r


def test_agreement_dispatch_categorical():
    pairs = [('a', 'a'), ('b', 'c')]
    r = agreement.agreement('categorical', pairs)
    assert r['accuracy'] == pytest.approx(0.5)


def test_agreement_dispatch_numeric_passes_tol():
    pairs = [(3.0, 3.4)]
    r = agreement.agreement('number', pairs, tol=0.5)
    assert r['within_tolerance_rate'] == pytest.approx(1.0)


def test_agreement_dispatch_accepts_numeric_alias():
    pairs = [(3.0, 3.0)]
    r = agreement.agreement('numeric', pairs)
    assert r['mae'] == pytest.approx(0.0)


def test_agreement_dispatch_unknown_type_raises():
    with pytest.raises(ValueError):
        agreement.agreement('freeform', [(1, 1)])
