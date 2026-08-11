"""Tests for the §5.2 type-gate + capture helpers (RES-978).

Pins the three pure pieces the multi-type fetch needs, against the *live* record
shapes confirmed from real evaluators (§8.1):
  - categorical: labels live in `categorical_labels` [{value, description}], with a
    flat `categories` mirror; K = len(labels).
  - numeric: output_type is `number`, and NO scale field exists in the record — so
    scale is override-only (flags / config), absent → unmeasurable, never guessed.

All three helpers are import-safe (no evaluatorq / lib.judge), so this file is
unaffected by the pre-existing _strip_code_fences breakage.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

import fetch_evaluator as fe  # noqa: E402
from lib import orq_client  # noqa: E402


# --- extract_categorical_labels: the declared K label set ---


def test_labels_from_rich_categorical_labels():
    # Real shape: list of {value, description}; we keep the values, in order.
    data = {'categorical_labels': [
        {'value': 'value 1', 'description': 'desc 1'},
        {'value': 'value 2', 'description': 'desc 2'},
    ]}
    assert orq_client.extract_categorical_labels(data) == ['value 1', 'value 2']


def test_labels_fallback_to_flat_categories():
    assert orq_client.extract_categorical_labels({'categories': ['a', 'b', 'c']}) == ['a', 'b', 'c']


def test_labels_prefers_rich_over_flat():
    data = {'categorical_labels': [{'value': 'x'}], 'categories': ['x', 'y']}
    assert orq_client.extract_categorical_labels(data) == ['x']


def test_labels_empty_when_absent():
    assert orq_client.extract_categorical_labels({}) == []


# --- _check_output_type: accept the three types, reject the rest ---


def test_check_type_accepts_and_normalizes():
    assert fe._check_output_type('boolean') == 'boolean'
    assert fe._check_output_type('Categorical') == 'categorical'
    assert fe._check_output_type('number') == 'number'


def test_check_type_rejects_unknown():
    with pytest.raises(ValueError):
        fe._check_output_type('freeform')


# --- _resolve_scale: override-only (flags > config), both-or-neither ---


def test_scale_from_flags():
    assert fe._resolve_scale({}, 1.0, 5.0) == (1.0, 5.0)


def test_scale_from_config():
    assert fe._resolve_scale({'scale_min': 1, 'scale_max': 10}, None, None) == (1.0, 10.0)


def test_scale_flags_override_config():
    assert fe._resolve_scale({'scale_min': 1, 'scale_max': 10}, 0.0, 5.0) == (0.0, 5.0)


def test_scale_none_when_absent():
    # No scale anywhere → None (numeric rows will be unmeasurable, not an error).
    assert fe._resolve_scale({}, None, None) is None


def test_scale_half_specified_raises():
    # A half-specified scale is a user error, not a silent None.
    with pytest.raises(ValueError):
        fe._resolve_scale({}, 1.0, None)
