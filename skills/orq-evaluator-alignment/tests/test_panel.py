"""Jury mode: real script handoff with a fake judge, plus panel edge cases."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
for path in (SKILL, SKILL / 'scripts'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_queue  # noqa: E402
import estimate_cost  # noqa: E402
import metrics  # noqa: E402
import retest  # noqa: E402
import stability  # noqa: E402
from lib import panel, runner  # noqa: E402

CONFIG = str(SKILL / 'tests' / 'config_fake.toml')


def test_three_model_jury_flows_through_metrics_and_queue(tmp_path, monkeypatch, capsys):
    runner.write_json(tmp_path / 'evaluator.json', {
        'id': 'e', 'prompt': '{{log.output}}', 'judge_model': 'judge',
        'variables': ['log.output'], 'output_type': 'boolean',
    })
    runner.write_jsonl(tmp_path / 'traces.jsonl', [
        {'output': 'split', 'reference': False},
        {'output': 'wobble', 'reference': False},
        {'output': 'control', 'reference': True},
        {'output': 'failed_extra', 'reference': True},
    ])
    calls = []
    votes = {
        'split': {'judge': [True] * 3, 'other-a': [False] * 3, 'other-b': [False] * 3},
        'wobble': {'judge': [True] * 3, 'other-a': [True, False, False], 'other-b': [False] * 3},
        'control': {'judge': [True] * 3, 'other-a': [True] * 3, 'other-b': [True] * 3},
        'failed_extra': {'judge': [True] * 3, 'other-a': [True] * 3, 'other-b': None},
    }

    async def fake_jury(spec, model, *, client, repetitions, output_type, labels, scale):
        row = spec.replacements['log.output']
        calls.append((row, model, repetitions))
        reps = votes[row][model]
        if reps is None:
            return {'success': False, 'error': 'provider outage', 'repetitions': [None] * repetitions,
                    'repetitions_failed': repetitions, 'n_wrong_output_type': 0,
                    'value': None, 'explanation': None}
        value = max(set(reps), key=reps.count)
        return {'success': True, 'error': None, 'repetitions': reps,
                'repetitions_failed': 0, 'n_wrong_output_type': 0,
                'value': value, 'explanation': 'fake'}

    monkeypatch.setattr(stability, 'make_judge_client', lambda: object())
    monkeypatch.setattr(stability, 'run_jury_for_row', fake_jury)
    estimate_cost.main(run_dir=str(tmp_path), config=CONFIG, panel_models='other-a,other-b')
    assert '36 judge calls (4 datapoints × 3 repeats × 3 models)' in capsys.readouterr().out
    stability.main(run_dir=str(tmp_path), config=CONFIG, panel_models='other-a,other-b')
    build_queue.main(run_dir=str(tmp_path), config=CONFIG, count=-1, low_flip_sample_size=1)

    assert len(calls) == 12
    assert {n for _, _, n in calls} == {3}  # 3 repeats/model is the panel default
    stab = runner.read_json(tmp_path / 'stability.json')
    assert stab['metadata']['panel_models'] == ['judge', 'other-a', 'other-b']
    assert len(stab['rows'][0]['panel']) == 3
    m = runner.read_json(tmp_path / 'metrics.json')
    assert m['panel']['n_panel_disagreement'] == 2
    assert m['panel']['n_any_model_unstable'] == 1
    assert m['panel']['n_panel_unmeasurable'] == 0
    assert m['panel']['correctness_by_model']['judge']['tnr'] == 0.0
    assert m['panel']['correctness_by_model']['other-a']['tnr'] == 1.0
    queue = runner.read_json(tmp_path / 'queue.json')
    assert [(x['source_index'], x['reason']) for x in queue['items'][:2]] == [
        (1, 'panel_disagreement'), (0, 'panel_disagreement'),
    ]
    assert queue['items'][0]['panel_votes'] == {'judge': True, 'other-a': False, 'other-b': False}
    assert queue['meta']['n_panel_disagreement'] == 2
    assert len([x for x in queue['items'] if x['low_flip_sample']]) == 1


def test_tie_precedes_disagreement_and_failed_primary_is_not_queued(tmp_path):
    runner.write_json(tmp_path / 'evaluator.json', {'id': 'e', 'prompt': 'judge', 'output_type': 'boolean'})
    runner.write_jsonl(tmp_path / 'traces.jsonl', [{'output': str(i)} for i in range(3)])
    rows = [
        {'source_index': 0, 'instability': 0.0, 'band': 'stable', 'n_successful_repeats': 3,
         'panel_disagreement': True, 'panel_tied': False, 'panel_agreement': 2 / 3,
         'panel_unstable_models': [], 'panel_max_instability': 0.0},
        {'source_index': 1, 'instability': 0.0, 'band': 'stable', 'n_successful_repeats': 3,
         'panel_disagreement': True, 'panel_tied': True, 'panel_agreement': 0.5,
         'panel_unstable_models': [], 'panel_max_instability': 0.0},
        {'source_index': 2, 'instability': None, 'band': 'unmeasurable', 'n_successful_repeats': 0,
         'panel_disagreement': True, 'panel_tied': False, 'panel_agreement': 0.5,
         'panel_unstable_models': ['other'], 'panel_max_instability': 0.5},
    ]
    runner.write_json(tmp_path / 'metrics.json', {'metadata': {}, 'per_row': rows})
    runner.write_json(tmp_path / 'stability.json', {'rows': [{'source_index': i} for i in range(3)]})
    build_queue.main(run_dir=str(tmp_path), config=CONFIG, count=-1, low_flip_sample_size=0)
    queue = runner.read_json(tmp_path / 'queue.json')
    assert [x['source_index'] for x in queue['items']] == [1, 0]


def test_panel_ignores_failed_repetitions_and_reports_unmeasurable():
    votes = [
        {'model': 'judge', 'success': True, 'repetitions': [True, True, True]},
        {'model': 'outage', 'success': False, 'repetitions': [None, None, None]},
    ]
    sig = panel.row_signals(votes, 'boolean', clean=lambda r: metrics._clean_verdicts(r, 'boolean'), floor=2)
    assert sig['n_models_measured'] == 1
    assert sig['disagreement'] is None
    assert sig['unstable_models'] == []
    assert sig['per_model']['outage']['value'] is None


@pytest.mark.parametrize('output_type, primary, peer, reference, scale, tol', [
    ('categorical', ['safe'] * 3, ['abuse'] * 3, 'abuse', None, 0.5),
    ('number', [7.0] * 3, [2.0] * 3, 2.0, (0.0, 10.0), 1.0),
])
def test_panel_disagreement_and_correctness_work_for_other_verdict_types(
    output_type, primary, peer, reference, scale, tol,
):
    row = {
        'source_index': 0, 'reference': reference,
        'panel': [
            {'model': 'judge', 'success': True, 'value': primary[0], 'repetitions': primary},
            {'model': 'peer', 'success': True, 'value': peer[0], 'repetitions': peer},
        ],
    }
    entry = {'source_index': 0, 'instability': 0.0, 'n_successful_repeats': 3}
    summary = metrics._jury([row], [entry], output_type, 2 if output_type == 'categorical' else None,
                            scale, 2, tol, {'n_labelled': 1, 'labelled_source_indices': [0]})
    assert entry['panel_disagreement'] is True
    assert summary['correctness_by_model']['peer'][
        'within_tolerance_rate' if output_type == 'number' else 'accuracy'
    ] == 1.0


def test_model_diagnosis_uses_balanced_accuracy_on_same_rows():
    labels = {i: {'value': i < 3} for i in range(30)}
    rows = []
    for i in range(30):
        # The aligned judge passes everything; a peer catches 2/3 rare positives.
        votes = [
            {'model': 'judge', 'success': True, 'value': False, 'repetitions': [False] * 3},
            {'model': 'peer', 'success': True, 'value': i in (0, 1), 'repetitions': [i in (0, 1)] * 3},
        ]
        rows.append({'source_index': i, 'panel': votes})
    scores = retest._panel_agreement({'metadata': {'n_repeats': 3}, 'rows': rows}, labels,
                                     'boolean', 0.5, set(range(30)))
    assert scores['judge']['accuracy'] == pytest.approx(0.9)
    assert scores['judge']['balanced_accuracy'] == pytest.approx(0.5)
    assert scores['peer']['balanced_accuracy'] == pytest.approx(5 / 6)
    assert retest._better_panel_model(scores, 'judge', 'boolean')[0] == 'peer'
    # Equal sample counts on different rows are not a fair model comparison.
    scores['peer']['source_indices'] = list(range(1, 30)) + [99]
    assert retest._better_panel_model(scores, 'judge', 'boolean') is None
