"""Unit tests for lib.cross_model — per-datapoint cross-model disagreement (RES-980 §11.3 opt 4).

When a single judge is unanimous everywhere (flat instability), a second model's
different verdicts surface the grey zones the first hides. Disagreement is
type-native: boolean/categorical/string differ on value; numeric on |Δ| > tol.

Pure stdlib — safe to import directly on Windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

from lib import cross_model  # noqa: E402


def test_disagrees_boolean():
    assert cross_model.disagrees('boolean', True, False) is True
    assert cross_model.disagrees('boolean', True, True) is False


def test_disagrees_categorical_casefold():
    assert cross_model.disagrees('categorical', 'Abuse', 'abuse') is False
    assert cross_model.disagrees('categorical', 'abuse', 'safe') is True


def test_disagrees_numeric_within_tol():
    assert cross_model.disagrees('number', 3.0, 3.4, tol=0.5) is False
    assert cross_model.disagrees('number', 3.0, 4.0, tol=0.5) is True


def test_disagrees_string_canonical_exact():
    assert cross_model.disagrees('string', 'Hello  World', 'hello world') is False
    assert cross_model.disagrees('string', 'a', 'b') is True


def test_cross_model_rows_flags_disagreements_and_skips_none():
    a = {0: True, 1: False, 2: None}
    b = {0: False, 1: False, 2: True}
    rows = cross_model.cross_model_rows('boolean', a, b)
    by = {r['source_index']: r for r in rows}
    assert set(by) == {0, 1}  # idx 2 skipped — model A had no usable verdict
    assert by[0]['disagree'] is True
    assert by[1]['disagree'] is False
    assert by[0]['model_a'] is True and by[0]['model_b'] is False


def test_disagreeing_indices_returns_only_disagreers():
    a = {0: 'abuse', 1: 'safe', 2: 'spam'}
    b = {0: 'safe', 1: 'safe', 2: 'abuse'}
    assert cross_model.disagreeing_indices('categorical', a, b) == [0, 2]
