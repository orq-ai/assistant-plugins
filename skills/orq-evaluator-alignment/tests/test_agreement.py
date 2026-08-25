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


# --- the numeric band has to mean the same thing on every scale ---


def test_default_tolerance_scales_with_the_declared_range():
    # The whole point: a fixed 0.5 is half of a 0-1 scale and 0.5% of a 0-100 one,
    # so the same "tolerance" waved through a rewrite that missed the human by half
    # the scale on one judge and rejected one that was within a point on the other.
    assert agreement.default_tolerance([0.0, 1.0]) == pytest.approx(0.1)
    assert agreement.default_tolerance([1.0, 10.0]) == pytest.approx(0.9)
    assert agreement.default_tolerance([0.0, 100.0]) == pytest.approx(10.0)


def test_default_tolerance_falls_back_when_no_scale_is_declared():
    # The band has to be something, and an undeclared scale is the one case where
    # there is nothing to derive it from.
    assert agreement.default_tolerance(None) == pytest.approx(agreement.FALLBACK_TOL)
    assert agreement.default_tolerance([5.0]) == pytest.approx(agreement.FALLBACK_TOL)
    assert agreement.default_tolerance([2.0, 2.0]) == pytest.approx(agreement.FALLBACK_TOL)
    assert agreement.default_tolerance(['a', 'b']) == pytest.approx(agreement.FALLBACK_TOL)


def test_scale_derived_band_reverses_both_bad_verdicts():
    # A 0-1 judge off by 0.4 on every row used to PASS at within >= 0.7; a 0-100
    # judge off by 1 used to FAIL. Both now read the way the scale says they should.
    off_by_40_percent = [(0.5, 0.9), (0.4, 0.8), (0.2, 0.6)]
    r = agreement.numeric_agreement(off_by_40_percent, tol=agreement.default_tolerance([0, 1]))
    assert r['within_tolerance_rate'] == pytest.approx(0.0)

    off_by_one_percent = [(50.0, 51.0), (40.0, 41.0), (20.0, 21.0)]
    r = agreement.numeric_agreement(off_by_one_percent, tol=agreement.default_tolerance([0, 100]))
    assert r['within_tolerance_rate'] == pytest.approx(1.0)


# --- per-point bands, because the policy requires one on every numeric label ---


def test_per_point_tolerance_is_used_where_given():
    pairs = [(3.0, 3.4), (7.0, 7.4)]
    # Second point was banded tightly by the human; the first takes the run-wide band.
    r = agreement.numeric_agreement(pairs, tol=0.5, tols=[None, 0.1])
    assert r['n_within'] == 1
    assert r['tol_source'] == 'per_point'


def test_uniform_per_point_bands_are_not_reported_as_per_point():
    r = agreement.numeric_agreement([(3.0, 3.1), (7.0, 7.1)], tol=0.5, tols=[0.5, 0.5])
    assert r['tol_source'] == 'uniform'


def test_no_per_point_bands_scores_exactly_as_before():
    pairs = [(3.0, 3.4), (7.0, 7.9)]
    assert agreement.numeric_agreement(pairs, tol=0.5) == agreement.numeric_agreement(
        pairs, tol=0.5, tols=None
    )


def test_misaligned_per_point_bands_raise():
    # Positional alignment is the contract; a silent zip() would score the wrong
    # point against the wrong band.
    with pytest.raises(ValueError):
        agreement.numeric_agreement([(1.0, 1.0), (2.0, 2.0)], tol=0.5, tols=[0.1])


def test_agreement_dispatch_forwards_per_point_bands():
    r = agreement.agreement('number', [(3.0, 3.4)], tol=0.5, tols=[0.1])
    assert r['within_tolerance_rate'] == pytest.approx(0.0)
