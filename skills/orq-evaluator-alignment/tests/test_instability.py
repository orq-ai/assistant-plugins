"""Unit tests for lib.instability — the pure per-row instability formulas (RES-978 §4).

These are the ticket's worked examples turned into tests, plus the §9 edges. The
module is pure stdlib (no evaluatorq/orq import), so it is safe to import directly
even on the Windows host where those pull-ins can abort the process.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

from lib import instability  # noqa: E402


# --- boolean: 2·minority/N (RES-978 redefinition, §2) ---


def test_boolean_7t_3f_is_0_6():
    # Ticket worked example: 10 runs, 7T/3F → 2·3/10 = 0.6.
    assert instability.boolean([True] * 7 + [False] * 3) == pytest.approx(0.6)


def test_boolean_even_split_is_1():
    # 50/50 is maximal instability.
    assert instability.boolean([True] * 5 + [False] * 5) == pytest.approx(1.0)


def test_boolean_unanimous_is_0():
    assert instability.boolean([True] * 8) == pytest.approx(0.0)


def test_boolean_empty_raises():
    with pytest.raises(ValueError):
        instability.boolean([])


# --- categorical: normalized entropy H/ln(k) ---


def test_categorical_6_2_2_k3_is_0_86():
    # Ticket worked example: counts {6,2,2}, N=10, K=3 → ≈0.86.
    labels = ['a'] * 6 + ['b'] * 2 + ['c'] * 2
    assert instability.categorical(labels, 3) == pytest.approx(0.865, abs=0.005)


def test_categorical_unanimous_is_0():
    assert instability.categorical(['x'] * 10, 3) == pytest.approx(0.0)


def test_categorical_k1_is_0():
    # K=1 → ln(1)=0 denominator → defined as 0.0 (no possible disagreement).
    assert instability.categorical(['x'] * 5, 1) == 0.0


def test_categorical_even_scatter_is_1():
    # Each of the K declared labels equally represented → max entropy → 1.0.
    assert instability.categorical(['a', 'b', 'c'] * 3, 3) == pytest.approx(1.0)


def test_categorical_uses_declared_k_not_observed():
    # Only 2 labels observed but K=4 declared → denominator ln(4), not ln(2).
    # H = ln 2 = 0.6931; / ln 4 = 1.3863 → 0.5.
    assert instability.categorical(['a'] * 5 + ['b'] * 5, 4) == pytest.approx(0.5, abs=0.005)


def test_categorical_empty_raises():
    with pytest.raises(ValueError):
        instability.categorical([], 3)


def test_categorical_off_contract_labels_clamp_to_1():
    # Judge emits 3 distinct labels but only k=2 were declared: H = ln 3 > ln 2,
    # so the raw ratio is ~1.58. Must saturate at 1.0, never exceed the 0..1 scale.
    assert instability.categorical(['a', 'b', 'c'], 2) == pytest.approx(1.0)


# --- numeric: population stdev / (scale_max − scale_min) ---


def test_numeric_stdev_1_5_on_1_10_is_0_17():
    # Ticket worked example: pstdev 1.5 on a 1–10 scale → 1.5/9 ≈ 0.17.
    # [4, 7] has population stdev exactly 1.5.
    assert instability.numeric([4.0, 7.0], 1.0, 10.0) == pytest.approx(1.5 / 9)


def test_numeric_single_value_is_0():
    assert instability.numeric([5.0], 1.0, 10.0) == pytest.approx(0.0)


def test_numeric_all_equal_is_0():
    assert instability.numeric([3.0, 3.0, 3.0], 0.0, 10.0) == pytest.approx(0.0)


def test_numeric_zero_scale_range_raises():
    with pytest.raises(ValueError):
        instability.numeric([1.0, 2.0], 5.0, 5.0)


def test_numeric_empty_raises():
    with pytest.raises(ValueError):
        instability.numeric([], 1.0, 10.0)


def test_numeric_out_of_scale_spread_clamps_to_1():
    # Values far outside the declared [1, 10] scale make stdev exceed the range;
    # the raw ratio would be > 1. Must saturate at 1.0.
    assert instability.numeric([-50.0, 50.0], 1.0, 10.0) == pytest.approx(1.0)


# --- string: exact-match normalized entropy H/ln(N) ---


def test_string_unanimous_is_0():
    assert instability.string(['refund'] * 8) == pytest.approx(0.0)


def test_string_single_value_is_0():
    # N=1 → ln(1)=0 denominator → defined as 0.0 (no possible disagreement).
    assert instability.string(['refund']) == pytest.approx(0.0)


def test_string_all_distinct_is_1():
    # Every repetition differs → max entropy for N draws → 1.0.
    assert instability.string(['a', 'b', 'c', 'd']) == pytest.approx(1.0)


def test_string_6_2_2_over_10_uses_lnN_denominator():
    # counts {6,2,2}, N=10 → H≈0.9503; denominator ln(10)≈2.3026 → ≈0.413.
    values = ['a'] * 6 + ['b'] * 2 + ['c'] * 2
    assert instability.string(values) == pytest.approx(0.413, abs=0.005)


def test_string_empty_raises():
    with pytest.raises(ValueError):
        instability.string([])


# --- classify bands: <0.1 stable | 0.1–0.3 noisy | >0.3 unreliable ---


def test_classify_stable():
    assert instability.classify(0.05) == 'stable'


def test_classify_noisy_lower_boundary():
    assert instability.classify(0.1) == 'noisy'


def test_classify_noisy_upper_boundary():
    assert instability.classify(0.3) == 'noisy'


def test_classify_unreliable():
    assert instability.classify(0.301) == 'unreliable'


# --- row_instability dispatcher ---


def test_row_instability_boolean():
    assert instability.row_instability('boolean', [True] * 7 + [False] * 3) == pytest.approx(0.6)


def test_row_instability_categorical():
    labels = ['a'] * 6 + ['b'] * 2 + ['c'] * 2
    assert instability.row_instability('categorical', labels, k=3) == pytest.approx(0.865, abs=0.005)


def test_row_instability_categorical_missing_k_raises():
    # Fail loud on missing k — it is always derivable from the declared labels.
    with pytest.raises(ValueError):
        instability.row_instability('categorical', ['a', 'b'])


def test_row_instability_numeric_with_scale():
    assert instability.row_instability('number', [4.0, 7.0], scale=(1.0, 10.0)) == pytest.approx(1.5 / 9)


def test_row_instability_numeric_no_scale_is_unmeasurable():
    # No scale → unmeasurable (None), not an error (§4a).
    assert instability.row_instability('number', [4.0, 7.0]) is None


def test_row_instability_accepts_numeric_alias():
    # Guard the open output_type spelling question (§8.1): accept 'numeric' too.
    assert instability.row_instability('numeric', [4.0, 7.0], scale=(1.0, 10.0)) == pytest.approx(1.5 / 9)


def test_row_instability_string():
    # No k / scale needed — denominator is ln(N).
    values = ['a'] * 6 + ['b'] * 2 + ['c'] * 2
    assert instability.row_instability('string', values) == pytest.approx(0.413, abs=0.005)


def test_row_instability_unknown_type_raises():
    with pytest.raises(ValueError):
        instability.row_instability('freeform', [1, 2, 3])
