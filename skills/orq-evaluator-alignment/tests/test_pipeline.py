"""End-to-end smoke test of the alignment pipeline on a 3-row fixture.

Exercises stability -> metrics -> build_queue -> annotation-load -> recommend
-> aggregate -> rewrite without touching the network: the judge is monkeypatched
to canned per-row verdicts and the model backend is `fake`. Asserts the run
directory fills with the expected artifacts and that the rewrite preserves the
judge's template variables.

Run:
    cd skills/orq-evaluator-alignment
    uv run pytest tests/test_pipeline.py -v
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / 'scripts'
for p in (str(SKILL_ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

FIXTURES = SKILL_ROOT / 'tests' / 'fixtures'
FAKE_CONFIG = str(SKILL_ROOT / 'tests' / 'config_fake.toml')


def test_judge_io_falls_back_to_eval_span_own_gen_ai():
    # Newer orq schema: the judge's LLM call is on the evaluator span itself,
    # with no separate child chat span. Extraction must still recover the IO.
    from fetch_traces import _judge_io, _judge_model

    eval_span = {
        'span_id': 'e1',
        'attributes': {
            'gen_ai': {
                'input': {'messages': [{'content': 'Screen this: hello world'}]},
                'request': {'model': 'openai/gpt-4o-mini'},
            }
        },
    }
    rendered, msgs = _judge_io([eval_span], eval_span)
    assert 'hello world' in rendered
    assert msgs
    assert _judge_model([eval_span], eval_span) == 'openai/gpt-4o-mini'


def test_guard_hollow_aborts_over_threshold():
    from fetch_traces import _guard_hollow

    # 3/4 hollow (75%) with a 20% threshold and no --force → abort.
    with pytest.raises(SystemExit):
        _guard_hollow(n_degraded=3, n_rows=4, abort_ratio=0.2, force=False)
    # --force persists anyway; a small fraction under threshold is fine; empty is a no-op.
    _guard_hollow(n_degraded=3, n_rows=4, abort_ratio=0.2, force=True)
    _guard_hollow(n_degraded=1, n_rows=100, abort_ratio=0.2, force=False)
    _guard_hollow(n_degraded=0, n_rows=0, abort_ratio=0.2, force=False)

# Canned per-row verdicts keyed by a substring of the judged input.
_CANNED = {
    'useless': [True, False, True, False, True],   # flips: 3T/2F, mode True
    'tokyo': [False, False, False, False, False],  # unanimous False
    'hate you': [True, True, True, True, True],     # unanimous True
}


async def _fake_run_jury_for_row(spec, judge_model, *, client, repetitions):
    text = (spec.replacements.get('log.input') or '').lower()
    reps = next((v for k, v in _CANNED.items() if k in text), [True] * repetitions)
    reps = reps[:repetitions]
    n_true = sum(reps)
    value = n_true >= (len(reps) - n_true)
    return {
        'success': True,
        'repetitions': reps,
        'repetitions_failed': 0,
        'value': value,
        'explanation': 'canned judge rationale',
    }


@pytest.fixture()
def run_dir(tmp_path, monkeypatch):
    d = tmp_path / 'fixture_run'
    d.mkdir()
    shutil.copy(FIXTURES / 'evaluator.json', d / 'evaluator.json')
    shutil.copy(FIXTURES / 'traces.jsonl', d / 'traces.jsonl')

    import stability

    monkeypatch.setattr(stability, 'run_jury_for_row', _fake_run_jury_for_row)
    monkeypatch.setattr(stability, 'make_judge_client', lambda: object())
    return d


def test_pipeline_end_to_end(run_dir):
    import aggregate
    import build_queue
    import metrics  # noqa: F401  (invoked via stability)
    import recommend
    import rewrite_eval
    import stability

    # Step 4 (+5 metrics auto): stability over the fixture.
    stability.main(run_dir=str(run_dir), config=FAKE_CONFIG)
    stab = json.loads((run_dir / 'stability.json').read_text(encoding='utf-8'))
    assert len(stab['rows']) == 3
    mx = json.loads((run_dir / 'metrics.json').read_text(encoding='utf-8'))
    assert mx['scores']['n_flipped'] == 1  # only the "useless" row flips
    assert mx['scores']['num_rows'] == 3

    # Step 6: queue = 1 flipped + 1 low-flip sanity item.
    build_queue.main(run_dir=str(run_dir), config=FAKE_CONFIG, count=-1)
    queue = json.loads((run_dir / 'queue.json').read_text(encoding='utf-8'))
    assert queue['meta']['n_flipped_items'] == 1
    assert queue['meta']['n_low_flip_sample'] == 1
    assert queue['meta']['n_items'] == 2

    # Step 7 (simulated): write human labels for both queue items. Force a
    # disagreement on the flipped row (human=False vs judge mode True).
    annotations = {}
    for it in queue['items']:
        idx = it['source_index']
        human = False if not it['low_flip_sample'] else bool(it['ambiguity']['mode_value'])
        annotations[str(idx)] = {
            'status': 'labeled',
            'value': human,
            'explanation': 'test label',
            'provenance': {'low_flip_sample': it['low_flip_sample']},
        }
    (run_dir / 'annotations.json').write_text(json.dumps(annotations), encoding='utf-8')

    # Step 8: recommend (fake backend -> canned recommendation JSON) + aggregate.
    recommend.main(run_dir=str(run_dir), config=FAKE_CONFIG)
    recs = json.loads((run_dir / 'recommendations.json').read_text(encoding='utf-8'))
    assert recs['metadata']['n_ok'] == 2
    assert all(r['success'] for r in recs['recommendations'])

    aggregate.main(run_dir=str(run_dir), config=FAKE_CONFIG)
    aggregated = (run_dir / 'aggregated.md').read_text(encoding='utf-8')
    assert 'Changes to make' in aggregated and 'Strengths to preserve' in aggregated

    # Step 9a: PO2 rewrite (fake backend echoes the prompt -> identity stub).
    rewrite_eval.main(run_dir=str(run_dir), config=FAKE_CONFIG)
    status = json.loads((run_dir / 'rewrite_status.json').read_text(encoding='utf-8'))
    assert status['var_check_passed'] is True
    assert set(status['source_vars']) == {'log.input', 'log.output'}
    new_prompt = (run_dir / 'new_prompt.md').read_text(encoding='utf-8')
    assert '{{log.input}}' in new_prompt and '{{log.output}}' in new_prompt
