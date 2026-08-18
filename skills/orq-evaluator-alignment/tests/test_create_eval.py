"""Tests for the type-parameterized create request body (RES-978 Part 2, §2.4).

Workstream C: `OrqClient.create_boolean_evaluator` generalises to
`create_evaluator(..., output_type, categorical_labels=None, scale=None)` over
httpx `POST /v2/evaluators`. The body assembly is factored into a pure function
(`build_create_body`) so the per-type request shape is unit-testable with no
network.

Pins the three verdict types against the real record shapes (§8.1):
  - boolean   → output_type='boolean' + a boolean guardrail_config;
  - categorical → output_type='categorical' + categorical_labels sent in orq's
    rich ``[{value, description}]`` shape (the field name orq's record uses);
  - numeric   → output_type='number', and NO scale field is invented when the
    record carries none (scale is passed through only if present).

Guards: creating from a categorical source must include its labels; from a
numeric source must set output_type='number'.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

from lib import orq_client  # noqa: E402

build = orq_client.build_create_body


# --- boolean: output_type + boolean guardrail, no categorical_labels/scale ---


def test_boolean_body_shape():
    body = build(
        key='k-aligned',
        path='Evaluators/k',
        prompt='PROMPT',
        model='deepseek-v4-flash',
        description='desc',
        output_type='boolean',
    )
    assert body['type'] == 'llm_eval'
    assert body['mode'] == 'single'
    assert body['model'] == 'deepseek-v4-flash'
    assert body['prompt'] == 'PROMPT'
    assert body['output_type'] == 'boolean'
    assert body['path'] == 'Evaluators/k'
    assert body['key'] == 'k-aligned'
    assert body['description'] == 'desc'
    # Boolean carries a boolean guardrail; never a label set or a scale.
    assert body['guardrail_config']['type'] == 'boolean'
    assert 'categorical_labels' not in body
    assert 'scale' not in body


def test_boolean_omits_description_when_none():
    body = build(
        key='k', path='p', prompt='x', model='m', description=None, output_type='boolean'
    )
    assert 'description' not in body


# --- display_name: the created evaluator reuses the source's human-readable name ---


def test_display_name_sent_when_provided():
    # orq shows `display_name` as the evaluator's name; without it, the ugly `key`
    # (`<source>-aligned-<ts>`) shows instead. Pass the source's name through.
    body = build(
        key='sec-aligned-123', path='p', prompt='x', model='m', description=None,
        output_type='boolean', display_name='Harmful or Illegal content',
    )
    assert body['display_name'] == 'Harmful or Illegal content'


def test_display_name_omitted_when_none():
    body = build(
        key='k', path='p', prompt='x', model='m', description=None, output_type='boolean',
    )
    assert 'display_name' not in body


# Naming and placement of the aligned copy live in tests/test_aligned_naming.py —
# `source_name`, `aligned_key`, and `_resolve_path`. orq overwrites the
# `display_name` we send with the `key`, so the key is what the user reads.


# --- categorical: labels sent in orq's rich [{value, description}] shape ---


def test_categorical_body_includes_labels_rich_shape():
    body = build(
        key='cat-aligned',
        path='Evaluators/cat',
        prompt='CLASSIFY',
        model='m',
        description='d',
        output_type='categorical',
        categorical_labels=['safe', 'abuse', 'spam'],
    )
    assert body['output_type'] == 'categorical'
    # orq's record shape: a list of {value, description} objects.
    assert body['categorical_labels'] == [
        {'value': 'safe', 'description': ''},
        {'value': 'abuse', 'description': ''},
        {'value': 'spam', 'description': ''},
    ]
    # A flat `categories` mirror comes along too (the record carries both).
    assert body['categories'] == ['safe', 'abuse', 'spam']
    # Categorical guardrail, not boolean.
    assert body['guardrail_config']['type'] == 'categorical'
    assert 'scale' not in body


def test_categorical_guardrail_includes_values_defaulting_to_labels():
    # orq's categorical guardrail_config requires a `values` array (the labels that
    # "pass" the guardrail). With no source set supplied, default to the full label
    # set so POST /v2/evaluators does not 400 (ZodError: values expected array,
    # received undefined).
    body = build(
        key='c', path='p', prompt='x', model='m', description=None,
        output_type='categorical', categorical_labels=['safe', 'abuse', 'spam'],
    )
    assert body['guardrail_config']['values'] == ['safe', 'abuse', 'spam']


def test_categorical_guardrail_preserves_source_values():
    # When the source evaluator declared which labels "pass", preserve that set
    # rather than defaulting to all labels (keeps the original guardrail semantics).
    body = build(
        key='c', path='p', prompt='x', model='m', description=None,
        output_type='categorical', categorical_labels=['Safe', 'Neutral', 'Unsafe'],
        guardrail_values=['Safe', 'Neutral'],
    )
    assert body['guardrail_config']['values'] == ['Safe', 'Neutral']


def test_categorical_preserves_rich_labels_when_given_dicts():
    # If the source already carries {value, description}, keep the descriptions.
    body = build(
        key='c',
        path='p',
        prompt='x',
        model='m',
        description=None,
        output_type='categorical',
        categorical_labels=[
            {'value': 'value 1', 'description': 'desc 1'},
            {'value': 'value 2', 'description': 'desc 2'},
        ],
    )
    assert body['categorical_labels'] == [
        {'value': 'value 1', 'description': 'desc 1'},
        {'value': 'value 2', 'description': 'desc 2'},
    ]
    assert body['categories'] == ['value 1', 'value 2']


def test_categorical_guard_requires_labels():
    # Creating a categorical evaluator with no labels is a programming error:
    # the verdict space would be undefined. Refuse rather than send an empty set.
    with pytest.raises(ValueError):
        build(
            key='c', path='p', prompt='x', model='m', description=None,
            output_type='categorical', categorical_labels=[],
        )


# --- numeric: output_type='number', scale only when present, never invented ---


def test_numeric_body_sets_number_type_no_invented_scale():
    body = build(
        key='num-aligned',
        path='Evaluators/num',
        prompt='RATE',
        model='m',
        description=None,
        output_type='number',
        scale=None,
    )
    assert body['output_type'] == 'number'
    # No scale in the record → DON'T invent one.
    assert 'scale' not in body
    # No label set on a numeric evaluator.
    assert 'categorical_labels' not in body


def test_numeric_body_never_sends_scale():
    # The evaluator schema has no scale field and the API drops it, so sending one
    # only made the request look like it carried a guarantee it never had. A numeric
    # judge's scale lives in its rubric text, which check_preservation gates on.
    body = build(
        key='n', path='p', prompt='x', model='m', description=None,
        output_type='number', scale=[1, 5],
    )
    assert body['output_type'] == 'number'
    assert 'scale' not in body


def test_numeric_ignores_stray_categorical_labels():
    # A numeric source may carry categorical_labels=[] in its evaluator.json;
    # that must never leak a label field onto a number evaluator.
    body = build(
        key='n', path='p', prompt='x', model='m', description=None,
        output_type='number', categorical_labels=[], scale=[1, 5],
    )
    assert 'categorical_labels' not in body
    assert 'categories' not in body


# --- unknown type is rejected (only the three supported verdict spaces) ---


def test_unknown_output_type_rejected():
    with pytest.raises(ValueError):
        build(
            key='k', path='p', prompt='x', model='m', description=None,
            output_type='freeform',
        )


# --- _create forwards the source scale for BOTH numeric spellings ---


class _FakeResult:
    def __init__(self) -> None:
        self.id, self.key, self.raw = 'new-id', 'new-key', {}


class _FakeClient:
    """Records the kwargs create_evaluator was called with (no network)."""

    captured: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def create_evaluator(self, **kwargs):
        _FakeClient.captured = kwargs
        return _FakeResult()


@pytest.mark.parametrize('spelling', ['number', 'numeric'])
def test_create_forwards_scale_for_both_numeric_spellings(monkeypatch, spelling):
    import asyncio

    import create_eval

    monkeypatch.setattr(create_eval, 'OrqClient', _FakeClient)
    evaluator = {
        'id': 'src-1',
        'judge_model': 'm',
        'output_type': spelling,
        'scale': [1, 10],
        'name': 'Quality',
    }
    asyncio.run(create_eval._create(evaluator, prompt='RATE', key='num-aligned', path='Evaluators/num'))
    # A source stored as 'numeric' must not silently drop its scale on creation.
    assert _FakeClient.captured['scale'] == [1, 10]
    assert _FakeClient.captured['output_type'] == spelling


# --- create-time guards: all three checks, not just var_check_passed (RES-978 §5.1) ---


def test_enforce_create_guards_refuses_on_a_failed_check_not_covered_by_var_check():
    # rewrite_eval now writes verdict_space_ok/preservation_ok alongside
    # var_check_passed, but nothing read them — a rewrite whose preservation
    # guard failed still created cleanly as long as the variable set matched.
    import create_eval

    status = {'var_check_passed': True, 'verdict_space_ok': True, 'preservation_ok': False}
    with pytest.raises(SystemExit, match='preservation_ok'):
        create_eval._enforce_create_guards(status, force=False)


def test_enforce_create_guards_force_overrides_and_names_what_was_forced():
    import create_eval

    status = {'var_check_passed': True, 'verdict_space_ok': True, 'preservation_ok': False}
    forced = create_eval._enforce_create_guards(status, force=True)
    assert forced == ['preservation_ok']


def test_enforce_create_guards_defaults_missing_keys_to_passing():
    # A pre-existing run dir written before verdict_space_ok/preservation_ok
    # existed has neither key — absence must not block creation.
    import create_eval

    status = {'var_check_passed': True}
    assert create_eval._enforce_create_guards(status, force=False) == []


def test_enforce_create_guards_passes_when_everything_is_true():
    import create_eval

    status = {'var_check_passed': True, 'verdict_space_ok': True, 'preservation_ok': True}
    assert create_eval._enforce_create_guards(status, force=False) == []
