"""Regression tests for claim-aware filing and cross-pass agreement."""

from __future__ import annotations

from typing import Any

import pytest

import pipeline
import support


def make_issue(
    issue_id: str,
    claim: str,
    session_ids: list[str],
    *,
    cause: str = 'skill_missing',
    path: str = 'SKILL.md',
    start_line: int = 6,
    end_line: int = 6,
) -> dict[str, Any]:
    return {
        'id': issue_id,
        'claim': claim,
        'priority': 'p2',
        'cause': cause,
        'skill_hash': pipeline.loop.digest(support.ORIGINAL),
        'instruction': {'path': path, 'start_line': start_line, 'end_line': end_line},
        'evidence': [
            {
                'session_id': session_id,
                'step': 2,
                'start': 0,
                'end': len(support.FAILURE_TEXT),
                'quote': None,
            }
            for session_id in session_ids
        ],
    }


def finding(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        'decision': 'file',
        'reason': 'The evidence supports this claim.',
        'owning_surface': 'SKILL.md',
        'behavioral_rule': 'Follow the applicable instruction.',
        'issue': issue,
    }


def test_file_pass_does_not_pool_evidence_for_different_claims() -> None:
    sessions = {
        f'session-{number}': support.loaded(f'session-{number}', support.FAILURE_TEXT) for number in range(6)
    }
    grades = {session_id: {'low_score': True} for session_id in sessions}
    first = make_issue(
        'grader-a', 'The skill omits the dashboard verification step.', ['session-0', 'session-1', 'session-2']
    )
    second = make_issue(
        'grader-b',
        'The skill instructs the agent to fabricate dashboard data.',
        ['session-3', 'session-4', 'session-5'],
    )

    filed = pipeline.file_pass(
        grades,
        [finding(first), finding(second)],
        {'skill_hash': pipeline.loop.digest(support.ORIGINAL)},
        {'SKILL.md': support.ORIGINAL},
        sessions,
        list(sessions),
    )

    assert len(filed) == 2
    by_claim = {issue['claim']: {pointer['session_id'] for pointer in issue['evidence']} for issue in filed}
    assert by_claim == {
        first['claim']: {'session-0', 'session-1', 'session-2'},
        second['claim']: {'session-3', 'session-4', 'session-5'},
    }


def test_agree_does_not_turn_two_different_claims_into_a_two_of_three_vote() -> None:
    claim_a = make_issue('same-id', 'The skill omits dashboard verification.', ['session-0'])
    claim_b = make_issue('other-id', 'The skill requires fabricating dashboard evidence.', ['session-1'])
    findings_by_pass = [[finding(claim_a)], [finding(claim_b)], []]

    filed, findings, pass_filed_ids, agreement = pipeline.agree([[claim_a], [claim_b], []], findings_by_pass)

    assert filed == []
    assert agreement == {'same-id': 1, 'other-id': 1}
    assert pass_filed_ids == [['same-id'], ['other-id'], []]
    assert [finding['decision'] for finding in findings] == ['dont_file', 'dont_file']


@pytest.mark.parametrize(
    'change',
    [
        {'claim': 'A different claim'},
        {'cause': 'trigger'},
        {'instruction': {'path': 'other.md', 'start_line': 6, 'end_line': 6}},
        {'instruction': {'path': 'SKILL.md', 'start_line': 20, 'end_line': 20}},
    ],
)
def test_reused_issue_id_with_conflicting_identity_fails_closed(change: dict[str, Any]) -> None:
    original = make_issue('reused', 'The skill omits dashboard verification.', ['session-0'])
    conflict = original | change

    with pytest.raises(ValueError, match='reused for a different normalized claim'):
        pipeline.validate_issue_id_consistency([[finding(original)], [finding(conflict)]])


def test_same_claim_normalization_only_collapses_case_and_whitespace() -> None:
    assert pipeline.normalized_claim('  Check\tTHE\nDashboard  ') == 'check the dashboard'
    assert pipeline.normalized_claim('Check the dashboard!') != pipeline.normalized_claim('Check the dashboard')
