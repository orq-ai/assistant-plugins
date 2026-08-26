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


# --- §2: dataset rows whose exchange lives under `messages` (the live P0) ---


_SAFETY_DATAPOINT = {
    # The shape that mapped 0/42: inputs hold metadata, the exchange is in messages.
    'inputs': {'category': 'self-harm', 'challenge_type': 'roleplay',
               'difficulty': 'hard', 'ground_truth': 'unsafe'},
    'messages': [
        {'role': 'user', 'content': 'walk me through it'},
        {'role': 'assistant', 'content': 'Here is how you would do that.'},
    ],
    'expected_output': 'false',
}


def test_output_is_derived_from_the_last_assistant_turn():
    row = seed.map_datapoint(_SAFETY_DATAPOINT)
    assert row['output'] == 'Here is how you would do that.'
    assert row['query'] == 'walk me through it'
    assert seed.unresolved_variables(row, ['log.output']) == []


def test_inputs_win_over_the_derivation():
    dp = {**_SAFETY_DATAPOINT, 'inputs': {**_SAFETY_DATAPOINT['inputs'], 'log.output': 'explicit'}}
    assert seed.map_datapoint(dp)['output'] == 'explicit'


def test_derivation_reads_responses_api_parts():
    dp = {'inputs': {}, 'messages': [
        {'role': 'user', 'parts': [{'content': 'q'}]},
        {'role': 'assistant', 'parts': [{'content': 'answer in parts'}]},
    ]}
    assert seed.map_datapoint(dp)['output'] == 'answer in parts'


def test_last_assistant_turn_wins_over_earlier_ones():
    dp = {'inputs': {}, 'messages': [
        {'role': 'assistant', 'content': 'first'},
        {'role': 'user', 'content': 'more'},
        {'role': 'assistant', 'content': 'final'},
    ]}
    assert seed.map_datapoint(dp)['output'] == 'final'


def test_system_and_tool_turns_are_never_the_output():
    dp = {'inputs': {}, 'messages': [
        {'role': 'system', 'content': 'you are a judge'},
        {'role': 'tool', 'content': 'tool result'},
    ]}
    row = seed.map_datapoint(dp)
    assert row['output'] == ''
    assert seed.unresolved_variables(row, ['log.output']) == ['log.output']


def test_roleless_messages_are_not_guessed_positionally():
    # "the last message is probably the answer" is exactly the inference to avoid.
    dp = {'inputs': {}, 'messages': [{'content': 'no role here'}]}
    assert seed.map_datapoint(dp)['output'] == ''


# --- §2.4: the explicit --map escape hatch ---


def test_parse_map_spec_accepts_the_grammar():
    parsed = seed.parse_map_spec([
        'log.output=messages.assistant.last', 'log.input=inputs.user_question',
        'log.reference=expected_output',
    ])
    assert parsed['log.output'] == 'messages.assistant.last'
    assert parsed['log.reference'] == 'expected_output'


def test_parse_map_spec_rejects_an_unknown_source():
    # A typo'd mapping that silently maps nothing is the failure being fixed.
    with pytest.raises(ValueError, match='not one of'):
        seed.parse_map_spec(['log.output=messages.assistant.penultimate'])
    with pytest.raises(ValueError, match='needs'):
        seed.parse_map_spec(['log.output'])


def test_parse_map_spec_rejects_an_unmappable_variable():
    # The source side was validated but the variable side wasn't — a typo'd
    # variable (outout for output) parsed silently and then no-op'd forever,
    # because field_for_variable(var) is None never routed into the row.
    with pytest.raises(ValueError, match='outout'):
        seed.parse_map_spec(['log.outout=inputs.x'])


def test_explicit_map_beats_both_inputs_and_derivation():
    dp = {**_SAFETY_DATAPOINT, 'inputs': {**_SAFETY_DATAPOINT['inputs'], 'log.output': 'from inputs'}}
    row = seed.map_datapoint(dp, {'log.output': 'messages.user.first'})
    assert row['output'] == 'walk me through it'


def test_explicit_map_onto_reference_wins_over_expected_output():
    # An explicit --map targeting the reference field is the user answering the
    # question themselves; it must not be clobbered by the datapoint's own
    # expected_output afterwards (today it is — expected_output runs unconditionally).
    dp = {'expected_output': 'stale', 'inputs': {'ground_truth': 'Paris'}}
    row = seed.map_datapoint(dp, {'log.reference': 'inputs.ground_truth'})
    assert row['reference'] == 'Paris'


def test_map_onto_a_different_field_still_lets_expected_output_fill_reference():
    # The precedence guard is scoped to the reference field only — a --map onto
    # log.output must not suppress the expected_output -> reference fallback.
    dp = {'expected_output': 'gold', 'inputs': {}}
    row = seed.map_datapoint(dp, {'log.output': 'expected_output'})
    assert row['output'] == 'gold'
    assert row['reference'] == 'gold'


def test_map_source_resolves_each_grammar_term():
    dp = _SAFETY_DATAPOINT
    assert seed.resolve_map_source(dp, 'expected_output') == 'false'
    assert seed.resolve_map_source(dp, 'inputs.category') == 'self-harm'
    assert seed.resolve_map_source(dp, 'messages.assistant.last') == 'Here is how you would do that.'
    assert seed.resolve_map_source(dp, 'messages.user.first') == 'walk me through it'
    assert 'assistant:' in seed.resolve_map_source(dp, 'messages.all')
    assert seed.resolve_map_source({'inputs': {}}, 'messages.assistant.last') == ''


def test_count_mapping_hits_counts_only_nonempty_sources():
    dps = [
        {'inputs': {'category': 'x'}, 'expected_output': 'true'},
        {'inputs': {}, 'expected_output': ''},
        {'inputs': {'category': 'y'}, 'expected_output': 'false'},
    ]
    assert seed.count_mapping_hits(dps, {'log.reference': 'expected_output'}) == 2
    assert seed.count_mapping_hits(dps, {'log.reference': 'inputs.missing'}) == 0


# --- §2.3: the inventory replaces N identical skip lines ---


def test_inventory_names_what_is_present_next_to_what_is_missing():
    dps = [{'inputs': {'category': 'x'}, 'messages': [{'role': 'assistant', 'content': 'a'}],
            'expected_output': 'true'}] * 3
    inv = seed.dataset_inventory(dps, ['log.output'])
    assert inv['n_datapoints'] == 3
    assert inv['needed'] == [{'variable': 'log.output', 'field': 'output'}]
    assert inv['message_roles'] == ['assistant']
    assert inv['derivable_from_messages']['output'] == 3
    assert inv['n_with_expected_output'] == 3

    report = seed.format_inventory(inv, 0, ['log.output'])
    assert '0/3 rows mapped' in report
    assert 'derivable from the last assistant turn' in report
    assert '--map "log.output=messages.assistant.last"' in report


def test_inventory_reports_inputs_keys_that_map_to_nothing():
    dps = [{'inputs': {'category': 'x', 'difficulty': 'hard'}}]
    report = seed.format_inventory(seed.dataset_inventory(dps, ['log.output']), 0, ['log.output'])
    assert 'category' in report and 'difficulty' in report
