"""build_queue surfaces cross-model disagreers as confusers (RES-980 §11.3 opt 4).

When the primary judge is flat (instability 0 everywhere) the normal confuser
queue is empty, but datapoints in cross_model.json's `disagreeing_indices` must
still be surfaced — tagged `reason: "cross_model"` — so the grey-zone stage has
something to work with.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

import build_queue  # noqa: E402

_SKILL = Path(__file__).resolve().parents[1]
_CONFIG = str(_SKILL / 'config.toml')


def _write(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj), encoding='utf-8')


def _stable_run(tmp_path: Path) -> Path:
    """A run where the judge was unanimous everywhere (no flipped confusers)."""
    d = tmp_path / 'run'
    d.mkdir()
    _write(d / 'evaluator.json', {'id': 'e', 'prompt': 'judge', 'output_type': 'boolean', 'variables': []})
    _write(d / 'metrics.json', {'metadata': {'output_type': 'boolean'}, 'scores': {}, 'per_row': [
        {'source_index': 0, 'instability': 0.0, 'band': 'stable', 'n_successful_repeats': 5, 'n_true': 5, 'n_false': 0},
        {'source_index': 1, 'instability': 0.0, 'band': 'stable', 'n_successful_repeats': 5, 'n_true': 5, 'n_false': 0},
    ]})
    _write(d / 'stability.json', {'rows': [
        {'source_index': 0, 'query': 'a', 'output': 'a', 'messages': None, 'aggregate_value': True},
        {'source_index': 1, 'query': 'b', 'output': 'b', 'messages': None, 'aggregate_value': True},
    ]})
    return d


def test_build_queue_surfaces_cross_model_disagreers(tmp_path):
    d = _stable_run(tmp_path)
    _write(d / 'cross_model.json', {'disagreeing_indices': [1]})

    build_queue.main(run_dir=str(d), config=_CONFIG, count=-1, low_flip_sample_size=0)

    q = json.loads((d / 'queue.json').read_text(encoding='utf-8'))
    by_idx = {it['source_index']: it for it in q['items']}
    assert 1 in by_idx  # the cross-model disagreer is surfaced
    assert by_idx[1]['reason'] == 'cross_model'
    assert 0 not in by_idx  # stable + not a disagreer → not a confuser


def test_build_queue_without_cross_model_is_unchanged(tmp_path):
    d = _stable_run(tmp_path)  # no cross_model.json
    build_queue.main(run_dir=str(d), config=_CONFIG, count=-1, low_flip_sample_size=0)
    q = json.loads((d / 'queue.json').read_text(encoding='utf-8'))
    assert q['items'] == []  # flat judge, no probe → empty queue (as before)
