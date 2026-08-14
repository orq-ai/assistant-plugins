"""Unit tests for lib.seed — the pure orq-datapoint ↔ trace-row mapping (RES-980 §11.4).

Data seeding pulls rows from an orq dataset (or conductor-generated synthetic
datapoints) into the alignment pipeline. orq datapoints are
`{inputs, messages, expected_output}`; the pipeline's trace rows are
`{query, output, messages, reference, ...}`. These pin the suffix-rule mapping
(mirrors `fetch_traces._assign_io`) and the variable resolution/validation.

Pure stdlib — safe to import directly on Windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

from lib import seed  # noqa: E402


def test_map_datapoint_maps_inputs_by_suffix():
    dp = {'inputs': {'log.input': 'hi', 'log.output': 'hello'}, 'messages': None, 'expected_output': 'ref'}
    row = seed.map_datapoint(dp)
    assert row['query'] == 'hi'
    assert row['output'] == 'hello'
    assert row['reference'] == 'ref'
    assert row['messages'] is None


def test_map_datapoint_uses_toplevel_messages():
    dp = {'inputs': {'query': 'q'}, 'messages': [{'role': 'user', 'content': 'hi'}]}
    row = seed.map_datapoint(dp)
    assert row['messages'] == [{'role': 'user', 'content': 'hi'}]


def test_map_datapoint_reads_conversation_input_suffix():
    dp = {'inputs': {'conversation': [{'role': 'user', 'content': 'x'}], 'response': 'y'}}
    row = seed.map_datapoint(dp)
    assert row['messages'] == [{'role': 'user', 'content': 'x'}]
    assert row['output'] == 'y'


# --- variable resolution / validation ---


def test_unresolved_variables_empty_when_row_satisfies_all():
    row = {'query': 'q', 'output': 'o', 'messages': None, 'reference': ''}
    assert seed.unresolved_variables(row, ['log.input', 'log.output']) == []


def test_unresolved_variables_flags_empty_field_and_unknown_leaf():
    row = {'query': 'q', 'output': '', 'messages': None, 'reference': ''}
    # log.output → empty output field; extra.context → unknown leaf (unfillable).
    missing = seed.unresolved_variables(row, ['log.input', 'log.output', 'extra.context'])
    assert missing == ['log.output', 'extra.context']


def test_unresolved_variables_resolves_reference_leaves():
    """A `reference`-family variable is satisfied by the row's reference field.

    `judge.make_replacements` fills `reference | expected | expected_output` from
    `row['reference']`, so this function must agree — otherwise an evaluator that
    declares `{{log.reference}}` renders correctly at judge time while every one of
    its dataset rows is silently skipped at pull time.
    """
    row = {'query': 'q', 'output': 'o', 'messages': None, 'reference': 'gold'}
    variables = ['log.input', 'log.output', 'log.reference', 'expected', 'expected_output']
    assert seed.unresolved_variables(row, variables) == []


def test_unresolved_variables_flags_reference_leaf_when_row_has_none():
    row = {'query': 'q', 'output': 'o', 'messages': None, 'reference': ''}
    assert seed.unresolved_variables(row, ['log.reference']) == ['log.reference']


def test_rows_from_datapoints_keeps_rows_for_a_reference_declaring_evaluator():
    """The end-to-end consequence of the bug: rows must not be dropped."""
    datapoints = [{'inputs': {'log.input': 'q', 'log.output': 'o'}, 'expected_output': 'gold'}]
    rows, skipped = seed.rows_from_datapoints(datapoints, ['log.input', 'log.output', 'log.reference'])
    assert skipped == []
    assert len(rows) == 1
    assert rows[0]['reference'] == 'gold'


# --- save-back direction: row → orq datapoint ---


def test_row_to_datapoint_keys_inputs_by_variable_names():
    row = {'query': 'hi', 'output': 'hello', 'messages': None, 'reference': 'ref'}
    dp = seed.row_to_datapoint(row, ['log.input', 'log.output'])
    assert dp['inputs'] == {'log.input': 'hi', 'log.output': 'hello'}
    assert dp['expected_output'] == 'ref'
    assert 'messages' not in dp  # None messages omitted


# --- batch conversion (shared by dataset_inputs.py + seed_inputs.py) ---


def test_rows_from_datapoints_maps_tags_and_skips_unusable():
    dps = [
        {'inputs': {'log.input': 'a', 'log.output': 'b'}, 'expected_output': '', 'rationale': 'edge'},
        {'inputs': {'log.input': 'only input'}, 'expected_output': ''},  # no output → skipped
    ]
    rows, skipped = seed.rows_from_datapoints(dps, ['log.input', 'log.output'], tag={'synthetic': True})
    assert len(rows) == 1
    assert rows[0]['query'] == 'a' and rows[0]['output'] == 'b'
    assert rows[0]['synthetic'] is True
    assert rows[0]['rationale'] == 'edge'
    assert skipped == [{'index': 1, 'missing': ['log.output']}]


def test_every_judge_fillable_leaf_is_resolvable_by_seed():
    # The anti-drift test. seed.py used to keep its own copy of the suffix rules
    # and "mirror" lib.judge by hand; the copy lost the reference family, which
    # silently skipped every row of an evaluator declaring {{log.reference}}.
    # Both sides now read lib.content.field_for_variable, so this asserts the
    # property that failure violated: every leaf the judge can fill, seed accepts.
    from lib.content import _FIELD_BY_LEAF

    row = {'query': 'q', 'output': 'o', 'messages': [{'role': 'user', 'content': 'hi'}], 'reference': 'r'}
    variables = [f'log.{leaf}' for leaf in _FIELD_BY_LEAF]

    assert seed.unresolved_variables(row, variables) == []
    # And a leaf outside the table is still reported, not silently accepted.
    assert seed.unresolved_variables(row, ['log.rubric']) == ['log.rubric']


def test_map_datapoint_fills_reference_from_an_inputs_key_too():
    # `expected_output` is the canonical ground-truth field, but the shared table
    # also maps {{...expected_output}} / {{...reference}} appearing under `inputs`
    # — that route must not be blanked by an absent top-level expected_output.
    row = seed.map_datapoint({'inputs': {'log.output': 'o', 'log.reference': 'gold'}})

    assert row['reference'] == 'gold'
    assert seed.unresolved_variables(row, ['log.output', 'log.reference']) == []
