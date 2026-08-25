"""Unit tests for recommend.py's pure per-type disagreement extraction (RES-978 Part 2 §2.2).

Workstream D makes recommend/aggregate type-aware: given a metrics `per_row`
entry and its human `annotations.json` label, produce the right typed
disagreement record — boolean flip, categorical confusion-pair, or numeric
signed-error — or None when the human and judge agree. These are pure functions
(no backend, no network, no evaluatorq/orq import), so they are safe to import
directly on the Windows host.

Run:
    cd skills/orq-evaluator-alignment
    "C:/Users/Chiel/anaconda3/Scripts/uv.exe" run --with pytest python -m pytest tests/test_recommend.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

import recommend  # noqa: E402


# --- judge central verdict per type -----------------------------------------


def test_judge_central_boolean_uses_mode_value():
    row = {'n_true': 3, 'n_false': 2, 'mode_value': True}
    assert recommend._judge_central(row, 'boolean') is True


def test_judge_central_boolean_from_counts_when_no_mode():
    # mode_value can be None on a single-repeat row; fall back to n_true/n_false.
    row = {'n_true': 4, 'n_false': 1, 'mode_value': None}
    assert recommend._judge_central(row, 'boolean') is True
    row2 = {'n_true': 1, 'n_false': 4, 'mode_value': None}
    assert recommend._judge_central(row2, 'boolean') is False


def test_judge_central_categorical_is_argmax_of_counts():
    row = {'counts': {'abuse': 3, 'safe': 2}, 'k': 3}
    assert recommend._judge_central(row, 'categorical') == 'abuse'


def test_judge_central_numeric_is_mean():
    row = {'mean': 3.0, 'stdev': 0.8, 'scale': [1, 5]}
    assert recommend._judge_central(row, 'number') == pytest.approx(3.0)


# --- boolean → flip ----------------------------------------------------------


def test_boolean_disagreement_is_a_flip():
    row = {'source_index': 0, 'n_true': 3, 'n_false': 2, 'mode_value': True}
    ann = {'value': False, 'reason': 'benign disclosure'}
    d = recommend._disagreement(row, ann, 'boolean', labels=None)
    assert d is not None
    assert d['type'] == 'boolean'
    assert d['kind'] == 'flip'
    assert d['judge_value'] is True
    assert d['human_value'] is False
    assert d['reason'] == 'benign disclosure'
    assert d['source_index'] == 0


def test_boolean_agreement_is_none():
    row = {'source_index': 1, 'n_true': 5, 'n_false': 0, 'mode_value': True}
    ann = {'value': True, 'reason': 'clearly fine'}
    assert recommend._disagreement(row, ann, 'boolean', labels=None) is None


def test_boolean_coerces_stringified_human_value():
    # UI may persist "true"/"false" strings; a flip must not be spuriously flagged.
    row = {'source_index': 2, 'n_true': 5, 'n_false': 0, 'mode_value': True}
    ann = {'value': 'true', 'reason': ''}
    assert recommend._disagreement(row, ann, 'boolean', labels=None) is None


# --- categorical → confusion pair -------------------------------------------


def test_categorical_disagreement_is_a_confusion_pair():
    row = {'source_index': 3, 'counts': {'abuse': 3, 'safe': 2}, 'k': 3}
    ann = {'value': 'safe', 'reason': 'no slur, just venting'}
    d = recommend._disagreement(row, ann, 'categorical', labels=['safe', 'abuse', 'spam'])
    assert d is not None
    assert d['type'] == 'categorical'
    assert d['kind'] == 'confusion_pair'
    assert d['judge_value'] == 'abuse'
    assert d['human_value'] == 'safe'
    assert d['confusion_pair'] == ('abuse', 'safe')  # (judge, human)
    assert d['reason'] == 'no slur, just venting'


def test_categorical_agreement_is_none():
    row = {'source_index': 4, 'counts': {'safe': 4, 'abuse': 1}, 'k': 3}
    ann = {'value': 'safe', 'reason': 'fine'}
    assert recommend._disagreement(row, ann, 'categorical', labels=['safe', 'abuse', 'spam']) is None


# --- numeric → signed error + magnitude -------------------------------------


def test_numeric_disagreement_is_signed_error_over():
    # Judge central 4.0, human 2.0 → judge over-scored by +2.0.
    row = {'source_index': 5, 'mean': 4.0, 'stdev': 0.5, 'scale': [1, 5]}
    ann = {'value': 2.0, 'reason': 'this is clearly unsafe'}
    d = recommend._disagreement(row, ann, 'number', labels=None)
    assert d is not None
    assert d['type'] == 'number'
    assert d['kind'] == 'signed_error'
    assert d['judge_value'] == pytest.approx(4.0)
    assert d['human_value'] == pytest.approx(2.0)
    assert d['signed_error'] == pytest.approx(2.0)   # judge − human
    assert d['magnitude'] == pytest.approx(2.0)
    assert d['direction'] == 'over'


def test_numeric_disagreement_is_signed_error_under():
    row = {'source_index': 6, 'mean': 2.0, 'stdev': 0.5, 'scale': [1, 5]}
    ann = {'value': 5.0, 'reason': 'perfectly safe'}
    d = recommend._disagreement(row, ann, 'number', labels=None)
    assert d is not None
    assert d['signed_error'] == pytest.approx(-3.0)
    assert d['magnitude'] == pytest.approx(3.0)
    assert d['direction'] == 'under'


def test_numeric_within_tolerance_is_agreement_none():
    # A tiny gap under tolerance is agreement, not a disagreement record.
    row = {'source_index': 7, 'mean': 3.1, 'stdev': 0.2, 'scale': [1, 5]}
    ann = {'value': 3.0, 'reason': ''}
    assert recommend._disagreement(row, ann, 'number', labels=None, tolerance=0.5) is None


def test_numeric_exact_match_is_none():
    row = {'source_index': 8, 'mean': 3.0, 'stdev': 0.0, 'scale': [1, 5]}
    ann = {'value': 3.0, 'reason': ''}
    assert recommend._disagreement(row, ann, 'number', labels=None) is None


# --- reason fallback: UI may key the note as `explanation` -------------------


def test_reason_falls_back_to_explanation_key():
    row = {'source_index': 9, 'n_true': 3, 'n_false': 2, 'mode_value': True}
    ann = {'value': False, 'explanation': 'legacy key'}
    d = recommend._disagreement(row, ann, 'boolean', labels=None)
    assert d['reason'] == 'legacy key'
