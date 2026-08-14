"""Unit tests for retest.py's label-source adapter (RES-980).

The grey-zone flow makes grey_zone_policy.json the default source of human labels;
annotations.json stays as the (interactive) UI-fallback source. These pin that
precedence and the numeric-tolerance resolution without needing a full retest run.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

import retest  # noqa: E402


def _write(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj), encoding='utf-8')


def _boolean_policy() -> dict:
    return {
        'output_type': 'boolean',
        'verdict_space': {'type': 'boolean', 'labels': [False, True]},
        'grey_zones': [],
        'labels': [{'source_index': 7, 'value': True, 'grey_zone_id': None}],
    }


def test_load_labels_prefers_grey_zone_policy(tmp_path):
    _write(tmp_path / 'annotations.json', {'7': {'value': False, 'reason': ''}})
    _write(tmp_path / 'grey_zone_policy.json', _boolean_policy())
    # Make the policy unambiguously the newer of the two.
    os.utime(tmp_path / 'grey_zone_policy.json', (time.time() + 10, time.time() + 10))

    labels, source, policy = retest._load_labels(tmp_path)

    assert source == 'grey_zone_policy'
    assert labels == {'7': {'value': True}}
    assert policy is not None


def test_load_labels_uses_the_newer_artifact_when_both_exist(tmp_path):
    # The documented fallback runs grey zone -> "these don't group" -> annotation UI,
    # so a fixed grey-zone-first preference threw away the labels the user had just
    # finished collecting, silently.
    _write(tmp_path / 'grey_zone_policy.json', _boolean_policy())
    _write(tmp_path / 'annotations.json', {'7': {'value': False, 'reason': 'by hand'}})
    os.utime(tmp_path / 'annotations.json', (time.time() + 10, time.time() + 10))

    labels, source, policy = retest._load_labels(tmp_path)

    assert source == 'annotations'
    assert labels['7']['value'] is False
    assert policy is None


def test_load_labels_falls_back_to_annotations(tmp_path):
    _write(tmp_path / 'annotations.json', {'7': {'value': True, 'reason': 'why'}})

    labels, source, policy = retest._load_labels(tmp_path)

    assert source == 'annotations'
    assert labels['7']['value'] is True
    assert policy is None


def test_resolve_numeric_tol_uses_uniform_policy_band():
    policy = {
        'output_type': 'number',
        'labels': [
            {'source_index': 3, 'value': 4.0, 'tolerance': 1.0},
            {'source_index': 4, 'value': 2.0, 'tolerance': 1.0},
        ],
    }
    assert retest._resolve_numeric_tol(None, {}, policy) == 1.0


def test_resolve_numeric_tol_cli_override_wins():
    policy = {'output_type': 'number', 'labels': [{'source_index': 3, 'value': 4.0, 'tolerance': 1.0}]}
    assert retest._resolve_numeric_tol(0.25, {'numeric_tol': 2.0}, policy) == 0.25


# --- the retest set is scoped to the labelled rows ---


def _seed_retest_inputs(tmp_path: Path, n_rows: int = 24) -> None:
    """A run dir shaped like a finished alignment: N traces, a new evaluator."""
    _write(tmp_path / 'evaluator.json', {
        'id': 'src', 'prompt': 'Judge: {{log.output}}', 'judge_model': 'm',
        'output_type': 'boolean', 'categorical_labels': [], 'scale': None, 'variables': [],
    })
    _write(tmp_path / 'new_evaluator.json', {
        'id': 'new', 'key': 'k-aligned', 'prompt': 'Judge better: {{log.output}}',
        'judge_model': 'm', 'output_type': 'boolean', 'categorical_labels': [], 'scale': None,
    })
    (tmp_path / 'traces.jsonl').write_text(
        '\n'.join(json.dumps({'query': f'q{i}', 'output': f'o{i}'}) for i in range(n_rows)),
        encoding='utf-8',
    )


def _retest_dir(tmp_path: Path, wanted, num_samples=None):
    """`_select_rows` + `_materialize_subrun`, the way `main` composes them."""
    selected = retest._select_rows(tmp_path, wanted, num_samples)
    evaluator = json.loads((tmp_path / 'evaluator.json').read_text(encoding='utf-8'))
    return retest._materialize_subrun(tmp_path, 'retest', evaluator, selected)


def test_retest_dir_holds_only_the_labelled_rows(tmp_path):
    # The bug: all 24 rows were re-judged (24 x N calls) while agreement scored
    # only the labelled 3 — triple the quoted cost for verdicts nothing read.
    _seed_retest_inputs(tmp_path, n_rows=24)

    retest_dir, _index_map = _retest_dir(tmp_path, {2, 7, 19})

    rows = [json.loads(line) for line in (retest_dir / 'traces.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 3
    assert [r['query'] for r in rows] == ['q2', 'q7', 'q19']


def test_index_map_translates_back_to_the_original_indices(tmp_path):
    # source_index is positional, so filtering renumbers it; the labels are keyed
    # by the ORIGINAL index and pairing would silently mis-align without this.
    _seed_retest_inputs(tmp_path, n_rows=24)

    _dir, index_map = _retest_dir(tmp_path, {2, 7, 19})

    assert index_map == {0: 2, 1: 7, 2: 19}


def test_all_rows_keeps_every_row_and_an_identity_map(tmp_path):
    _seed_retest_inputs(tmp_path, n_rows=5)

    retest_dir, index_map = _retest_dir(tmp_path, None)

    rows = (retest_dir / 'traces.jsonl').read_text(encoding='utf-8').strip().splitlines()
    assert len(rows) == 5
    assert index_map == {i: i for i in range(5)}


def test_labels_pointing_at_a_different_run_fail_loudly(tmp_path):
    _seed_retest_inputs(tmp_path, n_rows=3)

    with pytest.raises(SystemExit, match='None of the labelled rows'):
        _retest_dir(tmp_path, {90, 91})


def test_num_samples_narrows_the_scope_not_just_the_run(tmp_path):
    # --num_samples used to cap only the retest stability run, so the "before" mean
    # covered every labelled row while the "after" mean covered the first few — the
    # subset-vs-full-run artifact the rest of this file exists to avoid.
    _seed_retest_inputs(tmp_path, n_rows=24)

    _dir, index_map = _retest_dir(tmp_path, {2, 7, 19}, num_samples=2)

    assert index_map == {0: 2, 1: 7}


def test_fingerprint_mismatch_refuses_to_retest(tmp_path):
    # Labels are keyed by position in traces.jsonl; fetch_traces rewrites that file.
    # Partial overlap used to pair each label against a different datapoint and
    # report a clean-looking score for a comparison that never happened.
    _seed_retest_inputs(tmp_path, n_rows=3)
    metrics = {'metadata': {'traces_fingerprint': '3:deadbeefdeadbeef'}}

    with pytest.raises(SystemExit, match='traces.jsonl has changed'):
        retest._check_fingerprint(tmp_path, metrics)


def test_fingerprint_match_passes(tmp_path):
    from lib.content import traces_fingerprint

    _seed_retest_inputs(tmp_path, n_rows=3)
    rows = [json.loads(line) for line in (tmp_path / 'traces.jsonl').read_text(encoding='utf-8').splitlines()]
    retest._check_fingerprint(tmp_path, {'metadata': {'traces_fingerprint': traces_fingerprint(rows)}})


def test_a_run_dir_without_a_fingerprint_is_not_blocked(tmp_path):
    _seed_retest_inputs(tmp_path, n_rows=3)
    retest._check_fingerprint(tmp_path, {'metadata': {}})


# --- the before/after comparison must cover the same rows ---


def test_original_mean_is_recomputed_over_the_retested_rows():
    # Comparing a 3-row retest mean against the full 24-row original mean would
    # make the "drop" an artifact of which rows were picked.
    metrics = {
        'scores': {'mean_instability': 0.5},
        'per_row': [
            {'source_index': 0, 'instability': 0.9},
            {'source_index': 1, 'instability': 0.1},
            {'source_index': 2, 'instability': 0.2},
        ],
    }
    mean, scoped = retest._mean_instability_over(metrics, {1, 2})
    assert mean == pytest.approx(0.15)
    assert scoped is True


def test_original_mean_falls_back_to_the_run_wide_score():
    # The fallback changes the comparison basis, so it reports that it did rather
    # than presenting a subset-vs-full-run delta as like-for-like.
    metrics = {'scores': {'mean_instability': 0.42}, 'per_row': []}
    mean, scoped = retest._mean_instability_over(metrics, {1, 2})
    assert mean == pytest.approx(0.42)
    assert scoped is False


def test_original_mean_is_none_when_nothing_is_available():
    assert retest._mean_instability_over({'per_row': []}, {1}) == (None, False)


# --- the success gate itself ---


def _gate(dropped: bool, agreement_passed: bool, comparable: bool = True) -> bool:
    """The one line the whole skill turns on, mirrored from retest.main."""
    return bool(dropped and agreement_passed and comparable)


def test_success_requires_both_signals():
    # This gate IS the answer to the stable-but-wrong blind spot, and nothing read
    # it: flipping the `and` to an `or` left the whole suite green.
    assert _gate(True, True) is True
    assert _gate(True, False) is False, 'steadier but disagreeing with the human is not a win'
    assert _gate(False, True) is False, 'agreeing while still wobbling is not a win'
    assert _gate(False, False) is False


def test_success_requires_a_comparable_measurement():
    # Fewer repeats than the original run under-estimate instability, so a "drop"
    # can be the sample size alone.
    assert _gate(True, True, comparable=False) is False


def test_caveats_are_always_stated():
    # Including on the good runs — especially then.
    caveats = retest._caveats(has_baseline=True, provenance={'derived': 0, 'human_confirmed': 4}, regression={})
    assert any('holdout' in c for c in caveats)


def test_caveats_name_the_selection_bias_without_a_baseline_rerun():
    caveats = retest._caveats(has_baseline=False, provenance=None, regression=None)
    assert any('--baseline_rerun' in c for c in caveats)
    assert any('--with_low_flip' in c for c in caveats)


def test_caveats_report_derived_labels():
    caveats = retest._caveats(
        has_baseline=True, provenance={'derived': 3, 'human_confirmed': 1}, regression={}
    )
    assert any('derived by' in c for c in caveats)


# --- the regression check on rows the old judge never wavered on ---


def test_regression_report_counts_changed_verdicts():
    report = retest._regression_report({1: True, 2: True, 3: False}, {1: True, 2: False}, {1, 2, 3})
    assert report['n_compared'] == 2  # row 3 was not re-judged
    assert report['n_changed'] == 1
    assert report['changed_source_indices'] == [2]


def test_regression_report_is_none_without_overlap():
    assert retest._regression_report({1: True}, {2: True}, {1, 2}) is None


def test_low_flip_indices_come_from_the_queue(tmp_path):
    _write(tmp_path / 'queue.json', {'items': [
        {'source_index': 4, 'low_flip_sample': False},
        {'source_index': 9, 'low_flip_sample': True},
        {'source_index': 11, 'low_flip_sample': True},
    ]})
    assert retest._low_flip_indices(tmp_path) == {9, 11}
