"""Round-trip tests for the scripts/grey_zone.py CLI (RES-980).

`assemble` turns queue.json into the conductor's compact payload; `apply` turns
the conductor-written grey_zone_policy.json into the aggregated.md guidance that
rewrite_eval already consumes. The pure logic is unit-tested in test_grey_zone.py;
these pin the file round-trips against a real run dir.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

import grey_zone as grey_zone_cli  # noqa: E402  (scripts/grey_zone.py, the CLI)

_SKILL = Path(__file__).resolve().parents[1]
_CONFIG = str(_SKILL / 'config.toml')


def _write(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj), encoding='utf-8')


def _queue() -> dict:
    vs = {'type': 'boolean', 'labels': [False, True]}
    return {
        'meta': {'verdict_space': vs, 'eval_prompt': 'Judge: {{output}}', 'n_items': 1},
        'items': [{
            'rank': 1, 'source_index': 7, 'low_flip_sample': False,
            'query': 'q', 'output': 'rendered', 'variables': [{'name': 'log.output', 'value': 'v'}],
            'messages': None, 'verdict_space': vs,
            'ambiguity': {'instability': 0.9, 'band': 'unreliable', 'n_repeats': 5},
            'judge_votes': {'n_true': 3, 'n_false': 2, 'representative_explanation': 'r'},
        }],
    }


def test_assemble_writes_payload(tmp_path):
    _write(tmp_path / 'queue.json', _queue())

    grey_zone_cli.assemble(run_dir=str(tmp_path), config=_CONFIG)

    payload = json.loads((tmp_path / 'grey_zone_payload.json').read_text(encoding='utf-8'))
    assert payload['n_confusers'] == 1
    assert payload['confusers'][0]['source_index'] == 7


def _big_queue(n: int, chars: int = 2000) -> dict:
    vs = {'type': 'boolean', 'labels': [False, True]}
    return {
        'meta': {'verdict_space': vs, 'eval_prompt': 'Judge: {{output}}', 'n_items': n},
        'items': [{
            'rank': i + 1, 'source_index': i, 'low_flip_sample': False,
            'query': 'q', 'output': 'rendered',
            'variables': [{'name': 'output', 'value': 'z' * chars}],
            'messages': None, 'verdict_space': vs,
            'ambiguity': {'instability': 0.9, 'band': 'unreliable', 'n_repeats': 5},
            'judge_votes': {'n_true': 3, 'n_false': 2, 'representative_explanation': 'r'},
        } for i in range(n)],
    }


def test_assemble_clamps_to_max_tokens_flag(tmp_path):
    _write(tmp_path / 'queue.json', _big_queue(20))

    grey_zone_cli.assemble(run_dir=str(tmp_path), config=_CONFIG, max_chars=2000, max_tokens=2000)

    payload = json.loads((tmp_path / 'grey_zone_payload.json').read_text(encoding='utf-8'))
    assert payload['n_confusers'] < 20
    assert payload['budget']['n_dropped_by_budget'] > 0
    assert payload['budget']['budget_tokens'] == 2000


def test_assemble_takes_the_budget_from_config_by_default(tmp_path):
    # config.toml ships grey_zone_max_tokens; the CLI must read it rather than
    # falling back to "unbounded" the way v1 did with the missing keys.
    _write(tmp_path / 'queue.json', _big_queue(2))

    grey_zone_cli.assemble(run_dir=str(tmp_path), config=_CONFIG)

    payload = json.loads((tmp_path / 'grey_zone_payload.json').read_text(encoding='utf-8'))
    assert payload['budget']['budget_tokens'] == 60000


def test_apply_writes_aggregated_md(tmp_path):
    _write(tmp_path / 'grey_zone_policy.json', {
        'output_type': 'boolean',
        'verdict_space': {'type': 'boolean', 'labels': [False, True]},
        'grey_zones': [{'id': 'gz1', 'question': 'sarcasm?', 'answer': 'yes it counts',
                        'rule': 'Treat sarcasm as abuse.', 'member_source_indices': [7]}],
        'labels': [{'source_index': 7, 'value': True, 'grey_zone_id': 'gz1'}],
    })

    grey_zone_cli.apply(run_dir=str(tmp_path), config=_CONFIG)

    md = (tmp_path / 'aggregated.md').read_text(encoding='utf-8')
    assert 'Treat sarcasm as abuse.' in md


def test_apply_rejects_malformed_policy(tmp_path):
    _write(tmp_path / 'grey_zone_policy.json', {'grey_zones': [], 'labels': []})  # no output_type
    with pytest.raises(ValueError, match='output_type'):
        grey_zone_cli.apply(run_dir=str(tmp_path), config=_CONFIG)
