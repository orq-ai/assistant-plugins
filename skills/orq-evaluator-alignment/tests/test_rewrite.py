"""Tests for Workstream B — the single verdict-space-parameterized rewrite.

Two pure pieces, both offline (no live LLM):

1. `build_verdict_space_context` — given the evaluator's `output_type` plus
   `categorical_labels` / `scale`, it renders the meta-prompt's verdict-space
   template variables (the `{verdict_space}` / `{type_guidance}` /
   `{preservation_rule}` block). This is the per-type parameterization: the
   numeric section is deliberately shallow (anchor-nudging, not calibration).

2. The preservation guard — a rewrite that DROPS a declared categorical label,
   or MOVES the numeric scale bounds, is rejected; and the existing `{{...}}`
   variable-set check is still enforced. The guard is what keeps the verdict
   space intact across a rewrite.

Run:
    cd skills/orq-evaluator-alignment
    "C:/Users/Chiel/anaconda3/Scripts/uv.exe" run --with pytest \
        python -m pytest tests/test_rewrite.py -q
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(SKILL_ROOT), str(SKILL_ROOT / 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402
from loguru import logger  # noqa: E402

import rewrite_eval as rw  # noqa: E402


# --- 1. verdict-space context builder (output_type + labels/scale -> vars) ---


def test_context_boolean_names_both_labels():
    ctx = rw.build_verdict_space_context(
        {'output_type': 'boolean', 'categorical_labels': [], 'scale': None}
    )
    space = ctx['verdict_space'].lower()
    assert 'boolean' in space
    assert 'true' in space and 'false' in space
    # Boolean shares the categorical "pick-a-label" path.
    assert 'pick' in ctx['type_guidance'].lower()
    # The rule must forbid changing the verdict space.
    assert 'true' in ctx['preservation_rule'].lower() and 'false' in ctx['preservation_rule'].lower()


def test_context_categorical_lists_every_declared_label():
    ctx = rw.build_verdict_space_context(
        {'output_type': 'categorical', 'categorical_labels': ['safe', 'abuse', 'spam'], 'scale': None}
    )
    for label in ('safe', 'abuse', 'spam'):
        assert label in ctx['verdict_space']
        assert label in ctx['preservation_rule']
    # K is surfaced so the rewriter knows the label count is fixed.
    assert '3' in ctx['verdict_space']
    assert 'pick' in ctx['type_guidance'].lower()


def test_context_numeric_is_shallow_anchor_nudging():
    ctx = rw.build_verdict_space_context(
        {'output_type': 'number', 'categorical_labels': [], 'scale': [1, 5]}
    )
    space = ctx['verdict_space']
    # Scale endpoints are pinned in the verdict-space description.
    assert '1' in space and '5' in space
    guidance = ctx['type_guidance'].lower()
    # Deliberately shallow: nudge anchor descriptions, and explicitly forbid a
    # calibration model (the word may appear, but only inside a prohibition).
    assert 'anchor' in guidance
    assert 'not' in guidance and 'calibrat' in guidance  # "do NOT build a calibration model"
    # The rule must pin the scale bounds so they can't be moved.
    assert '1' in ctx['preservation_rule'] and '5' in ctx['preservation_rule']


def test_context_numeric_without_scale_still_builds():
    # Scale may be absent (unmeasurable numeric). The context must still render
    # rather than crash — it just can't pin explicit endpoints.
    ctx = rw.build_verdict_space_context(
        {'output_type': 'number', 'categorical_labels': [], 'scale': None}
    )
    assert ctx['verdict_space']
    assert 'anchor' in ctx['type_guidance'].lower()


def test_context_categorical_without_labels_raises():
    # A categorical evaluator with no declared labels cannot define its verdict
    # space — fail loud rather than emit a label-less rubric.
    with pytest.raises(ValueError):
        rw.build_verdict_space_context(
            {'output_type': 'categorical', 'categorical_labels': [], 'scale': None}
        )


# --- 2. the meta prompt actually renders with those variables ---


def test_meta_prompt_renders_with_context():
    ev = {'output_type': 'categorical', 'categorical_labels': ['safe', 'abuse', 'spam'], 'scale': None}
    ctx = rw.build_verdict_space_context(ev)
    rendered = rw.render_meta_prompt(
        instructions='loosen the abuse trigger',
        prompt='Judge it. <output>{{log.output}}</output>',
        context=ctx,
    )
    # No unfilled placeholders left in the rendered meta prompt.
    assert '{verdict_space}' not in rendered and '{type_guidance}' not in rendered
    # The user message keeps the <input_instructions>/<prompt> envelope the
    # rewrite backends key off (and the fake backend echoes).
    assert '<input_instructions>' in rendered and '<prompt>' in rendered
    assert 'safe' in rendered and 'abuse' in rendered


# --- 3. preservation guard: variable set + verdict space ---


def test_guard_passes_on_clean_boolean_rewrite():
    ev = {'output_type': 'boolean', 'categorical_labels': [], 'scale': None, 'variables': ['log.output']}
    proposed = 'Judge harder. Return True if abusive, False otherwise. <output>{{log.output}}</output>'
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert ok, reason


def test_guard_rejects_dropped_categorical_label():
    ev = {
        'output_type': 'categorical',
        'categorical_labels': ['safe', 'abuse', 'spam'],
        'scale': None,
        'variables': ['log.output'],
    }
    # "spam" dropped from the rubric.
    proposed = 'Classify as safe or abuse. <output>{{log.output}}</output>'
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert not ok
    assert 'spam' in reason


def test_guard_rejects_moved_numeric_scale():
    ev = {
        'output_type': 'number',
        'categorical_labels': [],
        'scale': [1, 5],
        'variables': ['log.output'],
    }
    # Rescaled to 1-10: the max moved.
    proposed = 'Rate safety 1-10. <output>{{log.output}}</output>'
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert not ok
    assert '5' in reason  # the missing declared endpoint is named


def test_guard_passes_when_numeric_scale_endpoints_kept():
    ev = {
        'output_type': 'number',
        'categorical_labels': [],
        'scale': [1, 5],
        'variables': ['log.output'],
    }
    proposed = (
        'Rate how safe on a 1 to 5 scale (1 = clearly unsafe, 3 = borderline, '
        '5 = clearly safe). <output>{{log.output}}</output>'
    )
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert ok, reason


def test_guard_still_enforces_variable_set():
    ev = {'output_type': 'boolean', 'categorical_labels': [], 'scale': None, 'variables': ['log.input', 'log.output']}
    # {{log.input}} dropped -> variable-set check must fail even though the
    # (boolean) verdict space is otherwise fine.
    proposed = 'Return True or False. <output>{{log.output}}</output>'
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert not ok
    assert 'log.input' in reason


def test_guard_rejects_added_variable():
    ev = {'output_type': 'boolean', 'categorical_labels': [], 'scale': None, 'variables': ['log.output']}
    proposed = 'Return True or False. <extra>{{sneaky}}</extra> <output>{{log.output}}</output>'
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert not ok
    assert 'sneaky' in reason


def test_guard_numeric_no_scale_skips_scale_check():
    # Unmeasurable numeric (no scale) can't pin endpoints, so only the variable
    # set is enforced — a rewrite must not be blocked for a scale it never had.
    ev = {'output_type': 'number', 'categorical_labels': [], 'scale': None, 'variables': ['log.output']}
    proposed = 'Rate safety on the same scale as before. <output>{{log.output}}</output>'
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert ok, reason


# --- the preservation guard must not pass a moved scale on a substring match ---


def test_moved_scale_is_rejected_even_though_the_digits_still_appear():
    # `b not in proposed` is a substring test: a rewrite that moved [1, 5] to
    # [1, 10] still contains a '1' and a '5' somewhere in the prose, so the guard
    # passed on a verdict space that had, in fact, moved.
    ev = {'variables': ['log.output'], 'output_type': 'number', 'scale': [1, 5]}
    proposed = 'Score {{log.output}} from 1 to 10. Use 10 for a perfect answer.'
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert ok is False
    assert 'MOVED the numeric scale' in reason


def test_scale_endpoint_inside_a_longer_number_does_not_count():
    ev = {'variables': ['log.output'], 'output_type': 'number', 'scale': [1, 5]}
    # '5' appears only as part of '15' and '0.5'.
    proposed = 'Score {{log.output}} from 1 to 15, and treat 0.5 increments as invalid.'
    ok, _reason, _fc = rw.check_preservation(ev, proposed)
    assert ok is False


def test_unmoved_scale_still_passes():
    ev = {'variables': ['log.output'], 'output_type': 'number', 'scale': [1, 5]}
    proposed = 'Score {{log.output}} on a 1 to 5 scale, where 5 is fully grounded.'
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert ok is True
    assert reason == ''


def test_dropped_label_is_rejected_when_only_a_longer_word_contains_it():
    # Same failure one level up: 'spam' is a substring of 'spammy', so a rubric
    # that dropped the label read as preserving it.
    ev = {'variables': ['log.output'], 'output_type': 'categorical',
          'categorical_labels': ['safe', 'spam']}
    proposed = 'Classify {{log.output}} as safe, or flag spammy content as promotional.'
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert ok is False
    assert 'spam' in reason


def test_label_with_punctuation_still_matches_itself():
    ev = {'variables': ['log.output'], 'output_type': 'categorical',
          'categorical_labels': ['abuse (severe)', 'safe']}
    proposed = 'Classify {{log.output}} as abuse (severe) or safe.'
    ok, _reason, _fc = rw.check_preservation(ev, proposed)
    assert ok is True


def test_transition_mention_counts_as_dropped():
    ev = {'variables': ['log.output'], 'output_type': 'categorical',
          'categorical_labels': ['good', 'bad']}
    proposed = (
        'Previously this judge answered good or bad. '
        'From now on, answer only yes or no. {{log.output}}'
    )
    ok, reason, _fc = rw.check_preservation(ev, proposed)
    assert ok is False
    assert 'good' in reason or 'bad' in reason


def test_verdict_space_ok_is_none_when_variables_failed():
    ev = {'output_type': 'categorical', 'categorical_labels': ['a', 'b'],
          'scale': None, 'variables': ['log.output']}
    proposed = 'No variables at all. Answer a or b.'
    _ok, _reason, fc = rw.check_preservation(ev, proposed)
    assert fc == 'variables'
    result = {
        'var_check_passed': False,
        'verdict_space_ok': None if fc == 'variables' else True,
        'failed_check': fc,
    }
    assert result['verdict_space_ok'] is None
    assert result['failed_check'] == 'variables'


def test_context_unknown_type_still_raises():
    with pytest.raises(ValueError):
        rw.build_verdict_space_context({'output_type': 'tensor'})


def test_string_preservation_still_enforces_the_variable_set():
    # The one guard that DOES apply to string: a rewrite that drops {{log.output}}
    # leaves a judge scoring nothing.
    ev = {'output_type': 'string', 'variables': ['log.output'], 'categorical_labels': [], 'scale': None}
    ok, _reason, _fc = rw.check_preservation(ev, 'Summarise the answer: {{log.output}}')
    assert ok is True
    ok, reason, _fc = rw.check_preservation(ev, 'Summarise the answer.')
    assert ok is False
    assert 'log.output' in reason


# --- _read_guidance: warn (not refuse) when the labels moved after aggregation ---


def _capture_warnings():
    messages: list[str] = []
    handler_id = logger.add(lambda msg: messages.append(str(msg)), level='WARNING')
    return messages, handler_id


def test_read_guidance_warns_when_annotations_are_newer_than_aggregated(tmp_path):
    # aggregate.py summarised the labels into aggregated.md; if annotations.json
    # was then touched (re-labelled, a grey-zone policy edited), the summary the
    # rewrite is about to read no longer reflects the latest human input.
    (tmp_path / 'aggregated.md').write_text('## Recommendations\n- be stricter', encoding='utf-8')
    now = time.time()
    os.utime(tmp_path / 'aggregated.md', (now - 100, now - 100))
    (tmp_path / 'annotations.json').write_text('{}', encoding='utf-8')
    os.utime(tmp_path / 'annotations.json', (now, now))

    messages, handler_id = _capture_warnings()
    try:
        rw._read_guidance(tmp_path)
    finally:
        logger.remove(handler_id)

    assert any('annotations.json' in m and 'stale' in m for m in messages)


def test_read_guidance_warns_when_grey_zone_policy_is_newer_than_aggregated(tmp_path):
    (tmp_path / 'aggregated.md').write_text('## Recommendations\n- be stricter', encoding='utf-8')
    now = time.time()
    os.utime(tmp_path / 'aggregated.md', (now - 100, now - 100))
    (tmp_path / 'grey_zone_policy.json').write_text('{}', encoding='utf-8')
    os.utime(tmp_path / 'grey_zone_policy.json', (now, now))

    messages, handler_id = _capture_warnings()
    try:
        rw._read_guidance(tmp_path)
    finally:
        logger.remove(handler_id)

    assert any('grey_zone_policy.json' in m and 'stale' in m for m in messages)


def test_read_guidance_no_warning_when_aggregated_is_the_newest_artifact(tmp_path):
    (tmp_path / 'annotations.json').write_text('{}', encoding='utf-8')
    now = time.time()
    os.utime(tmp_path / 'annotations.json', (now - 100, now - 100))
    (tmp_path / 'aggregated.md').write_text('## Recommendations\n- be stricter', encoding='utf-8')
    os.utime(tmp_path / 'aggregated.md', (now, now))

    messages, handler_id = _capture_warnings()
    try:
        rw._read_guidance(tmp_path)
    finally:
        logger.remove(handler_id)

    assert messages == []
