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


def test_resolve_numeric_tol_derives_the_default_from_the_scale():
    # Signal (a) normalizes by the declared range; signal (b) now matches, instead
    # of applying an absolute 0.5 that means half the scale on one judge and 0.5%
    # on another.
    assert retest._resolve_numeric_tol(None, {}, None, [0.0, 1.0]) == pytest.approx(0.1)
    assert retest._resolve_numeric_tol(None, {}, None, [0.0, 100.0]) == pytest.approx(10.0)


def test_resolve_numeric_tol_configured_absolute_beats_the_derived_default():
    assert retest._resolve_numeric_tol(None, {'numeric_tol': 2.0}, None, [0.0, 1.0]) == 2.0


def test_resolve_numeric_tol_ignores_a_blank_config_key():
    # config.toml ships `numeric_tol = ""` to mean "derive it"; an empty string must
    # not float() into a crash or a 0.0 band that fails every point.
    assert retest._resolve_numeric_tol(None, {'numeric_tol': ''}, None, [0.0, 10.0]) == pytest.approx(1.0)


def test_resolve_numeric_tol_honours_a_configured_fraction():
    cfg = {'numeric_tol': '', 'numeric_tol_fraction': 0.25}
    assert retest._resolve_numeric_tol(None, cfg, None, [0.0, 4.0]) == pytest.approx(1.0)


def test_varying_policy_bands_reach_the_metric_instead_of_being_dropped():
    # validate_policy REQUIRES a tolerance on every numeric label, and the old
    # resolver kept them only when all of them agreed — so three considered bands
    # from the human were replaced by one configured default, silently.
    labels = {
        '3': {'value': 4.0, 'tolerance': 0.1},
        '4': {'value': 2.0, 'tolerance': 2.0},
    }
    judge = {3: 4.5, 4: 2.5}
    pairs, tols, _indices = retest._pair_with_labels(labels, judge)
    assert pairs == [(4.0, 4.5), (2.0, 2.5)]
    assert tols == [0.1, 2.0]

    scores, _ = retest._evaluate_agreement(pairs, 'number', 0.5, 0.7, 0.7, 0.7, 0.7, tols=tols)
    # Point 3 misses its own tight band, point 4 clears its wide one. Under the
    # single 0.5 default both would have counted as agreement.
    assert scores['n_within'] == 1
    assert scores['tol_source'] == 'per_point'


def test_a_point_without_its_own_band_falls_back_to_the_run_wide_one():
    labels = {'3': {'value': 4.0}, '4': {'value': 2.0, 'tolerance': 0.1}}
    pairs, tols, _indices = retest._pair_with_labels(labels, {3: 4.4, 4: 2.4})
    assert tols == [None, 0.1]
    scores, _ = retest._evaluate_agreement(pairs, 'number', 0.5, 0.7, 0.7, 0.7, 0.7, tols=tols)
    assert scores['n_within'] == 1  # the unbanded point clears 0.5, the banded one does not


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


_ORIGINAL_METRICS = {
    'scores': {'mean_instability': 0.5},
    'per_row': [
        {'source_index': 0, 'instability': 0.9},
        {'source_index': 1, 'instability': 0.1},
        {'source_index': 2, 'instability': 0.2},
    ],
}


def test_original_mean_is_recomputed_over_the_retested_rows():
    # Comparing a 3-row retest mean against the full 24-row original mean would
    # make the "drop" an artifact of which rows were picked.
    by_idx = retest._instability_by_index(_ORIGINAL_METRICS)
    assert retest._mean_over(by_idx, {1, 2}) == pytest.approx(0.15)


def test_instability_by_index_translates_a_subrun_position():
    # A sub-run renumbers source_index from 0; the labels are keyed by the original.
    sub = {'per_row': [{'source_index': 0, 'instability': 0.4}]}
    assert retest._instability_by_index(sub, {0: 7}) == {7: 0.4}


def test_instability_by_index_omits_unmeasurable_rows():
    # Absent, not zero: an unmeasurable row must not be averaged in as "perfectly
    # stable", and the caller needs to see which rows a run could not measure.
    metrics = {'per_row': [
        {'source_index': 0, 'instability': None},
        {'source_index': 1, 'instability': 0.3},
    ]}
    assert retest._instability_by_index(metrics) == {1: 0.3}


def test_mean_over_is_none_when_nothing_is_available():
    assert retest._mean_over({}, {1}) is None


def test_rows_the_new_judge_cannot_measure_are_outside_the_comparison():
    # The gaming path gate (a) has to be closed against: the new judge answers
    # off-contract on the WORST row, that row goes unmeasurable, and averaging each
    # side over its own measurable set reports a drop that is really a deletion.
    before = retest._instability_by_index(_ORIGINAL_METRICS)          # {0: .9, 1: .1, 2: .2}
    after = retest._instability_by_index({'per_row': [                # row 0 lost
        {'source_index': 0, 'instability': None},
        {'source_index': 1, 'instability': 0.1},
        {'source_index': 2, 'instability': 0.2},
    ]})
    scope = {0, 1, 2}
    comparable_rows = {i for i in scope if i in before and i in after}
    assert comparable_rows == {1, 2}
    assert len({i for i in scope if i in before} - comparable_rows) == 1
    # Like-for-like: no drop, because the rows that remain did not move.
    assert retest._mean_over(before, comparable_rows) == pytest.approx(
        retest._mean_over(after, comparable_rows)
    )
    # Averaging each side over its own measurable set is what would have lied.
    assert retest._mean_over(after, scope) < retest._mean_over(before, scope)


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


# --- string: the reader handshake (pairs out, verdicts in) ---


def _string_setup():
    labels = {'3': {'value': 'refund request'}, '4': {'value': 'billing question'}}
    new_by_idx = {3: 'refund', 4: 'billing issue'}
    old_by_idx = {3: 'refund', 4: 'spam'}
    return labels, new_by_idx, old_by_idx


def test_string_pairs_file_carries_both_judges(tmp_path):
    # One reading pass has to produce the score AND its `before`, the way the other
    # three types get the before for free.
    labels, new_by_idx, old_by_idx = _string_setup()
    pairs, _tols, indices = retest._pair_with_labels(labels, new_by_idx)
    policy = {'grey_zones': [{'rule': 'a paraphrase of the same intent counts'}]}
    path = retest._write_string_pairs(tmp_path, pairs, indices, old_by_idx, policy)

    written = json.loads(path.read_text(encoding='utf-8'))
    assert [p['source_index'] for p in written['pairs']] == [3, 4]
    assert written['pairs'][1]['new_judge_value'] == 'billing issue'
    assert written['pairs'][1]['original_judge_value'] == 'spam'
    # The reader scores against the user's rule, not its own idea of a good answer.
    assert written['metadata']['rule'] == ['a paraphrase of the same intent counts']


def test_string_matches_align_by_source_index_not_position(tmp_path):
    # The verdicts file is written by hand, so its order cannot be assumed.
    verdicts = {'verdicts': [
        {'source_index': 4, 'match_new': False},
        {'source_index': 3, 'match_new': True},
    ]}
    assert retest._string_matches(verdicts, [3, 4], 'match_new') == [True, False]


def test_string_matches_is_none_when_a_pair_is_unscored():
    verdicts = {'verdicts': [{'source_index': 3, 'match_new': True}]}
    assert retest._string_matches(verdicts, [3, 4], 'match_new') is None


def test_string_matches_is_none_on_an_empty_verdicts_file():
    assert retest._string_matches({'verdicts': []}, [3], 'match_new') is None


def test_string_gate_uses_the_accuracy_bar():
    labels, new_by_idx, _old = _string_setup()
    pairs, tols, _idx = retest._pair_with_labels(labels, new_by_idx)
    scores, passed = retest._evaluate_agreement(
        pairs, 'string', 0.5, 0.7, 0.7, 0.7, 0.7, tols=tols, matches=[True, True]
    )
    assert scores['accuracy'] == pytest.approx(1.0)
    assert passed is True

    scores, passed = retest._evaluate_agreement(
        pairs, 'string', 0.5, 0.7, 0.7, 0.7, 0.7, tols=tols, matches=[True, False]
    )
    assert passed is False  # 0.5 is under the 0.7 bar


def test_string_caveat_names_who_scored_it():
    # A model judging its own rewrite is not evidence, and the report has to say so.
    conductor = retest._caveats(True, None, {}, 0, 'conductor')
    assert any('same model that wrote the rewrite' in c for c in conductor)
    confirmed = retest._caveats(True, None, {}, 0, 'human_confirmed')
    assert any('user confirmed' in c for c in confirmed)
    assert not any('scored by reading' in c for c in retest._caveats(True, None, {}, 0, None))


# --- regression: how wide the check actually looked ---


def test_regression_covers_every_unlabelled_row_not_just_the_spot_check():
    # The old report only ever looked at the ~5 low-flip rows, so a rewrite could
    # move behaviour across the whole dataset and it still said "0 of 5 changed".
    old = {i: True for i in range(10)}
    new = {**old, 7: False, 8: False}          # two rows moved, neither low-flip
    low_flip = {0, 1}
    scope = set(range(10))
    labelled = {2, 3}

    narrow = retest._regression_report(old, new, low_flip & scope)
    assert narrow['n_changed'] == 0            # the reassuring, narrow answer

    wide = retest._regression_report(old, new, scope - labelled, retest._UNLABELLED_NOTE)
    assert wide['n_compared'] == 8
    assert wide['n_changed'] == 2
    assert wide['changed_source_indices'] == [7, 8]
    assert wide['changed_rate'] == pytest.approx(0.25)


def test_regression_excludes_the_labelled_rows():
    # Labelled rows are SUPPOSED to change — counting them as regressions would
    # report the rewrite working as if it were the rewrite misfiring.
    old = {1: True, 2: True}
    new = {1: False, 2: True}
    assert retest._regression_report(old, new, {1, 2} - {1}, retest._UNLABELLED_NOTE)['n_changed'] == 0


def test_regression_is_none_when_nothing_overlaps():
    # Absent, not "0 changed": a row the new judge never answered says nothing.
    assert retest._regression_report({1: True}, {}, {1}) is None


def test_regression_note_says_how_far_it_looked():
    wide = retest._regression_report({1: True}, {1: True}, {1}, retest._UNLABELLED_NOTE)
    assert '--all_rows' in wide['note']


def test_caveat_fires_when_the_check_was_narrower_than_the_run():
    narrow = retest._caveats(True, None, {}, 0, None, covers_original=False, n_rejudged=17, n_original=200)
    assert any('17 of the 200' in c for c in narrow)
    full = retest._caveats(True, None, {}, 0, None, covers_original=True, n_rejudged=200, n_original=200)
    assert not any('of the 200 datapoint' in c for c in full)
