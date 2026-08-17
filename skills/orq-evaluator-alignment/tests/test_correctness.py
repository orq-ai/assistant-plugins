"""Ground-truth correctness against dataset labels (flow-friction §3).

Instability answers "does the judge agree with itself". When rows carry a
`reference` label the run can also answer "is it RIGHT" — the question the skill
otherwise says, correctly, that it structurally cannot answer. The label was
already being collected (`map_datapoint` routes `expected_output` → `reference`)
and then dropped, because an evaluator declaring no `{{reference}}` variable
never renders it.

`by_band.stable.accuracy` is the point of the whole block: accuracy among rows
the judge never wavered on is a direct measurement of the consistently-wrong
blind spot.

Pure stdlib; no orq/evaluatorq import.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

import metrics  # noqa: E402


def _rows(*specs):
    """(source_index, reference, aggregate_value) triples → stability rows."""
    return [{'source_index': i, 'reference': ref, 'aggregate_value': got} for i, ref, got in specs]


def _per_row(*specs):
    return [{'source_index': i, 'band': band} for i, band in specs]


def test_judge_correct_on_every_labelled_row():
    c = metrics._correctness(
        _rows((0, 'true', True), (1, 'false', False)),
        _per_row((0, 'stable'), (1, 'stable')), 'boolean', 0.5,
    )
    assert c['accuracy'] == pytest.approx(1.0)
    assert c['n_labelled'] == 2
    assert c['wrong_source_indices'] == []
    assert c['label_source'] == 'dataset_reference'


def test_stable_but_wrong_is_measured_not_hidden():
    # The blind spot, in numbers: perfect instability, 40% correct on those rows.
    c = metrics._correctness(
        _rows((0, 'true', True), (1, 'true', False), (2, 'true', False)),
        _per_row((0, 'stable'), (1, 'stable'), (2, 'stable')), 'boolean', 0.5,
    )
    assert c['by_band']['stable']['accuracy'] == pytest.approx(1 / 3)
    assert c['wrong_source_indices'] == [1, 2]


def test_by_band_separates_stable_from_unreliable():
    c = metrics._correctness(
        _rows((0, 'true', True), (1, 'true', False)),
        _per_row((0, 'stable'), (1, 'unreliable')), 'boolean', 0.5,
    )
    assert c['by_band']['stable']['accuracy'] == pytest.approx(1.0)
    assert c['by_band']['unreliable']['accuracy'] == pytest.approx(0.0)


def test_mixed_labelled_and_unlabelled_counts_only_the_labelled():
    c = metrics._correctness(
        _rows((0, 'true', True), (1, '', True), (2, None, False)),
        _per_row((0, 'stable'), (1, 'stable'), (2, 'stable')), 'boolean', 0.5,
    )
    assert c['n_labelled'] == 1
    assert c['labelled_source_indices'] == [0]


def test_unreadable_label_is_excluded_never_coerced():
    # 'maybe' is not a boolean. Counting it as a miss would report the judge wrong
    # for the dataset's sloppiness.
    c = metrics._correctness(
        _rows((0, 'true', True), (1, 'maybe', True)),
        _per_row((0, 'stable'), (1, 'stable')), 'boolean', 0.5,
    )
    assert c['n_labelled'] == 1
    assert c['n_unreadable_labels'] == 1
    assert c['accuracy'] == pytest.approx(1.0)


def test_no_labels_at_all_returns_none():
    # Absent means "not measured", never "nothing wrong found".
    assert metrics._correctness(_rows((0, '', True)), _per_row((0, 'stable')), 'boolean', 0.5) is None


def test_categorical_matches_case_insensitively():
    c = metrics._correctness(
        _rows((0, 'Safe', 'safe'), (1, 'unsafe', 'safe')),
        _per_row((0, 'stable'), (1, 'stable')), 'categorical', 0.5,
    )
    assert c['n_correct'] == 1


def test_numeric_uses_the_shared_tolerance_band():
    rows = _rows((0, 4.0, 4.4), (1, 4.0, 4.9))
    per_row = _per_row((0, 'stable'), (1, 'stable'))
    assert metrics._correctness(rows, per_row, 'number', 0.5)['n_correct'] == 1
    # A wider band from a wider declared scale changes what "correct" means.
    assert metrics._correctness(rows, per_row, 'number', 1.0)['n_correct'] == 2


def test_string_is_omitted_with_a_stated_reason():
    c = metrics._correctness(
        _rows((0, 'refund request', 'refund')), _per_row((0, 'stable')), 'string', 0.5
    )
    assert c['n_labelled'] == 0
    assert 'wording' in c['reason_omitted']


def test_confusion_records_the_direction_of_the_error():
    c = metrics._correctness(
        _rows((0, 'true', False), (1, 'true', False)),
        _per_row((0, 'stable'), (1, 'stable')), 'boolean', 0.5,
    )
    assert c['confusion'] == {'true→False': 2}


def test_report_calls_out_the_stable_row_accuracy():
    c = metrics._correctness(
        _rows((0, 'true', True), (1, 'true', False)),
        _per_row((0, 'stable'), (1, 'stable')), 'boolean', 0.5,
    )
    lines = '\n'.join(metrics._correctness_lines(c))
    assert 'correctness vs dataset labels' in lines
    assert 'blind spot, measured' in lines
    assert 'dataset_reference' in lines


def test_report_is_silent_without_labels():
    assert metrics._correctness_lines(None) == []
    assert metrics._correctness_lines({'n_labelled': 0}) == []
