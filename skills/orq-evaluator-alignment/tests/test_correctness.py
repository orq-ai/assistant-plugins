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
from collections import Counter
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
    # §4.1: "blind spot, measured" requires both a floor (n >= 10) and near-full
    # band coverage (n / n_band_total >= 0.9) — ten stable+labelled rows, one wrong.
    specs = [(i, 'true', i != 0) for i in range(10)]
    c = metrics._correctness(
        _rows(*specs), _per_row(*[(i, 'stable') for i in range(10)]), 'boolean', 0.5,
    )
    lines = '\n'.join(metrics._correctness_lines(c))
    assert 'correctness vs dataset labels' in lines
    assert 'blind spot, measured' in lines
    assert 'dataset_reference' in lines


def test_report_is_silent_without_labels():
    assert metrics._correctness_lines(None) == []
    assert metrics._correctness_lines({'n_labelled': 0}) == []


# ── §1.2 — reference declared as a judge input is not ground truth ───────────
# `make_replacements` (lib.judge) binds `reference` INTO the prompt when the
# evaluator declares a reference-family variable (`reference`/`expected`/
# `expected_output`). Grading the verdict against that same value afterwards is
# circular: the judge was shown the answer, not asked to find it.


def test_correctness_omitted_when_reference_declared():
    c = metrics._correctness(
        _rows((0, 'true', True), (1, 'true', True)),
        _per_row((0, 'stable'), (1, 'stable')), 'boolean', 0.5,
        variables=['log.output', 'log.reference'],
    )
    assert c['n_labelled'] == 0
    assert 'accuracy' not in c
    assert 'log.reference' in c['reason_omitted']


def test_correctness_not_omitted_when_no_reference_variable_declared():
    c = metrics._correctness(
        _rows((0, 'true', True)), _per_row((0, 'stable')), 'boolean', 0.5,
        variables=['log.output'],
    )
    assert c['n_labelled'] == 1
    assert 'accuracy' in c


# ── §1.3 — categorical reference outside the declared label set is unreadable ─
# `_reference_matches`'s own docstring says an unreadable label returns None,
# excluded from the count — not coerced into a False that reads as the judge
# being wrong. The categorical branch violated this for out-of-vocabulary
# references when `categorical_labels` was ignored.


def test_categorical_out_of_vocab_label_is_unreadable():
    assert metrics._reference_matches('pass', 'safe', 'categorical', 0.5, ['safe', 'unsafe']) is None
    # Every row's reference is out of vocabulary → no readable label anywhere,
    # so the whole correctness measurement is None — never a false accuracy=0.0.
    c = metrics._correctness(
        _rows((0, 'pass', 'safe'), (1, 'fail', 'safe')),
        _per_row((0, 'stable'), (1, 'stable')), 'categorical', 0.5,
        categorical_labels=['safe', 'unsafe'],
    )
    assert c is None


def test_categorical_reference_normalized_against_declared_labels():
    # Case/whitespace-normalized reference that IS in the label set still reads.
    assert metrics._reference_matches('Safe ', 'safe', 'categorical', 0.5, ['safe', 'unsafe']) is True


def test_categorical_out_of_vocab_counted_as_unreadable_not_wrong():
    c = metrics._correctness(
        _rows((0, 'safe', 'safe'), (1, 'pass', 'safe')),
        _per_row((0, 'stable'), (1, 'stable')), 'categorical', 0.5,
        categorical_labels=['safe', 'unsafe'],
    )
    assert c['n_labelled'] == 1
    assert c['n_unreadable_labels'] == 1
    assert c['accuracy'] == pytest.approx(1.0)


def test_categorical_empty_labels_keeps_exact_compare_behavior():
    # No declared label set: fall back to the pre-existing exact-compare rule.
    c = metrics._correctness(
        _rows((0, 'pass', 'safe')), _per_row((0, 'stable')), 'categorical', 0.5,
        categorical_labels=[],
    )
    assert c['n_labelled'] == 1
    assert c['n_unreadable_labels'] == 0
    assert c['accuracy'] == pytest.approx(0.0)


# ── §3.5 — numeric with no derivable tolerance band omits correctness ────────


def test_numeric_no_scale_omits_correctness():
    c = metrics._correctness(
        _rows((0, 0.0, 0.5), (1, 1.0, 0.5)),
        _per_row((0, 'stable'), (1, 'stable')), 'number', 0.5,
        tol_derivable=False,
    )
    assert c['n_labelled'] == 0
    assert 'accuracy' not in c
    assert 'reason_omitted' in c


def test_numeric_with_derivable_tol_computes_correctness():
    c = metrics._correctness(
        _rows((0, 0.0, 0.4)), _per_row((0, 'stable')), 'number', 0.5,
        tol_derivable=True,
    )
    assert c['n_labelled'] == 1
    assert 'accuracy' in c


# ── §4.1 — by_band coverage + floor before the "blind spot, measured" claim ──


def test_by_band_coverage_and_floor():
    # 200-row dataset, 190 stable, only 20 of them carry a label. Low coverage
    # (20/190) must not earn the "consistent-and-right, measured" rhetoric.
    per_row = _per_row(*([(i, 'stable') for i in range(190)] + [(i, 'noisy') for i in range(190, 200)]))
    rows = _rows(*[(i, 'true', True) for i in range(20)])
    c = metrics._correctness(rows, per_row, 'boolean', 0.5)
    assert c['by_band']['stable']['n_band_total'] == 190
    lines = '\n'.join(metrics._correctness_lines(c))
    assert '20 of 190' in lines
    assert 'partial view' in lines
    assert 'blind spot, measured' not in lines


def test_by_band_full_coverage_earns_the_blind_spot_claim():
    per_row = _per_row(*[(i, 'stable') for i in range(12)])
    rows = _rows(*[(i, 'true', True) for i in range(12)])
    c = metrics._correctness(rows, per_row, 'boolean', 0.5)
    assert c['by_band']['stable']['n_band_total'] == 12
    lines = '\n'.join(metrics._correctness_lines(c))
    assert 'blind spot, measured' in lines


# ── §4.2 — string instability report states its exact-match ceiling ──────────


def test_string_report_upper_bound():
    per_row = [{
        'source_index': 0, 'instability': 0.2, 'band': 'noisy', 'n_wrong_output_type': 0,
        'n_successful_repeats': 5, 'n_failed': 0, 'counts': {'a': 3, 'b': 2}, 'n_distinct': 2,
    }]
    report = metrics._report(per_row, 'string', 0.2, Counter({'noisy': 1}), 1, 1, 0, 0)
    assert 'upper bound' in report


def test_boolean_report_wording_unchanged():
    per_row = [{
        'source_index': 0, 'instability': 0.0, 'band': 'stable', 'n_wrong_output_type': 0,
        'n_successful_repeats': 5, 'n_failed': 0, 'n_true': 5, 'n_false': 0,
        'mode_value': True, 'mode_rate': 1.0, 'flip_rate': 0.0, 'pairwise_agreement': 1.0,
    }]
    report = metrics._report(per_row, 'boolean', 0.0, Counter({'stable': 1}), 1, 1, 0, 0)
    assert '0 = judge always agrees with itself' in report
    assert 'upper bound' not in report
