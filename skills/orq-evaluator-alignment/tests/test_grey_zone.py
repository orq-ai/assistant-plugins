"""Unit tests for lib.grey_zone — the pure grey-zone assessment helpers (RES-980).

Covers the three pure pieces the conductor stage leans on:
- `assemble_payload`   — queue.json → compact confuser payload for the conductor's
  context (verdict split + band + one representative rationale + truncated input).
- policy contract       — `validate_policy` + `policy_to_guidance` (→ aggregated.md
  free-text that rewrite_eval already consumes) + `policy_labels` (→ the
  annotations-shaped per-point labels that retest consumes).

All pure stdlib (no evaluatorq/orq import), safe to import directly on Windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

from lib import grey_zone  # noqa: E402


def _boolean_queue() -> dict:
    """A minimal queue.json with a single boolean confuser (3T/2F)."""
    verdict_space = {'type': 'boolean', 'labels': [False, True]}
    return {
        'meta': {'verdict_space': verdict_space, 'eval_prompt': 'Judge: {{output}}', 'n_items': 1},
        'items': [
            {
                'rank': 1,
                'source_index': 7,
                'low_flip_sample': False,
                'query': 'is this useless?',
                'output': 'the whole rendered judge prompt',
                'variables': [{'name': 'log.output', 'value': 'this is useless'}],
                'messages': None,
                'verdict_space': verdict_space,
                'ambiguity': {'instability': 0.96, 'band': 'unreliable', 'flip_rate': 0.8,
                              'mode_value': True, 'mode_rate': 0.6, 'n_repeats': 5},
                'judge_votes': {'n_true': 3, 'n_false': 2, 'counts': None, 'mean': None,
                                'stdev': None, 'representative_explanation': 'borderline sarcasm'},
            }
        ],
    }


# --- assemble_payload: verdict split (boolean) ---


def test_assemble_payload_boolean_verdict_split():
    payload = grey_zone.assemble_payload(_boolean_queue())

    assert payload['n_confusers'] == 1
    (confuser,) = payload['confusers']
    assert confuser['source_index'] == 7
    assert confuser['band'] == 'unreliable'
    assert confuser['verdict_split'] == {'n_true': 3, 'n_false': 2}


def _item(source_index: int, verdict_space: dict, votes: dict, **over) -> dict:
    item = {
        'rank': 1, 'source_index': source_index, 'low_flip_sample': False,
        'query': 'q', 'output': 'rendered prompt', 'variables': None, 'messages': None,
        'verdict_space': verdict_space,
        'ambiguity': {'instability': 0.5, 'band': 'noisy', 'n_repeats': 5},
        'judge_votes': votes,
    }
    item.update(over)
    return item


def test_assemble_payload_categorical_verdict_split():
    vs = {'type': 'categorical', 'labels': ['safe', 'abuse', 'spam'], 'k': 3}
    queue = {'meta': {'verdict_space': vs}, 'items': [
        _item(2, vs, {'counts': {'abuse': 3, 'safe': 2}, 'representative_explanation': 'r'})
    ]}
    (confuser,) = grey_zone.assemble_payload(queue)['confusers']
    assert confuser['verdict_split'] == {'counts': {'abuse': 3, 'safe': 2}}


def test_assemble_payload_numeric_verdict_split():
    vs = {'type': 'number', 'scale': [1, 5]}
    queue = {'meta': {'verdict_space': vs}, 'items': [
        _item(3, vs, {'mean': 3.2, 'stdev': 1.1, 'representative_explanation': 'r'})
    ]}
    (confuser,) = grey_zone.assemble_payload(queue)['confusers']
    assert confuser['verdict_split'] == {'mean': 3.2, 'stdev': 1.1}


def test_assemble_payload_top_k_caps_confusers():
    vs = {'type': 'boolean', 'labels': [False, True]}
    votes = {'n_true': 3, 'n_false': 2, 'representative_explanation': 'r'}
    queue = {'meta': {'verdict_space': vs},
             'items': [_item(i, vs, votes) for i in range(5)]}
    payload = grey_zone.assemble_payload(queue, top_k=2)
    assert payload['n_confusers'] == 2
    assert [c['source_index'] for c in payload['confusers']] == [0, 1]


def test_assemble_payload_input_prefers_variables():
    payload = grey_zone.assemble_payload(_boolean_queue())
    (confuser,) = payload['confusers']
    # The judged input, not the whole rendered prompt.
    assert 'log.output' in confuser['input']
    assert 'this is useless' in confuser['input']
    assert 'rendered judge prompt' not in confuser['input']


def test_assemble_payload_truncates_long_input():
    vs = {'type': 'boolean', 'labels': [False, True]}
    long_val = 'x' * 5000
    item = _item(1, vs, {'n_true': 3, 'n_false': 2, 'representative_explanation': 'r'},
                 variables=[{'name': 'log.output', 'value': long_val}])
    queue = {'meta': {'verdict_space': vs}, 'items': [item]}
    (confuser,) = grey_zone.assemble_payload(queue, max_chars=200)['confusers']
    # The budget bounds the RENDERED block (§12.5): the elision marker and the
    # `name: ` prefix are charged against it, so the conductor's ceiling is the one
    # the caller set rather than that plus whatever the formatting cost.
    assert confuser['input_chars_shown'] <= 200
    assert len(confuser['input']) <= 200
    assert 'chars elided' in confuser['input']


# --- context budget: fair-share windowing + elision accounting (§12) ---


def _sized_queue(*variables: tuple[str, str], reasoning: str = 'r') -> dict:
    """A one-confuser queue whose judged input is the given `(name, value)` vars."""
    vs = {'type': 'boolean', 'labels': [False, True]}
    item = _item(
        1, vs,
        {'n_true': 3, 'n_false': 2, 'representative_explanation': reasoning},
        variables=[{'name': n, 'value': v} for n, v in variables],
    )
    return {'meta': {'verdict_space': vs}, 'items': [item]}


def test_fair_share_does_not_starve_a_small_variable():
    # The v1 failure: variables were concatenated then head-truncated, so a long
    # {{query}} consumed the whole budget and {{output}} — the thing actually
    # judged — never reached the conductor at all.
    queue = _sized_queue(('query', 'q' * 5000), ('output', 'SHORT-OUTPUT'))
    (confuser,) = grey_zone.assemble_payload(queue, max_chars=200)['confusers']
    assert 'SHORT-OUTPUT' in confuser['input']


def test_window_keeps_both_head_and_tail_of_a_long_value():
    # Head-only truncation loses the final assistant turn, which is usually the
    # part the judge actually scored.
    queue = _sized_queue(('output', 'HEAD-MARKER' + 'x' * 5000 + 'TAIL-MARKER'))
    (confuser,) = grey_zone.assemble_payload(queue, max_chars=200)['confusers']
    assert 'HEAD-MARKER' in confuser['input']
    assert 'TAIL-MARKER' in confuser['input']


def test_truncated_input_reports_how_much_was_elided():
    queue = _sized_queue(('output', 'x' * 5000))
    (confuser,) = grey_zone.assemble_payload(queue, max_chars=200)['confusers']
    assert confuser['input_truncated'] is True
    assert confuser['input_chars_original'] == 5000
    assert confuser['input_chars_shown'] < confuser['input_chars_original']
    assert confuser['input_chars_shown'] > 0


def test_short_input_reports_no_elision():
    queue = _sized_queue(('output', 'tiny'))
    (confuser,) = grey_zone.assemble_payload(queue, max_chars=200)['confusers']
    assert confuser['input_truncated'] is False
    assert confuser['input_chars_shown'] == confuser['input_chars_original'] == 4


def test_long_reasoning_is_accounted_separately():
    queue = _sized_queue(('output', 'tiny'), reasoning='y' * 5000)
    (confuser,) = grey_zone.assemble_payload(queue, max_chars=200)['confusers']
    assert confuser['reasoning_truncated'] is True
    assert confuser['reasoning_chars_original'] == 5000
    assert confuser['reasoning_chars_shown'] < 5000


# --- context budget: token estimate + clamp (§12.6) ---


def _many_confusers(n: int, chars: int = 2000) -> dict:
    vs = {'type': 'boolean', 'labels': [False, True]}
    votes = {'n_true': 3, 'n_false': 2, 'representative_explanation': 'r'}
    items = [
        _item(i, vs, votes, variables=[{'name': 'output', 'value': f'{i}' * chars}])
        for i in range(n)
    ]
    return {'meta': {'verdict_space': vs}, 'items': items}


def test_assemble_clamps_confusers_to_the_token_budget():
    payload = grey_zone.assemble_payload(_many_confusers(20), max_chars=2000, max_tokens=2000)

    assert payload['n_confusers'] < 20
    assert payload['budget']['estimated_tokens'] <= 2000
    assert payload['budget']['within_budget'] is True
    assert payload['budget']['n_dropped_by_budget'] == 20 - payload['n_confusers']


def test_clamp_keeps_the_most_unstable_prefix():
    # queue.json is already most-unstable-first, so the clamp must keep a prefix —
    # dropping from the middle would silently discard the highest-signal points.
    payload = grey_zone.assemble_payload(_many_confusers(20), max_chars=2000, max_tokens=2000)
    kept = [c['source_index'] for c in payload['confusers']]
    assert kept == list(range(len(kept)))


def test_budget_block_totals_the_elision():
    payload = grey_zone.assemble_payload(_many_confusers(3, chars=5000), max_chars=200)
    assert payload['budget']['n_truncated'] == 3
    # Every elided character is accounted for: shown + elided == original, per row.
    accounted = sum(
        (c['input_chars_original'] - c['input_chars_shown'])
        + (c['reasoning_chars_original'] - c['reasoning_chars_shown'])
        for c in payload['confusers']
    )
    assert payload['budget']['total_chars_elided'] == accounted
    assert accounted >= 3 * (5000 - 200)  # at least the raw overshoot; markers cost more


def test_rendered_input_respects_max_chars():
    # The elision marker and the `name: ` labels used to be added ON TOP of the
    # budget, so a 200-char ceiling rendered ~4x that and the caller's cap was
    # not the cap it got.
    payload = grey_zone.assemble_payload(_structured_row_queue(), max_chars=200)
    for confuser in payload['confusers']:
        assert len(confuser['input']) <= 200


def test_keeps_one_confuser_even_when_it_alone_exceeds_the_budget():
    # Reporting "over budget" beats writing an empty item list the conductor
    # would read as "no confusers found" (§12.6).
    payload = grey_zone.assemble_payload(_many_confusers(5), max_chars=2000, max_tokens=1)
    assert payload['n_confusers'] == 1
    assert payload['budget']['within_budget'] is False


def test_no_budget_means_no_clamp():
    payload = grey_zone.assemble_payload(_many_confusers(20), max_chars=2000)
    assert payload['n_confusers'] == 20
    assert payload['budget']['n_dropped_by_budget'] == 0


def test_top_k_sentinel_minus_one_is_all_and_zero_is_none():
    # -1 is the all-sentinel used everywhere in this skill (num_samples, count);
    # 0 means none. Pinned because config.toml now ships grey_zone_top_k = -1.
    assert grey_zone.assemble_payload(_many_confusers(5), top_k=-1)['n_confusers'] == 5
    assert grey_zone.assemble_payload(_many_confusers(5), top_k=0)['n_confusers'] == 0


# --- grey_zone_policy.json contract: validate / labels / guidance ---


def _boolean_policy() -> dict:
    """A well-formed grey_zone_policy.json for a boolean evaluator."""
    return {
        'output_type': 'boolean',
        'verdict_space': {'type': 'boolean', 'labels': [False, True]},
        'grey_zones': [
            {
                'id': 'gz1',
                'question': 'Does borderline sarcasm aimed at a group count as abuse?',
                'answer': 'Yes — sarcasm targeting a protected group is abuse.',
                'rule': 'Treat sarcasm aimed at a protected group as abuse.',
                'member_source_indices': [7, 12],
            }
        ],
        'labels': [
            {'source_index': 7, 'value': True, 'grey_zone_id': 'gz1'},
            {'source_index': 12, 'value': False, 'grey_zone_id': 'gz1'},
        ],
    }


def test_validate_policy_accepts_wellformed_boolean():
    grey_zone.validate_policy(_boolean_policy())  # must not raise


def test_validate_policy_rejects_missing_output_type():
    policy = _boolean_policy()
    del policy['output_type']
    with pytest.raises(ValueError, match='output_type'):
        grey_zone.validate_policy(policy)


def test_validate_policy_rejects_label_without_value():
    policy = _boolean_policy()
    policy['labels'][0].pop('value')
    with pytest.raises(ValueError, match='value'):
        grey_zone.validate_policy(policy)


def test_validate_policy_requires_numeric_tolerance():
    # Numeric policy labels are target_score + tolerance (§3.5); a missing band is
    # an error, not a silent exact-match.
    policy = {
        'output_type': 'number',
        'verdict_space': {'type': 'number', 'scale': [1, 5]},
        'grey_zones': [],
        'labels': [{'source_index': 3, 'value': 4.0, 'grey_zone_id': None}],
    }
    with pytest.raises(ValueError, match='tolerance'):
        grey_zone.validate_policy(policy)


def test_policy_labels_boolean_matches_annotations_shape():
    # retest reads {str(source_index): {value: ...}} — the annotations.json shape.
    labels = grey_zone.policy_labels(_boolean_policy())
    assert labels == {'7': {'value': True}, '12': {'value': False}}


def test_policy_labels_numeric_carries_tolerance():
    policy = {
        'output_type': 'number',
        'verdict_space': {'type': 'number', 'scale': [1, 5]},
        'grey_zones': [],
        'labels': [{'source_index': 3, 'value': 4.0, 'tolerance': 1.0, 'grey_zone_id': None}],
    }
    assert grey_zone.policy_labels(policy) == {'3': {'value': 4.0, 'tolerance': 1.0}}


def test_policy_to_guidance_carries_rule_and_answer():
    # rewrite_eval consumes this as free-text guidance (aggregated.md), unchanged.
    md = grey_zone.policy_to_guidance(_boolean_policy())
    assert 'Treat sarcasm aimed at a protected group as abuse.' in md
    assert 'Yes — sarcasm targeting a protected group is abuse.' in md


# --- low-flip spot-check rows are NOT confusers to open-code ---


def _mixed_queue(n_flipped: int = 3, n_low_flip: int = 5) -> dict:
    """A queue shaped like build_queue's real output: the count the user chose,
    plus the `low_flip_sample_size` stable spot-check rows appended after it."""
    verdict_space = {'type': 'boolean', 'labels': [False, True]}

    def _item(idx: int, *, low_flip: bool) -> dict:
        return {
            'rank': idx,
            'source_index': idx,
            'low_flip_sample': low_flip,
            'reason': 'low_flip' if low_flip else 'instability',
            'query': f'q{idx}',
            'output': f'rendered prompt {idx}',
            'variables': [{'name': 'log.output', 'value': f'out{idx}'}],
            'messages': None,
            'verdict_space': verdict_space,
            'ambiguity': {
                'instability': 0.0 if low_flip else 0.9,
                'band': 'stable' if low_flip else 'unreliable',
                'flip_rate': 0.0 if low_flip else 0.8,
                'mode_value': True, 'mode_rate': 1.0, 'n_repeats': 5,
            },
            'judge_votes': {'n_true': 5 if low_flip else 3, 'n_false': 0 if low_flip else 2,
                            'counts': None, 'mean': None, 'stdev': None,
                            'representative_explanation': f'why {idx}'},
        }

    items = [_item(i, low_flip=False) for i in range(n_flipped)]
    items += [_item(100 + i, low_flip=True) for i in range(n_low_flip)]
    return {'meta': {'verdict_space': verdict_space, 'eval_prompt': 'Judge: {{output}}',
                     'n_items': len(items)}, 'items': items}


def test_low_flip_rows_are_excluded_by_default():
    # The bug this pins: asking to review 3 examples put 3 + low_flip_sample_size
    # (5) into the conductor's context, silently overriding the step-5 count.
    payload = grey_zone.assemble_payload(_mixed_queue(n_flipped=3, n_low_flip=5))

    assert payload['n_confusers'] == 3
    assert all(not c['low_flip_sample'] for c in payload['confusers'])
    assert payload['budget']['n_low_flip_excluded'] == 5


def test_include_low_flip_opt_in_restores_them():
    payload = grey_zone.assemble_payload(
        _mixed_queue(n_flipped=3, n_low_flip=5), include_low_flip=True
    )

    assert payload['n_confusers'] == 8
    assert payload['budget']['n_low_flip_excluded'] == 0


def test_top_k_applies_after_the_low_flip_filter():
    # top_k must cap *confusers*, not queue rows — otherwise a queue whose first
    # rows are stable would spend the budget on rows with no grey zone in them.
    payload = grey_zone.assemble_payload(_mixed_queue(n_flipped=3, n_low_flip=5), top_k=2)

    assert payload['n_confusers'] == 2
    assert [c['source_index'] for c in payload['confusers']] == [0, 1]


def test_all_low_flip_queue_yields_an_empty_payload():
    # A judge that never wavered has nothing to open-code; say so with an empty
    # payload rather than handing over five stable rows as if they were confusers.
    payload = grey_zone.assemble_payload(_mixed_queue(n_flipped=0, n_low_flip=5))

    assert payload['n_confusers'] == 0
    assert payload['confusers'] == []
    assert payload['budget']['n_low_flip_excluded'] == 5


# --- the fallback must not silently drop half the judged input ---


def _structured_row_queue(**overrides) -> dict:
    """A confuser captured in the structured Responses-API shape: `output` is the
    assistant's ANSWER, not the rendered judge prompt, so template inversion fails
    and `variables` is None. The context to judge against lives in `query`."""
    verdict_space = {'type': 'boolean', 'labels': [False, True]}
    item = {
        'rank': 1,
        'source_index': 9,
        'low_flip_sample': False,
        'query': 'CONTEXT: Acme is a Tier-2 customer. Tier-2 gets 24h support.\nQUESTION: Does Acme get 24h support?',
        'output': 'Yes, Acme gets 24-hour support.',
        'variables': None,          # inversion failed
        'messages': None,
        'verdict_space': verdict_space,
        'ambiguity': {'instability': 1.0, 'band': 'unreliable', 'flip_rate': 1.0,
                      'mode_value': True, 'mode_rate': 0.5, 'n_repeats': 8},
        'judge_votes': {'n_true': 4, 'n_false': 4, 'counts': None, 'mean': None,
                        'stdev': None, 'representative_explanation': 'borderline'},
    }
    item.update(overrides)
    return {'meta': {'verdict_space': verdict_space, 'eval_prompt': 'Judge: {{log.output}}',
                     'n_items': 1}, 'items': [item]}


def test_fallback_includes_the_context_not_just_the_answer():
    # The bug: the fallback was `output or query`, so a groundedness judge's
    # context — the only thing you can check groundedness against — never arrived.
    payload = grey_zone.assemble_payload(_structured_row_queue(), max_chars=10_000)

    (confuser,) = payload['confusers']
    assert 'Tier-2 gets 24h support' in confuser['input']   # the context
    assert 'Yes, Acme gets 24-hour support.' in confuser['input']  # the answer
    assert confuser['input_fields'] == ['query', 'output']


def test_fallback_is_flagged_as_such():
    # No elision count can reveal a field that was never collected, so the payload
    # says where the input came from and the budget block counts it.
    payload = grey_zone.assemble_payload(_structured_row_queue(), max_chars=10_000)

    assert payload['confusers'][0]['input_source'] == 'fallback'
    assert payload['budget']['n_fallback_input'] == 1


def test_inverted_variables_are_not_flagged_as_fallback():
    payload = grey_zone.assemble_payload(_boolean_queue(), max_chars=10_000)

    assert payload['confusers'][0]['input_source'] == 'variables'
    assert payload['budget']['n_fallback_input'] == 0


def test_fallback_char_accounting_covers_every_field():
    # Previously `input_chars_original` was the answer's length alone, so the
    # budget block reported "nothing elided" while most of the input was absent.
    q = _structured_row_queue()
    item = q['items'][0]
    expected = len(item['query']) + len(item['output'])

    payload = grey_zone.assemble_payload(q, max_chars=10_000)

    assert payload['confusers'][0]['input_chars_original'] == expected


def test_fallback_includes_messages_when_present():
    q = _structured_row_queue(messages=[{'role': 'user', 'content': 'earlier turn'}])
    payload = grey_zone.assemble_payload(q, max_chars=10_000)

    assert 'earlier turn' in payload['confusers'][0]['input']
    assert payload['confusers'][0]['input_fields'] == ['query', 'output', 'messages']


def test_conversation_renders_as_the_judge_sees_it_not_as_json():
    # A Responses-API conversation nests its text under parts[].content. Dumping
    # the raw JSON puts the text behind structural noise the char budget then pays
    # for, and shows the conductor a different artifact than the judge is given —
    # so the payload renders it through the same helper the judge renders with.
    q = _structured_row_queue(
        messages=[
            {'role': 'user', 'parts': [{'type': 'text', 'content': 'where is my order'}]},
            {'role': 'assistant', 'content': 'it shipped'},
        ]
    )
    rendered = grey_zone.assemble_payload(q, max_chars=10_000)['confusers'][0]['input']

    assert 'user: where is my order' in rendered
    assert 'assistant: it shipped' in rendered
    assert '"parts"' not in rendered  # not the raw JSON dump


def test_empty_row_does_not_crash_the_payload():
    q = _structured_row_queue(query='', output='', variables=None)
    payload = grey_zone.assemble_payload(q, max_chars=10_000)

    assert payload['n_confusers'] == 1
    assert payload['confusers'][0]['input_chars_original'] == 0


# --- a tie arrives with no rationale; say so rather than quote the placeholder ---


def test_tie_break_notice_is_not_treated_as_reasoning():
    q = _structured_row_queue()
    q['items'][0]['judge_votes']['representative_explanation'] = (
        'Judge repetitions tied without a decisive tie-break.'
    )
    payload = grey_zone.assemble_payload(q, max_chars=10_000)

    assert payload['confusers'][0]['reasoning_available'] is False
    assert payload['budget']['n_no_rationale'] == 1


def test_real_reasoning_is_available():
    payload = grey_zone.assemble_payload(_structured_row_queue(), max_chars=10_000)

    assert payload['confusers'][0]['reasoning_available'] is True
    assert payload['budget']['n_no_rationale'] == 0


def test_empty_reasoning_is_unavailable():
    q = _structured_row_queue()
    q['items'][0]['judge_votes']['representative_explanation'] = '   '
    payload = grey_zone.assemble_payload(q, max_chars=10_000)

    assert payload['confusers'][0]['reasoning_available'] is False


# --- the policy may not carry a verdict the judge cannot emit ---


def _policy(output_type, verdict_space, labels, grey_zones=None):
    return {
        'output_type': output_type,
        'verdict_space': verdict_space,
        'grey_zones': grey_zones if grey_zones is not None else [],
        'labels': labels,
    }


def test_invented_categorical_label_is_rejected():
    # SKILL.md says never invent a label; nothing enforced it. An invented label
    # sailed through, entered the rewrite guidance, then scored 0 accuracy at step 8
    # — which reads as a judge failure rather than the policy error it is.
    policy = _policy(
        'categorical', {'type': 'categorical', 'labels': ['safe', 'abuse'], 'k': 2},
        [{'source_index': 0, 'value': 'borderline'}],
    )
    with pytest.raises(ValueError, match='not one of the evaluator'):
        grey_zone.validate_policy(policy)


def test_declared_categorical_label_passes():
    grey_zone.validate_policy(_policy(
        'categorical', {'type': 'categorical', 'labels': ['safe', 'abuse'], 'k': 2},
        [{'source_index': 0, 'value': 'abuse'}],
    ))


def test_numeric_label_outside_the_scale_is_rejected():
    policy = _policy(
        'number', {'type': 'number', 'scale': [1, 5]},
        [{'source_index': 0, 'value': 9, 'tolerance': 0.5}],
    )
    with pytest.raises(ValueError, match='outside the evaluator'):
        grey_zone.validate_policy(policy)


def test_numeric_tolerance_must_be_a_non_negative_number():
    policy = _policy(
        'number', {'type': 'number', 'scale': [1, 5]},
        [{'source_index': 0, 'value': 3, 'tolerance': -1}],
    )
    with pytest.raises(ValueError, match='non-negative'):
        grey_zone.validate_policy(policy)


def test_boolean_label_must_be_a_bool():
    policy = _policy(
        'boolean', {'type': 'boolean', 'labels': [False, True]},
        [{'source_index': 0, 'value': 'yes'}],
    )
    with pytest.raises(ValueError, match='must be true or false'):
        grey_zone.validate_policy(policy)


def test_label_referencing_an_undeclared_grey_zone_is_rejected():
    policy = _policy(
        'boolean', {'type': 'boolean', 'labels': [False, True]},
        [{'source_index': 0, 'value': True, 'grey_zone_id': 'gz9'}],
        grey_zones=[{'id': 'gz1', 'question': 'q', 'answer': 'a', 'rule': 'r',
                     'member_source_indices': [0]}],
    )
    with pytest.raises(ValueError, match='no grey zone declares'):
        grey_zone.validate_policy(policy)


# --- label provenance: who actually decided each point ---


def test_label_source_defaults_to_derived():
    policy = _policy(
        'boolean', {'type': 'boolean', 'labels': [False, True]},
        [{'source_index': 0, 'value': True}, {'source_index': 1, 'value': False}],
    )
    grey_zone.validate_policy(policy)
    assert grey_zone.label_provenance(policy) == {'dataset_reference': 0, 'derived': 2, 'human_confirmed': 0}


def test_label_provenance_counts_confirmed_labels():
    policy = _policy(
        'boolean', {'type': 'boolean', 'labels': [False, True]},
        [
            {'source_index': 0, 'value': True, 'label_source': 'human_confirmed'},
            {'source_index': 1, 'value': False, 'label_source': 'derived'},
        ],
    )
    assert grey_zone.label_provenance(policy) == {'dataset_reference': 0, 'derived': 1, 'human_confirmed': 1}


def test_unknown_label_source_is_rejected():
    policy = _policy(
        'boolean', {'type': 'boolean', 'labels': [False, True]},
        [{'source_index': 0, 'value': True, 'label_source': 'vibes'}],
    )
    with pytest.raises(ValueError, match='label_source must be one of'):
        grey_zone.validate_policy(policy)


# --- a real rationale that happens to contain a placeholder phrase ---


def test_real_reasoning_containing_not_available_is_kept():
    # The substring test classed this as "no rationale", and the conductor was then
    # told there was nothing to read when there was.
    assert grey_zone._is_real_rationale(
        'the tool was not available at inference time, so the claim is ungrounded'
    ) is True


def test_bare_placeholder_rationales_are_still_caught():
    assert grey_zone._is_real_rationale('No explanation provided.') is False
    assert grey_zone._is_real_rationale('(not available)') is False
    assert grey_zone._is_real_rationale('n/a') is False
    assert grey_zone._is_real_rationale('') is False


# --- cross-model rows are the first the budget drops, so they are counted ---


def test_cross_model_reason_rides_into_the_payload():
    q = _structured_row_queue()
    q['items'][0]['reason'] = 'cross_model'
    payload = grey_zone.assemble_payload(q, max_chars=10_000)
    assert payload['confusers'][0]['reason'] == 'cross_model'


def test_dropped_cross_model_rows_are_counted_separately():
    q = _many_confusers(20)
    for item in q['items'][10:]:
        item['reason'] = 'cross_model'
    payload = grey_zone.assemble_payload(q, max_chars=2000, max_tokens=2000)
    budget = payload['budget']
    if budget['n_dropped_by_budget']:
        assert budget['n_dropped_cross_model'] <= budget['n_dropped_by_budget']
    assert 'n_dropped_cross_model' in budget
