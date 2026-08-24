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
    assert retest._resolve_numeric_tol(None, {}, policy) == (1.0, 'policy_uniform')


def test_resolve_numeric_tol_cli_override_wins():
    policy = {'output_type': 'number', 'labels': [{'source_index': 3, 'value': 4.0, 'tolerance': 1.0}]}
    assert retest._resolve_numeric_tol(0.25, {'numeric_tol': 2.0}, policy) == (0.25, 'cli')


def test_resolve_numeric_tol_derives_the_default_from_the_scale():
    # Signal (a) normalizes by the declared range; signal (b) now matches, instead
    # of applying an absolute 0.5 that means half the scale on one judge and 0.5%
    # on another.
    tol, source = retest._resolve_numeric_tol(None, {}, None, [0.0, 1.0])
    assert tol == pytest.approx(0.1)
    assert source == 'scale_derived'
    tol, source = retest._resolve_numeric_tol(None, {}, None, [0.0, 100.0])
    assert tol == pytest.approx(10.0)
    assert source == 'scale_derived'


def test_resolve_numeric_tol_configured_absolute_beats_the_derived_default():
    assert retest._resolve_numeric_tol(None, {'numeric_tol': 2.0}, None, [0.0, 1.0]) == (2.0, 'configured')


def test_resolve_numeric_tol_ignores_a_blank_config_key():
    # config.toml ships `numeric_tol = ""` to mean "derive it"; an empty string must
    # not float() into a crash or a 0.0 band that fails every point.
    tol, source = retest._resolve_numeric_tol(None, {'numeric_tol': ''}, None, [0.0, 10.0])
    assert tol == pytest.approx(1.0)
    assert source == 'scale_derived'


def test_resolve_numeric_tol_honours_a_configured_fraction():
    cfg = {'numeric_tol': '', 'numeric_tol_fraction': 0.25}
    tol, source = retest._resolve_numeric_tol(None, cfg, None, [0.0, 4.0])
    assert tol == pytest.approx(1.0)
    assert source == 'scale_derived'


# --- IMPORTANT 2 — the 0.5 fallback is a distinct, named source ---------------


def test_resolve_numeric_tol_names_the_fallback_when_nothing_is_declared():
    # No --tol, no policy, no numeric_tol, no scale: the ONLY case where the
    # returned band is arbitrary rather than derived from something the user
    # actually said. `main` refuses on this source before judging (IMPORTANT 2).
    tol, source = retest._resolve_numeric_tol(None, {}, None, None)
    assert source == 'fallback'
    from lib import agreement as agreement_lib
    assert tol == agreement_lib.FALLBACK_TOL


def test_resolve_numeric_tol_zero_width_scale_is_still_a_fallback():
    tol, source = retest._resolve_numeric_tol(None, {}, None, [2.0, 2.0])
    assert source == 'fallback'


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


# --- dataset labels are parsed into the verdict space before merging (§1.1) ---


def test_unreadable_reference_is_skipped_not_crashed(tmp_path):
    # today: lib.agreement._coerce_bool raises ValueError on a free-text reference
    # for a boolean judge, uncaught — the retest run crashes AFTER the judge calls
    # it exists to score have already been spent.
    _write(tmp_path / 'stability.json', {'rows': [
        {'source_index': 3, 'reference': 'Paris is the capital of France.'},
    ]})
    labels, n_added, n_unreadable = retest._merge_dataset_labels(
        tmp_path, {}, 'boolean', [], []
    )
    assert labels == {}
    assert n_added == 0
    assert n_unreadable == 1


def test_reference_is_judge_input_is_not_merged_as_ground_truth(tmp_path):
    # An evaluator that declares {{log.reference}} was shown the answer as INPUT;
    # merging it back in as ground truth would grade the judge on what it was told.
    _write(tmp_path / 'stability.json', {'rows': [
        {'source_index': 0, 'reference': 'safe'},
    ]})
    labels, n_added, n_unreadable = retest._merge_dataset_labels(
        tmp_path, {}, 'categorical', ['safe', 'abuse'], ['log.reference', 'log.output']
    )
    assert labels == {}
    assert n_added == 0
    assert n_unreadable == 0


def test_readable_references_merge_normally(tmp_path):
    _write(tmp_path / 'stability.json', {'rows': [
        {'source_index': 1, 'reference': 'true'},
        {'source_index': 2, 'reference': 'no'},
    ]})
    labels, n_added, n_unreadable = retest._merge_dataset_labels(
        tmp_path, {}, 'boolean', [], []
    )
    assert labels['1'] == {'value': True, 'label_source': 'dataset_reference'}
    assert labels['2'] == {'value': False, 'label_source': 'dataset_reference'}
    assert n_added == 2
    assert n_unreadable == 0


def test_unreadable_categorical_reference_is_skipped(tmp_path):
    _write(tmp_path / 'stability.json', {'rows': [
        {'source_index': 0, 'reference': 'not-a-declared-label'},
    ]})
    labels, n_added, n_unreadable = retest._merge_dataset_labels(
        tmp_path, {}, 'categorical', ['safe', 'abuse'], []
    )
    assert labels == {}
    assert n_added == 0
    assert n_unreadable == 1


def test_a_human_answer_is_never_overwritten_by_a_dataset_label(tmp_path):
    _write(tmp_path / 'stability.json', {'rows': [
        {'source_index': 5, 'reference': 'true'},
    ]})
    labels, n_added, n_unreadable = retest._merge_dataset_labels(
        tmp_path, {'5': {'value': False}}, 'boolean', [], []
    )
    assert labels == {'5': {'value': False}}
    assert n_added == 0


# --- dataset-labelled rows are re-judged only on request (§3.6) ---


def test_dataset_labelled_rows_are_excluded_from_the_default_wanted_set():
    labels_before_merge = {str(i): {'value': True} for i in range(3)}
    labels_merged = {
        **labels_before_merge,
        **{str(i): {'value': True, 'label_source': 'dataset_reference'} for i in range(3, 20)},
    }
    wanted = retest._build_wanted(
        labels_before_merge, labels_merged, all_rows=False, with_dataset_labels=False,
        with_low_flip=False, low_flip=set(),
    )
    assert wanted == {0, 1, 2}


def test_with_dataset_labels_flag_adds_the_merged_rows():
    labels_before_merge = {str(i): {'value': True} for i in range(3)}
    labels_merged = {
        **labels_before_merge,
        **{str(i): {'value': True, 'label_source': 'dataset_reference'} for i in range(3, 20)},
    }
    wanted = retest._build_wanted(
        labels_before_merge, labels_merged, all_rows=False, with_dataset_labels=True,
        with_low_flip=False, low_flip=set(),
    )
    assert wanted == set(range(20))


def test_all_rows_flag_short_circuits_the_wanted_set():
    wanted = retest._build_wanted(
        {'0': {'value': True}}, {'0': {'value': True}}, all_rows=True,
        with_dataset_labels=False, with_low_flip=False, low_flip=set(),
    )
    assert wanted is None


def test_with_low_flip_still_unions_in_the_spot_check_rows():
    wanted = retest._build_wanted(
        {'0': {'value': True}}, {'0': {'value': True}}, all_rows=False,
        with_dataset_labels=False, with_low_flip=True, low_flip={9, 11},
    )
    assert wanted == {0, 9, 11}


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
#
# IMPORTANT 3: these used to run against a local `_gate()` that mirrored
# `retest.main`'s own success expression verbatim — so a bug in the real `and`/`or`
# wiring would have to be reproduced in the mirror to ever be caught, which is
# exactly backwards. Replaced with real `main()`-level tests using
# `_stub_stability_main`/`_seed_full_run` (defined below): they exercise the ACTUAL
# `success = bool(instability_dropped and agreement_passed and comparable and not
# regressed_vs_before)` line in `retest.py`, not a copy of it. No mirror of that
# expression remains as a test oracle anywhere in this file.


def test_success_true_when_both_signals_pass_and_nothing_regresses(tmp_path, monkeypatch):
    n = 4
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})
    _stub_stability_main(monkeypatch, lambda row: True, output_type='boolean')

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['agreement']['passed'] is True
    assert rm['instability']['dropped'] is True
    assert rm['instability']['comparable'] is True
    assert rm['agreement']['regressed_vs_before'] is not True
    assert rm['success'] is True


def test_success_is_false_when_the_measurement_is_not_comparable(tmp_path, monkeypatch):
    # Fewer repeats than the original run under-estimate instability, so a "drop"
    # can be the sample size alone — `comparable=False` must block `success` even
    # when both signals individually look like a pass.
    n = 4
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    metrics = json.loads((tmp_path / 'metrics.json').read_text(encoding='utf-8'))
    metrics['metadata']['n_repeats'] = 10  # the original run's recorded repeat count
    _write(tmp_path / 'metrics.json', metrics)
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})
    _stub_stability_main(monkeypatch, lambda row: True, output_type='boolean')

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG, n_repeats=2)  # fewer than 10

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['agreement']['passed'] is True  # the bar it would otherwise clear
    assert rm['instability']['comparable'] is False
    assert rm['success'] is False


def test_success_is_forced_false_by_a_regression_vs_before(tmp_path, monkeypatch): # §2.1
    # A real end-to-end proof, not a mirrored expression: a rewrite that clears
    # the accuracy bar but is WORSE than the judge it replaces must not report
    # PASS. The original judge agreed with every label (before accuracy 1.0); the
    # new judge disagrees on 1 of 4 (after accuracy 0.75) — above the 0.7 bar on
    # its own, but a regression against `before`.
    n = 4
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})

    def verdict_fn(row: dict) -> bool:
        idx = int(row['query'][1:])  # 'q0' -> 0
        return idx != 0  # disagree on row 0 only

    _stub_stability_main(monkeypatch, verdict_fn, output_type='boolean')
    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['agreement']['accuracy'] == pytest.approx(0.75)
    assert rm['agreement']['before']['accuracy'] == pytest.approx(1.0)
    assert rm['agreement']['passed'] is True  # 0.75 clears the 0.7 bar on its own
    assert rm['agreement']['regressed_vs_before'] is True
    assert rm['success'] is False


def test_success_is_false_when_instability_did_not_drop(tmp_path, monkeypatch):
    # Agreement passes, measurement is comparable, but instability did not improve
    # (original was already 0.0, retest is also 0.0). `success` must be False: both
    # signals required (AND), not either one (OR).
    n = 4
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    metrics = json.loads((tmp_path / 'metrics.json').read_text(encoding='utf-8'))
    metrics['scores']['mean_instability'] = 0.0
    for row in metrics.get('per_row', []):
        row['instability'] = 0.0
    _write(tmp_path / 'metrics.json', metrics)
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})
    _stub_stability_main(monkeypatch, lambda row: True, output_type='boolean')

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['agreement']['passed'] is True
    assert rm['instability']['dropped'] is False
    assert rm['success'] is False


def test_success_is_false_when_agreement_fails(tmp_path, monkeypatch):
    n = 4
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})
    _stub_stability_main(monkeypatch, lambda row: False, output_type='boolean')

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['instability']['dropped'] is True
    assert rm['agreement']['passed'] is False
    assert rm['success'] is False


# --- signal (b)'s gate must respect the `before` score (§2.1) ---


def test_primary_metric_is_accuracy_for_bool_categorical_string():
    assert retest._primary_metric('boolean') == 'accuracy'
    assert retest._primary_metric('categorical') == 'accuracy'
    assert retest._primary_metric('string') == 'accuracy'


def test_primary_metric_is_within_tolerance_rate_for_numeric():
    assert retest._primary_metric('number') == 'within_tolerance_rate'
    assert retest._primary_metric('numeric') == 'within_tolerance_rate'


def test_regressed_vs_before_true_when_the_primary_metric_drops():
    after = {'accuracy': 0.75}
    before = {'accuracy': 1.0, 'n_pairs': 4}
    assert retest._regressed_vs_before('boolean', after, before) is True


def test_regressed_vs_before_false_when_it_improves():
    after = {'accuracy': 0.9}
    before = {'accuracy': 0.7, 'n_pairs': 4}
    assert retest._regressed_vs_before('boolean', after, before) is False


def test_regressed_vs_before_is_none_without_a_before_score():
    assert retest._regressed_vs_before('boolean', {'accuracy': 0.9}, None) is None
    assert retest._regressed_vs_before('boolean', {'accuracy': 0.9}, {}) is None


def test_regressed_vs_before_uses_within_tolerance_rate_for_numeric():
    after = {'within_tolerance_rate': 0.5}
    before = {'within_tolerance_rate': 0.8}
    assert retest._regressed_vs_before('number', after, before) is True


def test_caveats_name_the_regression_vs_before():
    caveats = retest._caveats(
        True, None, {}, regression_before_after=(1.0, 0.75),
    )
    assert any('regressed vs the original judge (1.0 → 0.75)' in c for c in caveats)
    assert not any('regressed vs the original judge' in c for c in retest._caveats(True, None, {}))


def test_caveats_are_always_stated():
    # Including on the good runs — especially then.
    caveats = retest._caveats(has_baseline=True, provenance={'derived': 0, 'human_confirmed': 4}, regression={})
    assert any('holdout' in c for c in caveats)


def test_caveats_name_the_selection_bias_without_a_baseline_rerun():
    caveats = retest._caveats(has_baseline=False, provenance=None, regression=None)
    assert any('--baseline_rerun' in c for c in caveats)
    assert any('--with_low_flip' in c for c in caveats)


def test_caveats_state_the_dropped_verdict_is_unreliable_without_a_baseline(): # §2.2 honesty
    # today: the no-baseline caveat says WHY the rows drift toward the middle, but
    # not that the dropped verdict itself is close to guaranteed on this default
    # path — a maintainer reading only the headline number has no reason to doubt it.
    without_baseline = retest._caveats(has_baseline=False, provenance=None, regression=None)
    assert any('unreliable without --baseline_rerun' in c for c in without_baseline)
    with_baseline = retest._caveats(has_baseline=True, provenance=None, regression=None)
    assert not any('unreliable without --baseline_rerun' in c for c in with_baseline)


def test_caveats_report_derived_labels():
    caveats = retest._caveats(
        has_baseline=True, provenance={'derived': 3, 'human_confirmed': 1}, regression={}
    )
    assert any('derived by' in c for c in caveats)


# --- temperature joins the comparability check (§4.5) ---


def test_comparable_measurement_is_false_on_a_different_temperature():
    # today: `comparable` only checks n_repeats — a retest at temperature=0.0
    # against a stability run at 1.0 reports "comparable" while measuring
    # something else entirely.
    assert retest._comparable_measurement(5, 5, 1.0, 0.0) is False


def test_comparable_measurement_is_true_on_the_same_temperature():
    assert retest._comparable_measurement(5, 5, 1.0, 1.0) is True


def test_comparable_measurement_ignores_an_undeclared_original_temperature():
    # An older run recorded no temperature at all — nothing to compare against.
    assert retest._comparable_measurement(5, 5, None, 0.3) is True


def test_comparable_measurement_still_checks_n_repeats():
    assert retest._comparable_measurement(5, 2, None, None) is False


# --- the regression check on rows the old judge never wavered on ---


def test_regression_report_counts_changed_verdicts():
    report = retest._regression_report({1: True, 2: True, 3: False}, {1: True, 2: False}, {1, 2, 3})
    assert report['n_compared'] == 2  # row 3 was not re-judged
    assert report['n_changed'] == 1
    assert report['changed_source_indices'] == [2]


def test_regression_report_is_none_without_overlap():
    assert retest._regression_report({1: True}, {2: True}, {1, 2}) is None


def test_regression_report_excludes_rows_where_both_judges_are_none():
    report = retest._regression_report(
        {1: True, 2: None, 3: None}, {1: True, 2: None, 3: False}, {1, 2, 3}
    )
    assert report['n_compared'] == 2  # row 2 excluded (both None)
    assert report['n_changed'] == 1   # row 3: None → False
    assert report['changed_source_indices'] == [3]


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
    # The reader scores against the user's rule, not its own idea of a good answer.
    assert written['metadata']['rule'] == ['a paraphrase of the same intent counts']


def test_string_pairs_are_blind_not_labelled_by_judge(tmp_path): # §4.3
    # today: `new_judge_value`/`original_judge_value` name which judge wrote which
    # answer, so a reader who knows the rewrite is being validated can be nudged
    # toward it without meaning to.
    labels, new_by_idx, old_by_idx = _string_setup()
    pairs, _tols, indices = retest._pair_with_labels(labels, new_by_idx)
    path = retest._write_string_pairs(tmp_path, pairs, indices, old_by_idx, None)

    written = json.loads(path.read_text(encoding='utf-8'))
    for entry in written['pairs']:
        assert 'new_judge_value' not in entry
        assert 'original_judge_value' not in entry
        assert {'answer_a', 'answer_b'} <= entry.keys()
        assert 'human_value' in entry  # ground truth stays labelled

    key = json.loads((tmp_path / retest.STRING_PAIRS_KEY_FILE).read_text(encoding='utf-8'))
    for entry in written['pairs']:
        idx = entry['source_index']
        slot = key[str(idx)]['a']
        assert slot in ('new', 'original')
        expected_new = new_by_idx[idx]
        expected_original = old_by_idx[idx]
        if slot == 'new':
            assert entry['answer_a'] == expected_new and entry['answer_b'] == expected_original
        else:
            assert entry['answer_a'] == expected_original and entry['answer_b'] == expected_new


def test_blind_assignment_uses_a_per_run_salt_not_a_bare_hash_of_the_index(tmp_path): # MINOR 6
    # today: `a_is_new = sha256(str(idx))[0] % 2` is a pure function of the
    # PUBLIC `source_index` — recomputable by anyone who reads string_pairs.json
    # (which carries source_index in the open) using the formula sitting right in
    # this file's source, defeating the blind. A per-run salt, stored ONLY in
    # string_pairs_key.json, is required so the key file stays the sole way to
    # unblind — matching how tests already read the key rather than recompute.
    labels = {str(i): {'value': 'x'} for i in range(30)}
    new_by_idx = {i: 'new' for i in range(30)}
    old_by_idx = {i: 'old' for i in range(30)}
    pairs, _tols, indices = retest._pair_with_labels(labels, new_by_idx)

    dir_a, dir_b = tmp_path / 'a', tmp_path / 'b'
    dir_a.mkdir()
    dir_b.mkdir()
    retest._write_string_pairs(dir_a, pairs, indices, old_by_idx, None)
    retest._write_string_pairs(dir_b, pairs, indices, old_by_idx, None)
    key_a = json.loads((dir_a / retest.STRING_PAIRS_KEY_FILE).read_text(encoding='utf-8'))
    key_b = json.loads((dir_b / retest.STRING_PAIRS_KEY_FILE).read_text(encoding='utf-8'))

    assert key_a.get('_salt') and key_b.get('_salt')
    assert key_a['_salt'] != key_b['_salt']  # a fresh salt every run
    # With a fresh salt, the same index need not land in the same slot across two
    # runs — the old bare-hash formula made this assertion fail every time.
    assert any(key_a[str(i)]['a'] != key_b[str(i)]['a'] for i in range(30))


def test_blind_verdicts_round_trip_to_the_correct_accuracy(tmp_path):
    # A reader scoring match_a/match_b blind must unblind to the SAME accuracy a
    # non-blind match_new/match_original scoring would have produced.
    labels, new_by_idx, old_by_idx = _string_setup()
    pairs, _tols, indices = retest._pair_with_labels(labels, new_by_idx)
    retest._write_string_pairs(tmp_path, pairs, indices, old_by_idx, None)
    key = json.loads((tmp_path / retest.STRING_PAIRS_KEY_FILE).read_text(encoding='utf-8'))

    # The reader says: new is right both times, original is right once (idx 3 only).
    verdicts = {'scored_by': 'conductor', 'verdicts': []}
    truth = {3: {'new': True, 'original': True}, 4: {'new': True, 'original': False}}
    for idx in indices:
        slot_a = key[str(idx)]['a']
        match_a = truth[idx][slot_a]
        match_b = truth[idx]['original' if slot_a == 'new' else 'new']
        verdicts['verdicts'].append({'source_index': idx, 'match_a': match_a, 'match_b': match_b})

    unblinded = retest._unblind_string_verdicts(verdicts, key)
    new_matches = retest._string_matches(unblinded, indices, 'match_new')
    original_matches = retest._string_matches(unblinded, indices, 'match_original')
    assert new_matches == [True, True]
    assert original_matches == [True, False]


def test_scored_by_must_be_a_known_source(tmp_path):
    # today: an unrecognised scored_by (e.g. a typo'd "human") silently drops the
    # honesty caveat instead of failing loud.
    with pytest.raises(SystemExit, match=r"'conductor'.*'human_confirmed'|'human_confirmed'.*'conductor'"):
        retest._validate_string_scored_by('human')
    retest._validate_string_scored_by('conductor')  # does not raise
    retest._validate_string_scored_by('human_confirmed')  # does not raise


# --- string_verdicts.json is provably about the current pairs (§3.7) ---


def test_pairs_fingerprint_mismatch_refuses_to_score(tmp_path):
    labels, new_by_idx, old_by_idx = _string_setup()
    pairs, _tols, indices = retest._pair_with_labels(labels, new_by_idx)

    with pytest.raises(SystemExit, match='different set of pairs'):
        retest._check_pairs_fingerprint(
            {'pairs_fingerprint': 'deadbeef'}, pairs, indices, old_by_idx
        )
    with pytest.raises(SystemExit, match='different set of pairs'):
        retest._check_pairs_fingerprint({}, pairs, indices, old_by_idx)  # missing entirely


def test_pairs_fingerprint_match_proceeds(tmp_path):
    labels, new_by_idx, old_by_idx = _string_setup()
    pairs, _tols, indices = retest._pair_with_labels(labels, new_by_idx)
    fp = retest._pairs_fingerprint(pairs, indices, old_by_idx)

    retest._check_pairs_fingerprint({'pairs_fingerprint': fp}, pairs, indices, old_by_idx)  # no raise


def test_write_string_pairs_records_the_fingerprint(tmp_path):
    labels, new_by_idx, old_by_idx = _string_setup()
    pairs, _tols, indices = retest._pair_with_labels(labels, new_by_idx)
    path = retest._write_string_pairs(tmp_path, pairs, indices, old_by_idx, None)

    written = json.loads(path.read_text(encoding='utf-8'))
    expected = retest._pairs_fingerprint(pairs, indices, old_by_idx)
    assert written['metadata']['pairs_fingerprint'] == expected


def test_string_retest_round_trips_through_the_blind_reader_handshake(tmp_path, monkeypatch):
    # End-to-end proof that main() actually wires the blinding + fingerprint +
    # scored_by checks together: write pairs, stop; the conductor answers blind
    # (match_a/match_b, no idea which is which); re-run scores correctly.
    n = 2
    _write(tmp_path / 'evaluator.json', {
        'id': 'src', 'prompt': 'Judge: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None, 'variables': [],
    })
    _write(tmp_path / 'new_evaluator.json', {
        'id': 'new', 'key': 'k', 'prompt': 'Judge better: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None,
    })
    (tmp_path / 'traces.jsonl').write_text(
        '\n'.join(json.dumps({'query': f'q{i}', 'output': f'o{i}'}) for i in range(n)) + '\n',
        encoding='utf-8',
    )
    _write(tmp_path / 'stability.json', {
        'metadata': {'output_type': 'string'},
        'rows': [{'source_index': i, 'aggregate_value': 'original answer', 'reference': ''} for i in range(n)],
    })
    _write(tmp_path / 'metrics.json', {
        'metadata': {'output_type': 'string', 'evaluator_id': 'src'},
        'scores': {'mean_instability': 0.5},
        'per_row': [{'source_index': i, 'instability': 0.5} for i in range(n)],
    })
    _write(tmp_path / 'annotations.json', {str(i): {'value': 'the human answer'} for i in range(n)})
    _stub_stability_main(monkeypatch, lambda row: 'new answer', output_type='string')

    with pytest.raises(SystemExit, match='needs a reader'):
        retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)

    pairs_payload = json.loads((tmp_path / retest.STRING_PAIRS_FILE).read_text(encoding='utf-8'))
    for entry in pairs_payload['pairs']:
        assert 'new_judge_value' not in entry and 'original_judge_value' not in entry

    verdicts = {
        'scored_by': 'conductor',
        'pairs_fingerprint': pairs_payload['metadata']['pairs_fingerprint'],
        # Both slots score as a match — the reader cannot tell which is which,
        # and does not need to for this test.
        'verdicts': [
            {'source_index': e['source_index'], 'match_a': True, 'match_b': True}
            for e in pairs_payload['pairs']
        ],
    }
    _write(tmp_path / retest.STRING_VERDICTS_FILE, verdicts)

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)
    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['agreement']['accuracy'] == 1.0
    assert rm['agreement']['scored_by'] == 'conductor'


def test_string_retest_refuses_a_missing_pairs_key_file(tmp_path, monkeypatch): # MINOR 5
    # today: `runner.read_json(out_dir / STRING_PAIRS_KEY_FILE)` is a bare read
    # with no existence check — a missing string_pairs_key.json crashes with an
    # unguided FileNotFoundError instead of the SystemExit-with-guidance every
    # other artifact check in this file gives.
    n = 1
    _write(tmp_path / 'evaluator.json', {
        'id': 'src', 'prompt': 'Judge: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None, 'variables': [],
    })
    _write(tmp_path / 'new_evaluator.json', {
        'id': 'new', 'key': 'k', 'prompt': 'Judge better: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None,
    })
    (tmp_path / 'traces.jsonl').write_text(json.dumps({'query': 'q0', 'output': 'o0'}) + '\n', encoding='utf-8')
    _write(tmp_path / 'stability.json', {
        'metadata': {'output_type': 'string'},
        'rows': [{'source_index': 0, 'aggregate_value': 'original answer', 'reference': ''}],
    })
    _write(tmp_path / 'metrics.json', {
        'metadata': {'output_type': 'string', 'evaluator_id': 'src'},
        'scores': {'mean_instability': 0.5},
        'per_row': [{'source_index': 0, 'instability': 0.5}],
    })
    _write(tmp_path / 'annotations.json', {'0': {'value': 'the human answer'}})
    _stub_stability_main(monkeypatch, lambda row: 'new answer', output_type='string')

    with pytest.raises(SystemExit, match='needs a reader'):
        retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)  # writes pairs + the key

    pairs_payload = json.loads((tmp_path / retest.STRING_PAIRS_FILE).read_text(encoding='utf-8'))
    _write(tmp_path / retest.STRING_VERDICTS_FILE, {
        'scored_by': 'conductor',
        'pairs_fingerprint': pairs_payload['metadata']['pairs_fingerprint'],
        'verdicts': [{'source_index': 0, 'match_a': True, 'match_b': True}],
    })
    (tmp_path / retest.STRING_PAIRS_KEY_FILE).unlink()  # simulate the missing key

    with pytest.raises(SystemExit, match=retest.STRING_PAIRS_KEY_FILE):
        retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)


def test_string_retest_refuses_an_unrecognised_scored_by(tmp_path, monkeypatch):
    n = 1
    _write(tmp_path / 'evaluator.json', {
        'id': 'src', 'prompt': 'Judge: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None, 'variables': [],
    })
    _write(tmp_path / 'new_evaluator.json', {
        'id': 'new', 'key': 'k', 'prompt': 'Judge better: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None,
    })
    (tmp_path / 'traces.jsonl').write_text(json.dumps({'query': 'q0', 'output': 'o0'}) + '\n', encoding='utf-8')
    _write(tmp_path / 'stability.json', {
        'metadata': {'output_type': 'string'},
        'rows': [{'source_index': 0, 'aggregate_value': 'original answer', 'reference': ''}],
    })
    _write(tmp_path / 'metrics.json', {
        'metadata': {'output_type': 'string', 'evaluator_id': 'src'},
        'scores': {'mean_instability': 0.5},
        'per_row': [{'source_index': 0, 'instability': 0.5}],
    })
    _write(tmp_path / 'annotations.json', {'0': {'value': 'the human answer'}})
    _stub_stability_main(monkeypatch, lambda row: 'new answer', output_type='string')

    with pytest.raises(SystemExit):
        retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)  # writes the pairs file

    pairs_payload = json.loads((tmp_path / retest.STRING_PAIRS_FILE).read_text(encoding='utf-8'))
    _write(tmp_path / retest.STRING_VERDICTS_FILE, {
        'scored_by': 'human',  # not in {'conductor', 'human_confirmed'}
        'pairs_fingerprint': pairs_payload['metadata']['pairs_fingerprint'],
        'verdicts': [{'source_index': 0, 'match_a': True, 'match_b': True}],
    })

    with pytest.raises(SystemExit, match='conductor'):
        retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)


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


def test_string_matches_coerces_string_false_correctly():
    verdicts = {'verdicts': [
        {'source_index': 1, 'match_new': 'false'},
        {'source_index': 2, 'match_new': 'true'},
        {'source_index': 3, 'match_new': False},
    ]}
    result = retest._string_matches(verdicts, [1, 2, 3], 'match_new')
    assert result == [False, True, False]


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


# --- five payload untruths (§4.6) ---


def test_regression_wide_none_gets_its_own_caveat():
    # today: nothing distinguishes "the unlabelled-row check found 0 changes" from
    # "the unlabelled-row check never ran" — regression_wide=None (every re-judged
    # row was labelled, so there was nothing to compare) is silent either way.
    without_wide = retest._caveats(True, None, {}, regression_wide=None)
    assert any('unlabelled-row regression check had no rows to compare' in c for c in without_wide)
    with_wide = retest._caveats(True, None, {}, regression_wide={'n_compared': 3, 'n_changed': 0})
    assert not any('unlabelled-row regression check had no rows to compare' in c for c in with_wide)


def test_metadata_label_source_names_the_merge():
    assert retest._metadata_label_source('annotations', 0) == 'annotations'
    assert retest._metadata_label_source('annotations', 3) == 'annotations+dataset_reference'
    assert retest._metadata_label_source('grey_zone_policy', 2) == 'grey_zone_policy+dataset_reference'


def test_provenance_denominator_is_the_scored_pairs_not_the_merged_count():
    # 5 dataset labels were merged, but only 2 of them ended up in `pairs` (the
    # rest were never retested); the caveat's numbers must be 2 of 5 (len(pairs)),
    # never "5 of N_merged".
    caveats = retest._caveats(
        True, {'annotations': 3, 'dataset_reference': 2}, {}, n_pairs=5,
    )
    assert any('2 of 5 labels' in c for c in caveats)


def test_build_provenance_counts_only_scored_pairs_on_the_policy_path():
    # MINOR 8: EVERY count is rescoped to `pair_indices`, not just
    # dataset_reference — 5 policy labels are `human_confirmed`, but only 3 of
    # them (0, 1, 2) were among the scored pairs, so `human_confirmed` must read
    # 3, not the policy-wide 5.
    policy = {'labels': [
        {'source_index': i, 'value': True, 'label_source': 'human_confirmed'} for i in range(5)
    ]}
    # Merge added dataset labels for indices 5..9, but only index 5 was retested.
    labels = {
        **{str(i): {'value': True} for i in range(5)},
        **{str(i): {'value': True, 'label_source': 'dataset_reference'} for i in range(5, 10)},
    }
    provenance = retest._build_provenance(policy, [0, 1, 2, 5], labels)
    assert provenance == {'derived': 0, 'human_confirmed': 3, 'dataset_reference': 1}
    assert sum(provenance.values()) == 4  # == len(pair_indices), never the policy-wide total


def test_build_provenance_on_the_annotations_path_names_both_sources():
    labels = {
        **{str(i): {'value': True} for i in range(3)},
        **{str(i): {'value': True, 'label_source': 'dataset_reference'} for i in range(3, 5)},
    }
    provenance = retest._build_provenance(None, [0, 1, 2, 3, 4], labels)
    assert provenance == {'annotations': 3, 'dataset_reference': 2}


def test_num_samples_zero_refuses_to_retest_nothing(tmp_path):
    _seed_retest_inputs(tmp_path, n_rows=5)
    with pytest.raises(SystemExit, match=r'--num_samples must be >= 1'):
        retest._select_rows(tmp_path, None, 0)


def test_covers_original_dataset_is_true_without_the_flag_when_every_row_is_labelled(tmp_path, monkeypatch):
    # today: covers_original_dataset just echoes the --all_rows FLAG, so a run
    # that happens to label (and thus re-judge) every row reports "did not cover
    # the original dataset" even though it manifestly did.
    n = 5
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})
    _stub_stability_main(monkeypatch, lambda row: True, output_type='boolean')

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)  # no --all_rows

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['regression_scope']['covers_original_dataset'] is True


# --- end-to-end retest.main(): scoping and payload honesty ---
#
# The helpers above cover retest.py's pure functions in isolation. A few defects
# (§4.4's before-side scoping, §2.2's selection_bias_controlled field, §4.6's
# covers_original_dataset) live entirely in how `main` WIRES those functions
# together, so no unit test of a single function can see them — only running
# `main` itself can. `_stub_stability_main` replaces the (heavy, judge-calling)
# `stability.main` with a fast fake that writes stability.json/metrics.json
# straight from a per-row verdict function, the way `test_pipeline.py` stubs the
# jury (`run_jury_for_row`) for its own end-to-end runs — this stubs one level
# higher because these tests are about retest.py's scoping/gating/payload wiring,
# not the jury.

FAKE_CONFIG = str(Path(__file__).resolve().parents[1] / 'tests' / 'config_fake.toml')


def _stub_stability_main(monkeypatch, verdict_fn, output_type: str = 'boolean') -> None:
    import stability

    def fake_main(*, run_dir, config, n_repeats=None, temperature=None, metrics=True):
        rd = Path(run_dir)
        rows = retest.runner.read_jsonl(rd / 'traces.jsonl')
        stab_rows, per_row = [], []
        for i, row in enumerate(rows):
            stab_rows.append({
                'source_index': i, 'aggregate_value': verdict_fn(row), 'reference': row.get('reference', ''),
            })
            per_row.append({'source_index': i, 'instability': 0.0, 'band': 'stable'})
        retest.runner.write_json(rd / 'stability.json', {
            'metadata': {'output_type': output_type, 'n_repeats': n_repeats, 'temperature': temperature},
            'rows': stab_rows,
        })
        retest.runner.write_json(rd / 'metrics.json', {
            'metadata': {'output_type': output_type, 'n_repeats': n_repeats, 'temperature': temperature},
            'scores': {'mean_instability': 0.0},
            'per_row': per_row,
        })

    monkeypatch.setattr(stability, 'main', fake_main)


def _seed_full_run(tmp_path: Path, n: int = 5, output_type: str = 'boolean') -> Path:
    """A run dir shaped like a finished alignment WITH the original stability run
    already present (stability.json + metrics.json) — what `_seed_retest_inputs`
    omits, since its callers only ever exercise `_select_rows`/`_materialize_subrun`
    directly rather than `main()`."""
    _write(tmp_path / 'evaluator.json', {
        'id': 'src', 'prompt': 'Judge: {{log.output}}', 'judge_model': 'm',
        'output_type': output_type, 'categorical_labels': [], 'scale': None, 'variables': [],
    })
    _write(tmp_path / 'new_evaluator.json', {
        'id': 'new', 'key': 'k', 'prompt': 'Judge better: {{log.output}}', 'judge_model': 'm',
        'output_type': output_type, 'categorical_labels': [], 'scale': None,
    })
    (tmp_path / 'traces.jsonl').write_text(
        '\n'.join(json.dumps({'query': f'q{i}', 'output': f'o{i}'}) for i in range(n)) + '\n',
        encoding='utf-8',
    )
    _write(tmp_path / 'stability.json', {
        'metadata': {'output_type': output_type},
        'rows': [{'source_index': i, 'aggregate_value': True, 'reference': ''} for i in range(n)],
    })
    _write(tmp_path / 'metrics.json', {
        'metadata': {'output_type': output_type, 'evaluator_id': 'src'},
        'scores': {'mean_instability': 0.5},
        'per_row': [{'source_index': i, 'instability': 0.5} for i in range(n)],
    })
    return tmp_path


def test_agreement_before_is_scoped_to_the_retested_rows(tmp_path, monkeypatch):
    # today: `old_by_idx` is unscoped, so the before-side pairs against every
    # LABELLED row regardless of how many were actually re-judged — num_samples=1
    # retests one row but reports agreement_before over all 20.
    n = 20
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})
    _stub_stability_main(monkeypatch, lambda row: True, output_type='boolean')

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG, num_samples=1)

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['agreement']['before']['n_pairs'] == 1


def test_gate_b_regression_new_judge_covers_more_rows(tmp_path, monkeypatch):
    # Old judge silent on rows 5-11, new judge answers all 12.
    # The new judge is WORSE on the 5 shared rows (2/5 = 40% vs 3/5 = 60%) but
    # better overall (9/12 = 75%) because it picks up 7 new rows. Without the
    # intersection fix, _regressed_vs_before compares 75% vs 60% and reports an
    # improvement. With the fix, it compares 40% vs 60% on the shared rows and
    # correctly reports a regression.
    n = 12
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    stab = json.loads((tmp_path / 'stability.json').read_text(encoding='utf-8'))
    for row in stab['rows']:
        idx = row['source_index']
        if idx <= 2:
            row['aggregate_value'] = True
        elif idx <= 4:
            row['aggregate_value'] = False
        else:
            row['aggregate_value'] = None
    _write(tmp_path / 'stability.json', stab)
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})

    def verdict_fn(row):
        idx = int(row['query'][1:])
        return idx not in (2, 3, 4)

    _stub_stability_main(monkeypatch, verdict_fn, output_type='boolean')

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['agreement']['accuracy'] == pytest.approx(9 / 12)
    assert rm['agreement']['before']['n_pairs'] == 5
    assert rm['agreement']['before']['accuracy'] == pytest.approx(3 / 5)
    assert rm['agreement']['after_on_shared_rows'] is not None
    assert rm['agreement']['after_on_shared_rows']['n_pairs'] == 5
    assert rm['agreement']['after_on_shared_rows']['accuracy'] == pytest.approx(2 / 5)
    assert rm['agreement']['regressed_vs_before'] is True
    assert rm['success'] is False
    assert any('shared row' in c for c in rm['caveats'])


def test_gate_b_regression_old_judge_covers_more_rows(tmp_path, monkeypatch):
    # Forward direction: old judge answers all 8, new judge silent on rows 5-7.
    # The new judge is WORSE on the 5 shared rows (3/5 = 60% vs 4/5 = 80%) but
    # this direction was already handled by the one-way scoping in the original
    # code. Verify the intersection helper preserves it.
    n = 8
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})

    def verdict_fn(row):
        idx = int(row['query'][1:])
        if idx >= 5:
            return None  # new judge can't answer these rows
        return idx != 0  # wrong on row 0 only among shared

    _stub_stability_main(monkeypatch, verdict_fn, output_type='boolean')

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    # After side: only 5 rows paired (5-7 have None verdict, skipped)
    assert rm['agreement']['n'] == 5
    # Before has all 8 labelled, but scoped to shared (5 rows)
    assert rm['agreement']['before']['n_pairs'] == 5
    # No after_on_shared_rows needed — after already equals shared
    assert rm['agreement']['after_on_shared_rows'] is None
    # Before was 100% (all True), after on shared is 4/5 = 80% → regressed
    assert rm['agreement']['regressed_vs_before'] is True


def test_selection_bias_controlled_reflects_baseline_rerun(tmp_path, monkeypatch):
    # §2.2 honesty: the field must say whether THIS run actually isolated the
    # rewrite from selection bias, not just restate the --baseline_rerun flag.
    n = 5
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})
    _stub_stability_main(monkeypatch, lambda row: True, output_type='boolean')

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)
    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['instability']['selection_bias_controlled'] is False
    assert any('unreliable without --baseline_rerun' in c for c in rm['caveats'])

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG, baseline_rerun=True)
    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['instability']['selection_bias_controlled'] is True
    assert not any('unreliable without --baseline_rerun' in c for c in rm['caveats'])


def test_selection_bias_controlled_reflects_the_outcome_not_just_the_flag(tmp_path, monkeypatch): # MINOR 7
    # today: `selection_bias_controlled` is `bool(baseline_rerun)` — the FLAG —
    # so a `--baseline_rerun` that finds no rows measurable on both sides (falls
    # back to the original-run comparison, `baseline_mean is None`) still claims
    # `true`. It must track the OUTCOME instead.
    n = 3
    _seed_full_run(tmp_path, n=n, output_type='boolean')
    _write(tmp_path / 'annotations.json', {str(i): {'value': True, 'reason': ''} for i in range(n)})

    import stability

    def fake_main(*, run_dir, config, n_repeats=None, temperature=None, metrics=True):
        rd = Path(run_dir)
        rows = retest.runner.read_jsonl(rd / 'traces.jsonl')
        is_baseline = rd.name == 'retest_baseline'
        stab_rows, per_row = [], []
        for i, _row in enumerate(rows):
            stab_rows.append({'source_index': i, 'aggregate_value': True, 'reference': ''})
            per_row.append({
                'source_index': i,
                # The baseline sub-run measures NOTHING — forces baseline_mean to
                # None even though --baseline_rerun was passed and ran.
                'instability': None if is_baseline else 0.0,
                'band': 'unmeasurable' if is_baseline else 'stable',
            })
        retest.runner.write_json(rd / 'stability.json', {
            'metadata': {'output_type': 'boolean', 'n_repeats': n_repeats, 'temperature': temperature},
            'rows': stab_rows,
        })
        retest.runner.write_json(rd / 'metrics.json', {
            'metadata': {'output_type': 'boolean', 'n_repeats': n_repeats, 'temperature': temperature},
            'scores': {'mean_instability': None},
            'per_row': per_row,
        })

    monkeypatch.setattr(stability, 'main', fake_main)

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG, baseline_rerun=True)

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['instability']['baseline_rerun_mean'] is None
    assert rm['instability']['selection_bias_controlled'] is False  # not True, despite the flag
    assert any('unreliable without --baseline_rerun' in c for c in rm['caveats'])


# --- CRITICAL 1 — resuming a string retest must not re-judge -----------------


def test_string_retest_resume_does_not_rejudge(tmp_path, monkeypatch):
    # A judge at temperature 1 will not reproduce byte-identical modal strings, so
    # re-judging on resume regenerates string_pairs.json's fingerprint every time
    # and a string_verdicts.json written against the FIRST pass can never match —
    # a permanent "delete and redo" SystemExit loop, spending a full retest of
    # judge calls per iteration. The fix: the second call (string_verdicts.json
    # now present) must reuse the first call's judged sub-run rather than
    # re-judge. A call counter proves it directly — no re-judge, however subtle.
    n = 2
    _write(tmp_path / 'evaluator.json', {
        'id': 'src', 'prompt': 'Judge: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None, 'variables': [],
    })
    _write(tmp_path / 'new_evaluator.json', {
        'id': 'new', 'key': 'k', 'prompt': 'Judge better: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None,
    })
    (tmp_path / 'traces.jsonl').write_text(
        '\n'.join(json.dumps({'query': f'q{i}', 'output': f'o{i}'}) for i in range(n)) + '\n',
        encoding='utf-8',
    )
    _write(tmp_path / 'stability.json', {
        'metadata': {'output_type': 'string'},
        'rows': [{'source_index': i, 'aggregate_value': 'original answer', 'reference': ''} for i in range(n)],
    })
    _write(tmp_path / 'metrics.json', {
        'metadata': {'output_type': 'string', 'evaluator_id': 'src'},
        'scores': {'mean_instability': 0.5},
        'per_row': [{'source_index': i, 'instability': 0.5} for i in range(n)],
    })
    _write(tmp_path / 'annotations.json', {str(i): {'value': 'the human answer'} for i in range(n)})

    import stability

    calls = {'n': 0}

    def counting_fake_main(*, run_dir, config, n_repeats=None, temperature=None, metrics=True):
        calls['n'] += 1
        # A DIFFERENT answer on every call: if the fix regresses and re-judges on
        # resume, the pairs (and their fingerprint) change too — the round trip
        # would fail on the fingerprint mismatch alone even without the counter.
        answer = f'answer take {calls["n"]}'
        rd = Path(run_dir)
        rows = retest.runner.read_jsonl(rd / 'traces.jsonl')
        stab_rows = [{'source_index': i, 'aggregate_value': answer, 'reference': ''} for i in range(len(rows))]
        per_row = [{'source_index': i, 'instability': 0.0, 'band': 'stable'} for i in range(len(rows))]
        retest.runner.write_json(rd / 'stability.json', {
            'metadata': {'output_type': 'string', 'n_repeats': n_repeats, 'temperature': temperature},
            'rows': stab_rows,
        })
        retest.runner.write_json(rd / 'metrics.json', {
            'metadata': {'output_type': 'string', 'n_repeats': n_repeats, 'temperature': temperature},
            'scores': {'mean_instability': 0.0},
            'per_row': per_row,
        })

    monkeypatch.setattr(stability, 'main', counting_fake_main)

    with pytest.raises(SystemExit, match='needs a reader'):
        retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)
    assert calls['n'] == 1

    pairs_payload = json.loads((tmp_path / retest.STRING_PAIRS_FILE).read_text(encoding='utf-8'))
    verdicts = {
        'scored_by': 'conductor',
        'pairs_fingerprint': pairs_payload['metadata']['pairs_fingerprint'],
        'verdicts': [
            {'source_index': e['source_index'], 'match_a': True, 'match_b': True}
            for e in pairs_payload['pairs']
        ],
    }
    _write(tmp_path / retest.STRING_VERDICTS_FILE, verdicts)

    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)  # resume — must reuse, not re-judge

    assert calls['n'] == 1  # the judge was NOT called a second time
    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['agreement']['accuracy'] == 1.0
    assert rm['agreement']['scored_by'] == 'conductor'


def test_string_retest_rejudge_flag_forces_a_fresh_pass(tmp_path, monkeypatch):
    # The escape hatch: --rejudge must call the judge again even on the resume
    # path (and then legitimately invalidate stale verdicts via the fingerprint —
    # a DIFFERENT answer changes the pairs, so the carried-over verdicts file no
    # longer matches and is refused, which is the correct outcome for a forced
    # re-judge with no fresh verdicts to match it).
    n = 1
    _write(tmp_path / 'evaluator.json', {
        'id': 'src', 'prompt': 'Judge: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None, 'variables': [],
    })
    _write(tmp_path / 'new_evaluator.json', {
        'id': 'new', 'key': 'k', 'prompt': 'Judge better: {{log.output}}', 'judge_model': 'm',
        'output_type': 'string', 'categorical_labels': [], 'scale': None,
    })
    (tmp_path / 'traces.jsonl').write_text(json.dumps({'query': 'q0', 'output': 'o0'}) + '\n', encoding='utf-8')
    _write(tmp_path / 'stability.json', {
        'metadata': {'output_type': 'string'},
        'rows': [{'source_index': 0, 'aggregate_value': 'original answer', 'reference': ''}],
    })
    _write(tmp_path / 'metrics.json', {
        'metadata': {'output_type': 'string', 'evaluator_id': 'src'},
        'scores': {'mean_instability': 0.5},
        'per_row': [{'source_index': 0, 'instability': 0.5}],
    })
    _write(tmp_path / 'annotations.json', {'0': {'value': 'the human answer'}})

    import stability

    calls = {'n': 0}

    def counting_fake_main(*, run_dir, config, n_repeats=None, temperature=None, metrics=True):
        calls['n'] += 1
        answer = f'answer take {calls["n"]}'
        rd = Path(run_dir)
        retest.runner.write_json(rd / 'stability.json', {
            'metadata': {'output_type': 'string', 'n_repeats': n_repeats, 'temperature': temperature},
            'rows': [{'source_index': 0, 'aggregate_value': answer, 'reference': ''}],
        })
        retest.runner.write_json(rd / 'metrics.json', {
            'metadata': {'output_type': 'string', 'n_repeats': n_repeats, 'temperature': temperature},
            'scores': {'mean_instability': 0.0},
            'per_row': [{'source_index': 0, 'instability': 0.0, 'band': 'stable'}],
        })

    monkeypatch.setattr(stability, 'main', counting_fake_main)

    with pytest.raises(SystemExit, match='needs a reader'):
        retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)
    assert calls['n'] == 1

    pairs_payload = json.loads((tmp_path / retest.STRING_PAIRS_FILE).read_text(encoding='utf-8'))
    _write(tmp_path / retest.STRING_VERDICTS_FILE, {
        'scored_by': 'conductor',
        'pairs_fingerprint': pairs_payload['metadata']['pairs_fingerprint'],
        'verdicts': [{'source_index': 0, 'match_a': True, 'match_b': True}],
    })

    # --rejudge re-judges even though a matching sub-run exists; the fresh answer
    # changes the pairs, so the carried-over (now stale) verdicts are refused.
    with pytest.raises(SystemExit, match='different set of pairs'):
        retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG, rejudge=True)
    assert calls['n'] == 2  # the judge WAS called again


# --- IMPORTANT 2 — the numeric fallback tolerance refuses before judging -----


def test_numeric_fallback_tolerance_refuses_before_any_judging(tmp_path, monkeypatch):
    # No --tol, no grey-zone policy, no configured numeric_tol, no declared
    # scale: gate (b)'s band would be the arbitrary FALLBACK_TOL. Must refuse
    # BEFORE the retest sub-run is judged (money spent), not after.
    n = 3
    _seed_full_run(tmp_path, n=n, output_type='number')
    _write(tmp_path / 'annotations.json', {str(i): {'value': 3.0, 'reason': ''} for i in range(n)})

    import stability

    calls = {'n': 0}
    monkeypatch.setattr(stability, 'main', lambda **kwargs: calls.__setitem__('n', calls['n'] + 1))

    with pytest.raises(SystemExit, match=r'--tol|numeric_tol|scale'):
        retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG)
    assert calls['n'] == 0  # refused before any judging

    # An explicit --tol resolves the fallback and the run proceeds normally.
    _stub_stability_main(monkeypatch, lambda row: 3.0, output_type='number')
    retest.main(run_dir=str(tmp_path), config=FAKE_CONFIG, tol=0.1)

    rm = json.loads((tmp_path / 'retest_metrics.json').read_text(encoding='utf-8'))
    assert rm['agreement']['within_tolerance_rate'] == 1.0
