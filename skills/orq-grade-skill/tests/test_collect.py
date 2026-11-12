"""Exercise actual read/result collection from every session source."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import collect
import pipeline
import pytest
import support
from evaluatorq.formats.atif import AtifTrajectory
from support import ORIGINAL, SKILL, Client

def conversation_index(client: Client, tmp_path: Path, *, alteration: str = '') -> Path:
    """An index over one `orq traces get-conversation -o json` export holding a read of SKILL.md."""
    call_path = str(client.repo / SKILL / 'SKILL.md')
    body = ORIGINAL
    if alteration == 'snapshot':
        call_path = str(client.repo / '.orq/skill-loop/original/SKILL.md')
    if alteration == 'partial':
        body = ORIGINAL[:20]
    items: list[dict[str, Any]] = [
        {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'check the result'}]},
        {'type': 'function_call', 'call_id': 'c1', 'name': 'read', 'arguments': json.dumps({'path': call_path})},
        {'type': 'function_call_output', 'call_id': 'c1', 'output': body},
        {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Done.'}]},
    ]
    if alteration == 'mention':
        items = [
            items[0],
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': SKILL}]},
        ]
    (tmp_path / 'conv.json').write_text(json.dumps({'items': items, 'trace_id': 'trace-1', 'span_id': 'sp'}))
    entry: dict[str, Any] = {
        'trace_id': 'trace-1',
        'session_id': 'session-1',
        'repo_root': str(client.repo),
        'conversation_file': 'conv.json',
    }
    if alteration == 'request_only':
        entry.pop('session_id')
    if alteration == 'wrong_repo':
        entry['repo_root'] = str(tmp_path / 'different-repo')
    index = tmp_path / 'index.json'
    index.write_text(json.dumps([entry]))
    return index


def test_responses_collection_pairs_read_result_and_emits_atif(client: Client, tmp_path: Path) -> None:
    index = conversation_index(client, tmp_path)
    report = collect.collect_responses(client.repo, SKILL, index, tmp_path / 'export')
    assert report['loaded_sessions'] == 1
    session = json.loads((tmp_path / 'export/sessions.json').read_text())[0]
    assert set(session) == {'id', 'skill_hash', 'cwd', 'loads', 'trajectory'}
    assert session['skill_hash'] == pipeline.loop.digest(ORIGINAL)
    trajectory = AtifTrajectory.from_json(session['trajectory'])
    assert [s.source for s in trajectory.steps] == ['user', 'agent', 'agent']
    assert session['loads'] == [{'step': 2, 'skill': SKILL}]
    assert pipeline.loop.content_text(trajectory.steps[1].observation.results[0].content) == ORIGINAL


@pytest.mark.parametrize('alteration', ['mention', 'snapshot', 'partial', 'request_only', 'wrong_repo'])
def test_collection_does_not_manufacture_load_or_session_identity(
    client: Client, tmp_path: Path, alteration: str
) -> None:
    index = conversation_index(client, tmp_path, alteration=alteration)
    report = collect.collect_responses(client.repo, SKILL, index, tmp_path / 'export')
    assert report['loaded_sessions'] == 0
    assert len(report['skipped']) == 1


def test_responses_export_must_match_its_index_entry(client: Client, tmp_path: Path) -> None:
    index = conversation_index(client, tmp_path)
    (tmp_path / 'conv.json').write_text(json.dumps({'items': [], 'trace_id': 'another-trace'}))
    with pytest.raises(ValueError, match='does not match the trace index'):
        collect.collect_responses(client.repo, SKILL, index, tmp_path / 'export')


def test_responses_collection_accepts_native_orq_read_skill(client: Client, tmp_path: Path) -> None:
    items = [
        {'type': 'function_call', 'call_id': 'c1', 'name': 'read_skill', 'arguments': '{"name": "client-helper"}'},
        {'type': 'function_call_output', 'call_id': 'c1', 'output': json.dumps(collect.skill_body(ORIGINAL))},
    ]
    (tmp_path / 'conv.json').write_text(json.dumps(items))
    entry = {'trace_id': 't1', 'session_id': 's1', 'repo_root': str(client.repo), 'conversation_file': 'conv.json'}
    (tmp_path / 'index.json').write_text(json.dumps([entry]))
    report = collect.collect_responses(client.repo, SKILL, tmp_path / 'index.json', tmp_path / 'export')
    assert report['loaded_sessions'] == 1


@pytest.mark.parametrize(('variant', 'loaded'), [('exact', 1), ('other_path', 0), ('error', 0), ('broken', 0)])
def test_atif_files_are_collected_as_is(client: Client, tmp_path: Path, variant: str, loaded: int) -> None:
    step = support.read_step(f'{SKILL}/elsewhere.md' if variant == 'other_path' else f'{SKILL}/SKILL.md')
    if variant == 'error':
        step['observation']['results'][0]['extra'] = {'is_error': True}
    trajectory = support.atif('atif-1', [{'source': 'user', 'message': 'go'}, step])
    if variant == 'broken':
        trajectory['steps'][1]['step_id'] = 7
    path = tmp_path / 'atif-1.json'
    path.write_text(json.dumps(trajectory | {'extra': {'cwd': str(client.repo)}}))
    report = collect.collect_atif(client.repo, SKILL, [path], tmp_path / 'export', None)
    assert report['loaded_sessions'] == loaded
    if loaded:
        session = json.loads((tmp_path / 'export/sessions.json').read_text())[0]
        assert (session['id'], session['cwd'], session['loads']) == (
            'atif-1',
            str(client.repo),
            [{'step': 2, 'skill': SKILL}],
        )
        assert AtifTrajectory.from_json(session['trajectory']) == AtifTrajectory.from_json(path.read_text())
    else:
        assert report['skipped'][0]['reason'].startswith(
            'Not convertible' if variant == 'broken' else 'Observed read' if variant == 'error' else 'No confirmed'
        )


def claude_session(path: Path, session_id: str, base: Path, body: str) -> None:
    rows = [
        {'type': 'user', 'sessionId': session_id, 'cwd': '/work', 'uuid': 'u0', 'message': {'content': 'review it'}},
        {
            'type': 'assistant',
            'sessionId': session_id,
            'uuid': 'a1',
            'message': {'id': 'msg-1', 'content': [{'type': 'text', 'text': 'Loading the skill.'}]},
        },
        {
            'type': 'assistant',
            'uuid': 'a2',
            'message': {
                'id': 'msg-1',
                'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Skill', 'input': {'skill': 'client-helper'}}],
            },
        },
        {
            'type': 'user',
            'uuid': 'r1',
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'Launching skill'}]},
        },
        {
            'type': 'user',
            'uuid': 'm1',
            'isMeta': True,
            'sourceToolUseID': 't1',
            'message': {
                'content': [
                    {'type': 'text', 'text': f'Base directory for this skill: {base}\n\n{body}\n\nARGUMENTS: x'}
                ]
            },
        },
        {
            'type': 'assistant',
            'uuid': 'a3',
            'message': {
                'id': 'msg-2',
                'content': [{'type': 'tool_use', 'id': 't2', 'name': 'Bash', 'input': {'command': 'pytest -q'}}],
            },
        },
        {
            'type': 'user',
            'uuid': 'r2',
            'message': {
                'content': [{'type': 'tool_result', 'tool_use_id': 't2', 'is_error': True, 'content': 'Exit code 1'}]
            },
        },
        {'type': 'attachment', 'uuid': 'x1'},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(r, separators=(',', ':')) for r in rows))


@pytest.mark.parametrize(('variant', 'loaded'), [('exact', 1), ('other_version', 0), ('other_dir', 0)])
def test_claude_code_collection_requires_exact_injected_version(
    client: Client, tmp_path: Path, variant: str, loaded: int
) -> None:
    base = tmp_path / 'global/client-helper'
    body = collect.skill_body(ORIGINAL) + ('Extra line.\n' if variant == 'other_version' else '')
    claude_session(
        tmp_path / 'projects/p/s1.jsonl', 's1', tmp_path / 'elsewhere' if variant == 'other_dir' else base, body
    )
    report = collect.collect_claude(client.repo, SKILL, tmp_path / 'projects', base, tmp_path / 'export')
    assert report['loaded_sessions'] == loaded
    if not loaded:
        return
    session = json.loads((tmp_path / 'export/sessions.json').read_text())[0]
    assert (session['skill_hash'], session['cwd']) == (pipeline.loop.digest(ORIGINAL), '/work')
    steps = AtifTrajectory.from_json(session['trajectory']).steps
    assert [s.source for s in steps] == ['user', 'agent', 'user', 'agent']
    assert steps[1].message == 'Loading the skill.'
    assert [c.function_name for c in steps[1].tool_calls] == ['Skill']
    assert steps[1].observation.results[0].content == 'Launching skill'
    assert steps[2].extra == {'uuid': 'm1', 'meta': True, 'source_tool_use_id': 't1'}
    assert steps[3].observation.results[0].extra == {'is_error': True}
    assert session['loads'] == [{'step': 2, 'skill': SKILL}]
    assert pipeline.statistics(session)['explicit_error_outputs'] == 1


FAKE_ORQ = '''#!{python}
"""Stands in for the orq CLI: serves canned JSON by command and logs every invocation."""
import json, sys
from pathlib import Path
here = Path(__file__).parent
args = sys.argv[1:]
with (here / 'calls.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\\n')
canned = json.loads((here / 'canned.json').read_text())
if args[:2] == ['traces', 'query-oql']:
    token = args[args.index('--page-token') + 1] if '--page-token' in args else 'first'
    print(json.dumps(canned['pages'][token]))
elif args[:2] == ['traces', 'get-conversation']:
    print(json.dumps(canned['conversations'][args[2]]))
else:
    sys.exit(f'unexpected orq call: {{args}}')
'''


def read_items(call_id: str, name: str, arguments: dict[str, Any], output: str) -> list[dict[str, Any]]:
    return [
        {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'check the result'}]},
        {'type': 'function_call', 'call_id': call_id, 'name': name, 'arguments': json.dumps(arguments)},
        {'type': 'function_call_output', 'call_id': call_id, 'output': output},
        {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Done.'}]},
    ]


@pytest.fixture
def fake_orq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two pages of tagged traces: an older and a newer trace of one session, a thread, and an unloaded trace."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    script = bin_dir / 'orq'
    script.write_text(FAKE_ORQ.format(python=sys.executable))
    script.chmod(0o755)
    row = {'session_id': '', 'thread_id': '', 'start_time': ''}
    canned = {
        'pages': {
            'first': {
                'object': 'query',
                'search': {
                    'object': 'list',
                    'has_more': True,
                    'next_page_token': 'p2',
                    'data': [
                        row | {'trace_id': 't-old', 'session_id': 'sess-a', 'start_time': '2026-01-01T10:00:00Z'},
                        row | {'trace_id': 't-new', 'session_id': 'sess-a', 'start_time': '2026-01-01T10:05:00Z'},
                    ],
                },
            },
            'p2': {
                'object': 'query',
                'search': {
                    'object': 'list',
                    'has_more': False,
                    'data': [
                        row | {'trace_id': 't-thread', 'thread_id': 'thread-b', 'start_time': '2026-01-01T11:00:00Z'},
                        row | {'trace_id': 't-mention', 'start_time': '2026-01-01T12:00:00Z'},
                    ],
                },
            },
        },
        'conversations': {
            't-new': {
                'trace_id': 't-new',
                'span_id': 'sp',
                'items': read_items('c1', 'read', {'path': f'{SKILL}/SKILL.md'}, ORIGINAL),
            },
            't-thread': {
                'trace_id': 't-thread',
                'span_id': 'sp',
                'items': read_items(
                    'c1', 'read_skill', {'name': 'client-helper'}, json.dumps(collect.skill_body(ORIGINAL))
                ),
            },
            't-mention': {
                'trace_id': 't-mention',
                'span_id': 'sp',
                'items': [
                    {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': SKILL}]}
                ],
            },
        },
    }
    (bin_dir / 'canned.json').write_text(json.dumps(canned))
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    return bin_dir


def test_traces_collection_pages_tagged_traces_and_keeps_the_latest_per_session(
    client: Client, tmp_path: Path, fake_orq: Path
) -> None:
    out = tmp_path / 'export'
    report = collect.collect_traces(client.repo, SKILL, 'skill-grading=my-skill', '7d', 'now', out)
    calls = [json.loads(line) for line in (fake_orq / 'calls.jsonl').read_text().splitlines()]
    queries = [c for c in calls if c[:2] == ['traces', 'query-oql']]
    assert len(queries) == 2
    assert queries[0][queries[0].index('--oql') + 1] == 'fetch traces | filter metadata.skill-grading == "my-skill"'
    assert queries[1][queries[1].index('--page-token') + 1] == 'p2'
    assert all(c[-3:] == ['-o', 'json', '--no-input'] for c in calls)
    fetched = sorted(c[2] for c in calls if c[:2] == ['traces', 'get-conversation'])
    assert fetched == ['t-mention', 't-new', 't-thread']  # never the superseded t-old
    assert (report['harness'], report['matched_traces'], report['scanned']) == ('orq-traces', 4, 3)
    assert report['loaded_sessions'] == 2
    assert [s['source'] for s in report['skipped']] == ['t-mention']
    sessions = json.loads((out / 'sessions.json').read_text())
    assert sorted(s['id'] for s in sessions) == ['sess-a', 'thread-b']
    assert all(s['skill_hash'] == pipeline.loop.digest(ORIGINAL) for s in sessions)
    assert json.loads((out / 'collection.json').read_text()) == report
    index = json.loads((tmp_path / 'export-traces/index.json').read_text())
    assert {e['trace_id'] for e in index} == set(fetched)
    # the kept exports reproduce the same cohort offline
    offline = collect.collect_responses(client.repo, SKILL, tmp_path / 'export-traces/index.json', tmp_path / 'again')
    assert json.loads((tmp_path / 'again/sessions.json').read_text()) == sessions
    assert offline['loaded_sessions'] == 2


@pytest.mark.parametrize('pair', ['no-separator', 'bad key=v', 'key=say "hi"', 'key='])
def test_traces_collection_rejects_unsafe_filters_before_calling_orq(
    client: Client, tmp_path: Path, fake_orq: Path, pair: str
) -> None:
    with pytest.raises(ValueError, match='KEY=VALUE|Metadata'):
        collect.collect_traces(client.repo, SKILL, pair, '7d', 'now', tmp_path / 'export')
    assert not (fake_orq / 'calls.jsonl').exists()


def test_traces_collection_fails_clearly_without_the_orq_cli(
    client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    with pytest.raises(ValueError, match='orq CLI is not on PATH'):
        collect.collect_traces(client.repo, SKILL, 'skill-grading=my-skill', '7d', 'now', tmp_path / 'export')


# Sanitized from real tagged sessions (system prompts, ids and user paths replaced); see fixtures/traces.
FIXTURES = Path(__file__).parent / 'fixtures/traces'
PROBE_SKILL = '.agents/skills/probe-greeting'
PROBE = (
    '---\nname: probe-greeting\ndescription: Use when the user asks for the probe greeting.\n---\n\n'
    '# Probe greeting\n\nReply with exactly one line: `PROBE-OK lantern-42`. Do nothing else.\n'
)


def serve_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: str) -> Path:
    """The fake orq CLI serving one harness capture as a single page of tagged traces."""
    capture = json.loads((FIXTURES / f'{harness}.json').read_text())
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'orq').write_text(FAKE_ORQ.format(python=sys.executable))
    (bin_dir / 'orq').chmod(0o755)
    page = {'object': 'query', 'search': {'object': 'list', 'has_more': False, 'data': capture['rows']}}
    canned = {'pages': {'first': page}, 'conversations': capture['conversations']}
    (bin_dir / 'canned.json').write_text(json.dumps(canned))
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    return bin_dir


@pytest.mark.parametrize(
    ('harness', 'loader', 'scanned'),
    [('claude-code', 'Skill', 1), ('codex', 'exec', 3), ('orq-agent', 'read_skill', 1), ('storq', 'Read', 1)],
)
@pytest.mark.parametrize('version', ['same', 'edited'])
def test_traces_tie_each_harness_load_to_the_exact_skill_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: str, loader: str, scanned: int, version: str
) -> None:
    serve_capture(tmp_path, monkeypatch, harness)
    repo = tmp_path / 'client'
    (repo / PROBE_SKILL).mkdir(parents=True)
    (repo / PROBE_SKILL / 'SKILL.md').write_text(PROBE if version == 'same' else PROBE.replace('42', '43'))
    report = collect.collect_traces(repo, PROBE_SKILL, 'skill-probe=grade-me', '7d', 'now', tmp_path / 'export')
    assert report['scanned'] == scanned  # Codex: its side agents and an untagged call stay separate sessions
    if version == 'edited':
        assert report['loaded_sessions'] == 0
        return
    assert report['loaded_sessions'] == 1
    [session] = json.loads((tmp_path / 'export/sessions.json').read_text())
    steps = AtifTrajectory.from_json(session['trajectory']).steps
    assert [c.function_name for c in steps[session['loads'][0]['step'] - 1].tool_calls] == [loader]
