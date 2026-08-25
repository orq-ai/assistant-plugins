"""build_queue's confuser-queue bugs (RES-978 PR #63 review, §3.2/§3.3/§3.4/§4.6).

Four defects in `main()`, all around the confuser-list build:
  1. `low_pool` didn't exclude rows already queued as `wrong_vs_reference`, so a
     stable-but-wrong row could show up twice (once per reason).
  2. The queue's accounting (meta count + success log + "no confusers" warning)
     didn't know about the `wrong_vs_reference` class at all.
  3. `--count` only capped the `flipped` list, not the whole confuser list, so a
     large `wrong_vs_reference` tail always got through uncapped.
  4. Low-flip items never got a `judge_correct` verdict — the argument was
     simply omitted from the `_display_item` call.
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


def _stable_row(idx: int) -> dict:
    return {
        'source_index': idx, 'instability': 0.0, 'band': 'stable',
        'n_successful_repeats': 5, 'n_true': 5, 'n_false': 0,
    }


def _flipped_row(idx: int) -> dict:
    return {
        'source_index': idx, 'instability': 0.9, 'band': 'unreliable',
        'n_successful_repeats': 5, 'n_true': 3, 'n_false': 2,
    }


def _seed_stability(d: Path, indices: list[int]) -> None:
    _write(d / 'stability.json', {
        'rows': [{'source_index': i, 'query': 'q', 'output': 'o', 'messages': None} for i in indices],
    })


# ── §3.2 — low_pool excludes rows already queued as wrong_vs_reference ───────


def test_wrong_vs_reference_row_never_duplicates_into_low_flip(tmp_path):
    d = tmp_path / 'run'
    d.mkdir()
    _write(d / 'evaluator.json', {'prompt': 'judge', 'output_type': 'boolean'})
    _write(d / 'metrics.json', {
        'metadata': {'output_type': 'boolean'},
        'per_row': [_stable_row(0)],  # stable, so also eligible for the low pool
        'correctness': {'wrong_source_indices': [0], 'labelled_source_indices': [0]},
    })
    _seed_stability(d, [0])

    build_queue.main(run_dir=str(d), config=_CONFIG, count=-1, low_flip_sample_size=1)

    q = json.loads((d / 'queue.json').read_text(encoding='utf-8'))
    reasons = [it['reason'] for it in q['items'] if it['source_index'] == 0]
    assert reasons == ['wrong_vs_reference']  # appears exactly once, never as low_flip too


# ── §3.3 — accounting knows about the wrong_vs_reference class ───────────────


def test_wrong_only_queue_is_counted_and_does_not_warn_no_confusers(tmp_path, monkeypatch):
    d = tmp_path / 'run'
    d.mkdir()
    _write(d / 'evaluator.json', {'prompt': 'judge', 'output_type': 'boolean'})
    per_row = [_stable_row(i) for i in range(12)]
    _write(d / 'metrics.json', {
        'metadata': {'output_type': 'boolean'},
        'per_row': per_row,
        'correctness': {'wrong_source_indices': list(range(12)), 'labelled_source_indices': list(range(12))},
    })
    _seed_stability(d, list(range(12)))

    warnings: list[str] = []
    monkeypatch.setattr(build_queue.logger, 'warning', lambda msg: warnings.append(msg))

    build_queue.main(run_dir=str(d), config=_CONFIG, count=-1, low_flip_sample_size=0)

    q = json.loads((d / 'queue.json').read_text(encoding='utf-8'))
    assert q['meta']['n_wrong_vs_reference'] == 12
    assert not any('No confusers' in w for w in warnings)


# ── §3.4 — --count caps the whole confuser list, not just `flipped` ──────────


def test_count_caps_the_combined_confuser_list(tmp_path):
    d = tmp_path / 'run'
    d.mkdir()
    _write(d / 'evaluator.json', {'prompt': 'judge', 'output_type': 'boolean'})
    flipped_rows = [_flipped_row(i) for i in range(3)]
    wrong_rows = [_stable_row(i) for i in range(3, 17)]
    low_rows = [_stable_row(i) for i in range(17, 19)]
    per_row = flipped_rows + wrong_rows + low_rows
    _write(d / 'metrics.json', {
        'metadata': {'output_type': 'boolean'},
        'per_row': per_row,
        'correctness': {'wrong_source_indices': list(range(3, 17)), 'labelled_source_indices': []},
    })
    _seed_stability(d, list(range(19)))

    build_queue.main(run_dir=str(d), config=_CONFIG, count=3, low_flip_sample_size=2)
    q = json.loads((d / 'queue.json').read_text(encoding='utf-8'))
    reasons = [it['reason'] for it in q['items']]
    assert reasons.count('instability') == 3
    assert reasons.count('wrong_vs_reference') == 0  # capped away — only 3 confusers fit
    assert reasons.count('low_flip') == 2
    assert len(q['items']) == 5
    assert q['meta']['n_flipped_items'] == 3
    assert q['meta']['n_wrong_vs_reference'] == 0

    build_queue.main(run_dir=str(d), config=_CONFIG, count=0, low_flip_sample_size=2)
    q0 = json.loads((d / 'queue.json').read_text(encoding='utf-8'))
    assert [it['reason'] for it in q0['items']] == ['low_flip', 'low_flip']
    assert q0['meta']['n_flipped_items'] == 0
    assert q0['meta']['n_wrong_vs_reference'] == 0


# ── §4.6 — low-flip items carry judge_correct ─────────────────────────────────


def test_low_flip_item_carries_judge_correct(tmp_path):
    d = tmp_path / 'run'
    d.mkdir()
    _write(d / 'evaluator.json', {'prompt': 'judge', 'output_type': 'boolean'})
    _write(d / 'metrics.json', {
        'metadata': {'output_type': 'boolean'},
        'per_row': [_stable_row(0)],
        'correctness': {'wrong_source_indices': [], 'labelled_source_indices': [0]},
    })
    _seed_stability(d, [0])

    build_queue.main(run_dir=str(d), config=_CONFIG, count=-1, low_flip_sample_size=1)

    q = json.loads((d / 'queue.json').read_text(encoding='utf-8'))
    item = q['items'][0]
    assert item['reason'] == 'low_flip'
    assert item['judge_correct'] is True


# ── MINOR 10a — an omitted correctness block never produces wrong_vs_reference ──


def test_omitted_correctness_yields_no_wrong_vs_reference_and_judge_correct_none(tmp_path):
    # metrics.json's `correctness` can be the OMISSION shape (n_labelled=0 +
    # reason_omitted — a reference-family variable, or an undeclared numeric
    # scale) rather than either a real correctness dict or absent entirely.
    # build_queue reads `wrong_source_indices` / `labelled_source_indices` off it
    # with `.get(...) or []`/`or set()`, which already degrades gracefully on that
    # shape (both keys are simply missing) — pinned here so a future change to
    # either field's default can't regress it silently.
    d = tmp_path / 'run'
    d.mkdir()
    _write(d / 'evaluator.json', {'prompt': 'judge', 'output_type': 'number'})
    _write(d / 'metrics.json', {
        'metadata': {'output_type': 'number'},
        'per_row': [_stable_row(0), _flipped_row(1)],
        'correctness': {
            'n_labelled': 0,
            'reason_omitted': (
                'no declared scale and no configured numeric_tol — a fixed absolute band '
                'would be arbitrary (0.5 is half of a 0-1 scale)'
            ),
        },
    })
    _seed_stability(d, [0, 1])

    build_queue.main(run_dir=str(d), config=_CONFIG, count=-1, low_flip_sample_size=1)

    q = json.loads((d / 'queue.json').read_text(encoding='utf-8'))
    assert q['items']  # the instability confuser still queues normally
    assert all(it['reason'] != 'wrong_vs_reference' for it in q['items'])
    assert all(it['judge_correct'] is None for it in q['items'])
    assert q['meta']['n_wrong_vs_reference'] == 0
