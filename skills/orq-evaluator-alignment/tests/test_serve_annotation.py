"""Tests for the per-type scoring UI's pure bits (RES-978 Part 2, §2.1).

Workstream A extracts the type-native logic out of the HTTP server so it is unit-
testable without a live browser:

  - `input_type_for` maps each queue item's `verdict_space` to the widget the UI
    should render (`boolean` / `categorical` / `number`), carrying the label set
    or numeric scale the widget needs.
  - `coerce_value` / `validate_value` turn a raw posted value into the typed
    `annotations.json` value (bool | str | number) the contract pins, per type.
  - `build_annotation_record` + the load/write round-trip pin the on-disk
    contract: `{source_index} -> {status, value, reason, low_flip_sample}`.

All of it is import-safe (no evaluatorq / lib.judge / http server), so this file
runs standalone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

import serve_annotation as sa  # noqa: E402


# --- input_type_for: verdict_space -> UI widget spec -------------------------


def test_input_type_boolean():
    spec = sa.input_type_for({'type': 'boolean', 'labels': [False, True]})
    assert spec['type'] == 'boolean'


def test_input_type_categorical_carries_labels():
    spec = sa.input_type_for({'type': 'categorical', 'labels': ['good', 'bad', 'ugly'], 'k': 3})
    assert spec['type'] == 'categorical'
    assert spec['labels'] == ['good', 'bad', 'ugly']


def test_input_type_number_carries_scale():
    spec = sa.input_type_for({'type': 'number', 'scale': [1, 5]})
    assert spec['type'] == 'number'
    assert spec['scale'] == [1.0, 5.0]


def test_input_type_number_accepts_numeric_alias():
    spec = sa.input_type_for({'type': 'numeric', 'scale': [0, 10]})
    assert spec['type'] == 'number'
    assert spec['scale'] == [0.0, 10.0]


def test_input_type_number_scale_may_be_absent():
    # Numeric evaluators can lack a scale (fetch keeps it override-only); the
    # widget still renders — just an unbounded number input.
    spec = sa.input_type_for({'type': 'number', 'scale': None})
    assert spec['type'] == 'number'
    assert spec['scale'] is None


def test_input_type_defaults_to_boolean_when_missing_or_unknown():
    assert sa.input_type_for({})['type'] == 'boolean'
    assert sa.input_type_for(None)['type'] == 'boolean'
    # A type this build does not support — e.g. a queue.json written by an older
    # version that still had free-form `string` — renders as boolean rather than
    # crashing the UI on an unknown widget spec.
    assert sa.input_type_for({'type': 'string'})['type'] == 'boolean'


# --- coerce_value / validate_value: typed value per output type --------------


def test_coerce_boolean():
    assert sa.coerce_value({'type': 'boolean'}, True) is True
    assert sa.coerce_value({'type': 'boolean'}, False) is False


def test_coerce_categorical_must_be_declared_label():
    space = {'type': 'categorical', 'labels': ['good', 'bad']}
    assert sa.coerce_value(space, 'good') == 'good'
    with pytest.raises(ValueError):
        sa.coerce_value(space, 'sideways')  # not a declared label


def test_coerce_number_within_scale():
    space = {'type': 'number', 'scale': [1, 5]}
    assert sa.coerce_value(space, 3) == 3.0
    assert sa.coerce_value(space, '4') == 4.0  # string from an <input> is fine


def test_coerce_number_rejects_out_of_scale():
    space = {'type': 'number', 'scale': [1, 5]}
    with pytest.raises(ValueError):
        sa.coerce_value(space, 9)
    with pytest.raises(ValueError):
        sa.coerce_value(space, 0)


def test_coerce_number_unbounded_when_no_scale():
    space = {'type': 'number', 'scale': None}
    assert sa.coerce_value(space, 42) == 42.0


def test_coerce_number_rejects_non_number():
    with pytest.raises(ValueError):
        sa.coerce_value({'type': 'number', 'scale': None}, 'nope')


def test_coerce_boolean_rejects_non_bool():
    with pytest.raises(ValueError):
        sa.coerce_value({'type': 'boolean'}, 'true')



# --- build_annotation_record: the pinned annotations.json entry ---------------


def test_record_contract_boolean():
    rec = sa.build_annotation_record(
        space={'type': 'boolean'}, value=True, reason='clearly passes', low_flip_sample=False
    )
    assert rec == {
        'status': 'labeled',
        'value': True,
        'reason': 'clearly passes',
        'low_flip_sample': False,
    }


def test_record_contract_categorical():
    rec = sa.build_annotation_record(
        space={'type': 'categorical', 'labels': ['good', 'bad']},
        value='bad',
        reason='',
        low_flip_sample=True,
    )
    assert rec == {'status': 'labeled', 'value': 'bad', 'reason': '', 'low_flip_sample': True}


def test_record_contract_number():
    rec = sa.build_annotation_record(
        space={'type': 'number', 'scale': [1, 5]}, value='3', reason='mid', low_flip_sample=False
    )
    assert rec['value'] == 3.0 and rec['status'] == 'labeled' and rec['reason'] == 'mid'


def test_record_reason_defaults_to_empty_string():
    rec = sa.build_annotation_record(space={'type': 'boolean'}, value=False, reason=None, low_flip_sample=False)
    assert rec['reason'] == ''


# --- load / write round-trip: keyed by source_index (string) -----------------


def test_write_read_round_trip(tmp_path: Path):
    path = tmp_path / 'annotations.json'
    store = {}
    store['7'] = sa.build_annotation_record(
        space={'type': 'number', 'scale': [1, 5]}, value=4, reason='good enough', low_flip_sample=False
    )
    store['12'] = sa.build_annotation_record(
        space={'type': 'categorical', 'labels': ['a', 'b']}, value='b', reason='', low_flip_sample=True
    )
    sa.write_annotations(path, store)

    back = sa.read_annotations(path)
    assert back == store
    # value types survive the round-trip exactly (number stays float, str stays str)
    assert isinstance(back['7']['value'], float)
    assert isinstance(back['12']['value'], str)


def test_read_missing_file_returns_empty(tmp_path: Path):
    assert sa.read_annotations(tmp_path / 'nope.json') == {}


def test_source_index_is_stringified_key(tmp_path: Path):
    # The contract keys on source_index as a *string*; upsert must normalize an
    # int index to the same key a reload reads.
    path = tmp_path / 'annotations.json'
    store = sa.read_annotations(path)
    rec = sa.build_annotation_record(space={'type': 'boolean'}, value=True, reason='', low_flip_sample=False)
    sa.upsert_annotation(store, source_index=5, record=rec)
    sa.write_annotations(path, store)
    assert '5' in sa.read_annotations(path)
