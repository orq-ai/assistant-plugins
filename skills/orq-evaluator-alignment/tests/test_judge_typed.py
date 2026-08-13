"""Tests for the §5.3 type-aware judge wiring (RES-978): response format per type,
the wrong_output_type -> abstained mapping, and the off-contract tally.

The routing tests drive build_judge_fn with a monkeypatched execute_chat_completion
so no network is touched; they pin the locked decision that an off-contract judge
completion becomes an ABSTAINED prediction (not an error, not a scored verdict).
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

from lib import judge  # noqa: E402


# --- build_response_format: per-type structured-output schema ---


def test_response_format_boolean_is_bool():
    rf = judge.build_response_format('boolean')
    assert rf['json_schema']['schema']['properties']['value']['type'] == 'boolean'


def test_response_format_categorical_has_enum():
    rf = judge.build_response_format('categorical', ['a', 'b', 'c'])
    value = rf['json_schema']['schema']['properties']['value']
    assert value['type'] == 'string'
    assert value['enum'] == ['a', 'b', 'c']


def test_response_format_numeric_is_number():
    rf = judge.build_response_format('number')
    assert rf['json_schema']['schema']['properties']['value']['type'] == 'number'


def test_response_format_string_is_value_only_no_explanation():
    # String evaluators emit ONLY `value` (a free-form string), no explanation.
    schema = judge.build_response_format('string')['json_schema']['schema']
    assert schema['properties']['value']['type'] == 'string'
    assert 'explanation' not in schema['properties']
    assert schema['required'] == ['value']


# --- _count_off_contract: n_wrong = (#None) − repetitions_failed ---


def test_count_off_contract_splits_none_from_failures():
    # 5 reps: 3 decisive values, 2 None; 1 None was a failed call → 1 off-contract.
    assert judge._count_off_contract(['a', 'b', None, 'c', None], repetitions_failed=1) == 1


def test_count_off_contract_zero_when_all_none_are_failures():
    assert judge._count_off_contract([None, None], repetitions_failed=2) == 0


# --- build_judge_fn routing: ok → value, off-contract → abstained ---


def _fake_exec(raw: str):
    async def _exec(**kwargs):  # matches execute_chat_completion's keyword call
        resp = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=raw))]
        )
        return resp, None
    return _exec


_SPEC = judge.JudgeSpec(prompt_template='rate it', replacements={}, temperature=0.0)


def _run(fn):
    return asyncio.run(fn('some-model'))


def test_categorical_ok_returns_label(monkeypatch):
    monkeypatch.setattr(judge, 'execute_chat_completion', _fake_exec('{"explanation":"x","value":"abuse"}'))
    fn = judge.build_judge_fn(_SPEC, client=object(), output_type='categorical', labels=['abuse', 'safe'])
    pred = _run(fn)
    assert pred.value == 'abuse'
    assert pred.abstained is False


def test_categorical_off_contract_abstains(monkeypatch):
    monkeypatch.setattr(judge, 'execute_chat_completion', _fake_exec('totally different label'))
    fn = judge.build_judge_fn(_SPEC, client=object(), output_type='categorical', labels=['abuse', 'safe'])
    pred = _run(fn)
    assert pred.abstained is True
    assert pred.value is None
    assert pred.error is None  # NOT a failed repetition


def test_numeric_ok_returns_number(monkeypatch):
    monkeypatch.setattr(judge, 'execute_chat_completion', _fake_exec('4'))
    fn = judge.build_judge_fn(_SPEC, client=object(), output_type='number', scale=(1.0, 5.0))
    pred = _run(fn)
    assert pred.value == 4.0
    assert pred.abstained is False


def test_numeric_out_of_scale_abstains(monkeypatch):
    monkeypatch.setattr(judge, 'execute_chat_completion', _fake_exec('12'))
    fn = judge.build_judge_fn(_SPEC, client=object(), output_type='number', scale=(1.0, 5.0))
    pred = _run(fn)
    assert pred.abstained is True
    assert pred.error is None


# --- string: value from JSON or whole completion, canonicalized; empty abstains ---


def test_string_ok_from_json_value(monkeypatch):
    monkeypatch.setattr(judge, 'execute_chat_completion', _fake_exec('{"value":"Refund Request"}'))
    fn = judge.build_judge_fn(_SPEC, client=object(), output_type='string')
    pred = _run(fn)
    assert pred.value == 'refund request'  # casefold + whitespace-collapsed
    assert pred.abstained is False


def test_string_ok_from_free_text(monkeypatch):
    # No JSON — the entire completion is the value.
    monkeypatch.setattr(judge, 'execute_chat_completion', _fake_exec('  billing   issue \n'))
    fn = judge.build_judge_fn(_SPEC, client=object(), output_type='string')
    pred = _run(fn)
    assert pred.value == 'billing issue'


def test_string_empty_abstains(monkeypatch):
    monkeypatch.setattr(judge, 'execute_chat_completion', _fake_exec('   '))
    fn = judge.build_judge_fn(_SPEC, client=object(), output_type='string')
    pred = _run(fn)
    assert pred.abstained is True
    assert pred.value is None
    assert pred.error is None
