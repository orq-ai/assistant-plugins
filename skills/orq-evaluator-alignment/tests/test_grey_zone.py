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
    # The budget bounds SOURCE characters shown (§12.5); the elision marker and the
    # short `name: ` prefix are overhead on top, not charged against it.
    assert confuser['input_chars_shown'] <= 200
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
    assert payload['budget']['total_chars_elided'] == 3 * (5000 - 200)


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
