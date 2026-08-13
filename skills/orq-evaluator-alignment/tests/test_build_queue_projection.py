"""build_queue's grey-zone payload projection (design §12.4).

The context budget rides on the step-5 count decision rather than adding a gate of
its own: `build_queue` reports, in the same breath as building the queue, how much
of it will actually fit in the conductor's context. The number must be the *same*
one `grey_zone.py assemble` produces later — a projection that drifts from reality
is worse than no projection, so that equality is pinned here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

import build_queue  # noqa: E402  (scripts/build_queue.py)
import grey_zone as grey_zone_cli  # noqa: E402  (scripts/grey_zone.py, the CLI)

_SKILL = Path(__file__).resolve().parents[1]
_CONFIG = str(_SKILL / 'config.toml')


def _write(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj), encoding='utf-8')


def _seed_run_dir(tmp_path: Path, n: int = 12, chars: int = 4000) -> None:
    """A run dir just past metrics: an evaluator, N unstable rows, their inputs."""
    _write(tmp_path / 'evaluator.json', {
        'prompt': 'Judge this: {{output}}',
        'output_type': 'boolean',
    })
    _write(tmp_path / 'metrics.json', {
        'metadata': {'evaluator_id': 'ev1', 'evaluator_key': 'k', 'judge_model': 'm',
                     'output_type': 'boolean'},
        'per_row': [
            {'source_index': i, 'instability': 0.9, 'band': 'unreliable',
             'n_true': 3, 'n_false': 2, 'n_successful_repeats': 5,
             'representative_explanation': 'borderline'}
            for i in range(n)
        ],
    })
    _write(tmp_path / 'stability.json', {
        'rows': [
            {'source_index': i, 'query': 'q', 'output': f'Judge this: {"z" * chars}',
             'messages': None}
            for i in range(n)
        ],
    })


def test_build_queue_records_a_grey_zone_projection(tmp_path):
    _seed_run_dir(tmp_path)

    build_queue.main(run_dir=str(tmp_path), config=_CONFIG, count=-1, low_flip_sample_size=0)

    queue = json.loads((tmp_path / 'queue.json').read_text(encoding='utf-8'))
    projection = queue['meta']['grey_zone_projection']
    assert projection['estimated_tokens'] > 0
    assert projection['budget_tokens'] == 60000


def test_projection_matches_what_assemble_actually_produces(tmp_path):
    # Anti-drift: same pure code path, so the step-5 number cannot promise one
    # thing and step 6 deliver another.
    _seed_run_dir(tmp_path)
    build_queue.main(run_dir=str(tmp_path), config=_CONFIG, count=-1, low_flip_sample_size=0)
    projection = json.loads(
        (tmp_path / 'queue.json').read_text(encoding='utf-8')
    )['meta']['grey_zone_projection']

    grey_zone_cli.assemble(run_dir=str(tmp_path), config=_CONFIG)
    budget = json.loads(
        (tmp_path / 'grey_zone_payload.json').read_text(encoding='utf-8')
    )['budget']

    assert projection == budget


def test_projection_failure_does_not_fail_the_queue_build(tmp_path, monkeypatch):
    # The queue is the artifact that matters; the projection is a courtesy. If
    # sizing blows up, the user still gets their queue.
    _seed_run_dir(tmp_path, n=2)

    def _boom(*_args, **_kwargs):
        raise RuntimeError('sizing blew up')

    monkeypatch.setattr(build_queue.gzlib, 'assemble_payload', _boom)
    build_queue.main(run_dir=str(tmp_path), config=_CONFIG, count=-1, low_flip_sample_size=0)

    queue = json.loads((tmp_path / 'queue.json').read_text(encoding='utf-8'))
    assert len(queue['items']) == 2
    assert queue['meta']['grey_zone_projection'] is None
