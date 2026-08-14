"""Unit tests for retest.py's label-source adapter (RES-980).

The grey-zone flow makes grey_zone_policy.json the default source of human labels;
annotations.json stays as the (interactive) UI-fallback source. These pin that
precedence and the numeric-tolerance resolution without needing a full retest run.
"""

from __future__ import annotations

import json
import sys
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
    _write(tmp_path / 'grey_zone_policy.json', _boolean_policy())
    _write(tmp_path / 'annotations.json', {'7': {'value': False, 'reason': ''}})

    labels, source, policy = retest._load_labels(tmp_path)

    assert source == 'grey_zone_policy'
    assert labels == {'7': {'value': True}}
    assert policy is not None


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


def test_retest_dir_holds_only_the_labelled_rows(tmp_path):
    # The bug: all 24 rows were re-judged (24 x N calls) while agreement scored
    # only the labelled 3 — triple the quoted cost for verdicts nothing read.
    _seed_retest_inputs(tmp_path, n_rows=24)

    retest_dir, index_map = retest._build_retest_dir(tmp_path, {2, 7, 19})

    rows = [json.loads(line) for line in (retest_dir / 'traces.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 3
    assert [r['query'] for r in rows] == ['q2', 'q7', 'q19']


def test_index_map_translates_back_to_the_original_indices(tmp_path):
    # source_index is positional, so filtering renumbers it; the labels are keyed
    # by the ORIGINAL index and pairing would silently mis-align without this.
    _seed_retest_inputs(tmp_path, n_rows=24)

    _retest_dir, index_map = retest._build_retest_dir(tmp_path, {2, 7, 19})

    assert index_map == {0: 2, 1: 7, 2: 19}


def test_all_rows_keeps_every_row_and_an_identity_map(tmp_path):
    _seed_retest_inputs(tmp_path, n_rows=5)

    retest_dir, index_map = retest._build_retest_dir(tmp_path, None)

    rows = (retest_dir / 'traces.jsonl').read_text(encoding='utf-8').strip().splitlines()
    assert len(rows) == 5
    assert index_map == {i: i for i in range(5)}


def test_labels_pointing_at_a_different_run_fail_loudly(tmp_path):
    _seed_retest_inputs(tmp_path, n_rows=3)

    with pytest.raises(SystemExit, match='None of the labelled rows'):
        retest._build_retest_dir(tmp_path, {90, 91})


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
    assert retest._mean_instability_over(metrics, {1, 2}) == pytest.approx(0.15)


def test_original_mean_falls_back_to_the_run_wide_score():
    metrics = {'scores': {'mean_instability': 0.42}, 'per_row': []}
    assert retest._mean_instability_over(metrics, {1, 2}) == pytest.approx(0.42)


def test_original_mean_is_none_when_nothing_is_available():
    assert retest._mean_instability_over({'per_row': []}, {1}) is None
