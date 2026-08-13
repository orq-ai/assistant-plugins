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
