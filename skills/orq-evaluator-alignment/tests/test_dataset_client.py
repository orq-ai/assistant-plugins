"""Unit tests for the orq Datasets request-body helpers (RES-980 §11.5).

The async client methods (list/get/create) hit the network, so — as with
`build_create_body` — only the pure request-body assembly is unit-tested here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

from lib import orq_client  # noqa: E402


def test_build_create_dataset_body_minimal():
    body = orq_client.build_create_dataset_body('My Dataset')
    assert body['display_name'] == 'My Dataset'
    assert 'path' not in body
    assert 'description' not in body


def test_build_create_dataset_body_with_path_and_description():
    body = orq_client.build_create_dataset_body('DS', path='Datasets/x', description='d')
    assert body == {'display_name': 'DS', 'path': 'Datasets/x', 'description': 'd'}


def test_build_create_datapoints_body_is_a_bare_array():
    """`POST /v2/datasets/{id}/datapoints` takes the rows as a top-level JSON array.

    Wrapping them as `{"datapoints": [...]}` is rejected with
    `400 invalid_request_body` — "expected array, received object" — which broke
    the dataset save-back path (`seed_inputs.py save`). Verified live 2026-08-13.
    """
    dps = [{'inputs': {'log.input': '1'}, 'expected_output': 'a', 'messages': None}]
    assert orq_client.build_create_datapoints_body(dps) == dps


def test_build_create_datapoints_body_copies_the_input_list():
    """The caller's list is not aliased into the request body."""
    dps = [{'inputs': {'log.input': '1'}}]
    body = orq_client.build_create_datapoints_body(dps)
    dps.append({'inputs': {'log.input': '2'}})
    assert len(body) == 1
