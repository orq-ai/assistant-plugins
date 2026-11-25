"""Tests the grader batch self-check against its immutable assignment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import pipeline
import support


def prepare_run(client: support.Client) -> Path:
    client.sessions = [client.sessions[0] | {'id': f'session-{number}'} for number in range(10)]
    client.sessions_path.write_text(json.dumps(client.sessions))
    return pipeline.prepare(client.repo, support.SKILL, client.sessions_path, None, 'batch-selfcheck')


def assigned_ids(batch: dict[str, Any]) -> list[str]:
    return [Path(transcript).stem for transcript in batch['transcripts']]


def write_result(run: Path, batch: dict[str, Any], *, fabricated_quote: bool = False) -> None:
    session_grades = [
        {
            'session_id': session_id,
            'efficiency': {'label': 'mostly_efficient', 'score': 0.8, 'reason': 'Direct work observed.'},
            'quality': {'label': 'insufficient_evidence', 'score': None, 'reason': 'Outcome is not visible.'},
            'usage': 'loaded',
            'usage_reason': 'The session header records the skill load.',
        }
        for session_id in assigned_ids(batch)
    ]
    findings = []
    if fabricated_quote:
        issue = support.issue([session_grades[0]['session_id']])
        issue['evidence'][0]['quote'] = 'This exact quotation does not occur in the assigned session.'
        findings = [
            {
                'decision': 'file',
                'reason': 'A source quote is required for the claimed defect.',
                'owning_surface': 'SKILL.md',
                'behavioral_rule': 'Cite exact evidence from the assigned transcript.',
                'issue': issue,
            }
        ]
    result = {
        'schema_version': 1,
        'batch_id': batch['batch_id'],
        'sessions': session_grades,
        'findings': findings,
    }
    destination = run / batch['output']
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result))


def test_validate_batch_cli_confirms_a_complete_assigned_result(
    client: support.Client, capsys: pytest.CaptureFixture[str]
) -> None:
    run = prepare_run(client)
    batch = pipeline.plan_batches(json.loads((run / 'grade-plan.json').read_text()))[0]
    write_result(run, batch)

    pipeline.main(['validate-batch', '--run', str(run), '--batch-id', batch['batch_id']])

    output = json.loads(capsys.readouterr().out)
    assert output == {
        'valid': True,
        'batch_id': batch['batch_id'],
        'output': batch['output'],
        'sessions': len(batch['transcripts']),
        'findings': 0,
        'assignment_source': 'frozen grade-plan.json',
    }


def test_validate_batch_rejects_fabricated_source_quote(client: support.Client) -> None:
    run = prepare_run(client)
    batch = pipeline.plan_batches(json.loads((run / 'grade-plan.json').read_text()))[0]
    write_result(run, batch, fabricated_quote=True)

    with pytest.raises(ValueError, match='Quote not found'):
        pipeline.validate_batch(run, batch['batch_id'])


def test_validate_batch_rejects_changed_frozen_assignment(client: support.Client) -> None:
    run = prepare_run(client)
    batch = pipeline.plan_batches(json.loads((run / 'grade-plan.json').read_text()))[0]
    write_result(run, batch)
    plan_path = run / 'grade-plan.json'
    plan = json.loads(plan_path.read_text())
    plan['passes'][0]['batches'][0]['transcripts'].pop()
    plan_path.write_text(json.dumps(plan))

    with pytest.raises(ValueError, match=r'grade-plan\.json snapshot changed'):
        pipeline.validate_batch(run, batch['batch_id'])


def test_validate_batch_rejects_missing_or_unassigned_session(client: support.Client) -> None:
    run = prepare_run(client)
    batch = pipeline.plan_batches(json.loads((run / 'grade-plan.json').read_text()))[0]
    write_result(run, batch)
    output_path = run / batch['output']
    payload = json.loads(output_path.read_text())
    payload['sessions'].pop()
    output_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match='every assigned session exactly once'):
        pipeline.validate_batch(run, batch['batch_id'])
