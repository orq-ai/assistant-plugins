"""A synthetic client repo with one tracked skill and ten synthetic ATIF sessions."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pipeline

loop = pipeline.loop

SKILL = '.agents/skills/client-helper'
ORIGINAL = '---\nname: client-helper\ndescription: Check the client result.\n---\n\nRead the result.\n'
FAILURE_TEXT = 'The client returned an error. The agent reported success without reading the result.'
FAILED = {'label': 'mostly_inefficient', 'score': 0.4, 'reason': 'Reworked after an unread error.'}
FINE = {'label': 'mostly_efficient', 'score': 0.8, 'reason': 'Observed direct work.'}
UNSEEN = {'label': 'insufficient_evidence', 'score': None, 'reason': 'Outcome not visible.'}


def atif(session_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """An ATIF trajectory dict with sequential step ids."""
    numbered = [{'step_id': n} | step for n, step in enumerate(steps, start=1)]
    return {'schema_version': 'ATIF-v1.7', 'session_id': session_id, 'agent': {'name': 't', 'version': '1'}} | {
        'steps': numbered
    }


def read_step(path: str = f'{SKILL}/SKILL.md', body: str = ORIGINAL) -> dict[str, Any]:
    call = {'tool_call_id': 'c1', 'function_name': 'Read', 'arguments': {'file_path': path}}
    result = {'source_call_id': 'c1', 'content': body}
    return {
        'source': 'agent',
        'message': 'Reading the skill.',
        'tool_calls': [call],
        'observation': {'results': [result]},
    }


def loaded(session_id: str, text: str) -> dict[str, Any]:
    """Step 1 reads the skill, step 2 is the agent's answer (evidence points there)."""
    return {
        'id': session_id,
        'skill_hash': loop.digest(ORIGINAL),
        'loads': [{'step': 1, 'skill': SKILL}],
        'trajectory': atif(session_id, [read_step(), {'source': 'agent', 'message': text}]),
    }


def issue(session_ids: list[str] | None = None, priority: str = 'p1') -> dict[str, Any]:
    return {
        'id': 'result-check',
        'claim': 'The skill does not say to reject an error result.',
        'priority': priority,
        'cause': 'skill_missing',
        'skill_hash': loop.digest(ORIGINAL),
        'instruction': {'path': 'SKILL.md', 'start_line': 6, 'end_line': 6},
        'evidence': [
            {'session_id': session_id, 'step': 2, 'start': 0, 'end': len(FAILURE_TEXT), 'quote': None}
            for session_id in (session_ids if session_ids is not None else [f'failure-{i}' for i in range(3)])
        ],
    }


def finding(filed: dict[str, Any]) -> dict[str, Any]:
    return {
        'decision': 'file',
        'reason': 'The skill omits the error check.',
        'owning_surface': 'SKILL.md',
        'behavioral_rule': 'Report error results as failures.',
        'issue': filed,
    }


@dataclass
class Client:
    repo: Path
    sessions_path: Path
    sessions: list[dict[str, Any]]

    def prepare(self, run_id: str = 'test-run', filed: dict[str, Any] | None = None) -> Path:
        """Prepare, grade failure-* as failed, file one issue, and freeze the baseline."""
        self.sessions_path.write_text(json.dumps(self.sessions))
        run = pipeline.prepare(self.repo, SKILL, self.sessions_path, None, run_id)
        grade(run, [finding(filed if filed is not None else issue())])
        return run


def grade(run: Path, findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Write one grader output per batch; each finding goes to the batch holding its evidence."""
    for batch in pipeline.plan_batches(json.loads((run / 'grade-plan.json').read_text())):
        ids = [Path(t).stem for t in batch['transcripts']]
        sessions = [
            {
                'session_id': sid,
                'efficiency': FAILED if sid.startswith('failure') else FINE,
                'quality': UNSEEN,
                'usage': 'loaded',
                'usage_reason': 'Recorded skill read.',
            }
            for sid in ids
        ]
        mine = [f for f in findings if f['issue']['evidence'][0]['session_id'] in ids]
        payload = {'schema_version': 1, 'batch_id': batch['batch_id'], 'sessions': sessions, 'findings': mine}
        (run / batch['output']).write_text(json.dumps(payload))
    return pipeline.baseline(run)


def make_client(tmp_path: Path) -> Client:
    repo = tmp_path / 'client'
    repo.mkdir()
    loop.git(repo, 'init', '-q')
    loop.git(repo, 'config', 'user.name', 'Skill Grading Test')
    loop.git(repo, 'config', 'user.email', 'skill-grading@example.invalid')
    path = repo / SKILL / 'SKILL.md'
    path.parent.mkdir(parents=True)
    path.write_text(ORIGINAL)
    (repo / 'AGENTS.md').write_text('Check actual results before reporting success.\n')
    loop.git(repo, 'add', '--', SKILL, 'AGENTS.md')
    loop.git(repo, 'commit', '-qm', 'Track the client skill')
    sessions = [loaded(f'failure-{i}', FAILURE_TEXT) for i in range(3)]
    sessions += [loaded(f'control-{i}', 'Read the result and reported the error.') for i in range(7)]
    return Client(repo, tmp_path / 'sessions.json', sessions)
