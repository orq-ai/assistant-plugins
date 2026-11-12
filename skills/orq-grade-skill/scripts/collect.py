# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pydantic>=2",
#     "evaluatorq>=1.87",
# ]
# ///
"""Normalize sessions to ATIF trajectories with exact skill-load and version evidence.

Inputs: Orq traces selected by a metadata tag (fetched with the `orq` CLI), Claude Code
JSONL (converted here), ATIF files as-is, and Orq Responses conversations
(`orq traces get-conversation <trace-id> -o json`) converted with evaluatorq's
`responses_to_atif`. Loads are detected on the ATIF steps; each one is tied to the exact
skill text it returned:

- `Skill` call (Claude Code): the body Claude Code injects right after the call.
- `read_skill` (Orq agents): the skill text in the call's result.
- Any other call naming `<skill>/SKILL.md` (`Read` in storq, `exec` shell reads in Codex): the
  skill text inside its result; `cat -n` line numbers are removed first.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import loop
from evaluatorq.formats.atif import AtifTrajectory
from evaluatorq.formats.convert_responses_atif import responses_to_atif
from evaluatorq.formats.responses import ResponsesConversation

CLAUDE_BASE = re.compile(r'Base directory for this skill: (\S+)\n\n')
NUMBERED = re.compile(r' *\d+\t(.*)')


def skill_body(skill_md: str) -> str:
    """Instructions as harnesses inject them: frontmatter removed, leading blank lines dropped."""
    match = re.match(r'---\n.*?\n---\n', skill_md, re.DOTALL)
    return (skill_md[match.end() :] if match else skill_md).lstrip('\n')


def decoded(result: str) -> str:
    """Orq read_skill results arrive JSON-string encoded; `Read` results as `cat -n` listings."""
    try:
        value = json.loads(result)
    except ValueError:
        value = result
    result = value if isinstance(value, str) else result
    lines = result.removesuffix('\n').split('\n')
    numbered = [NUMBERED.fullmatch(line) for line in lines]
    if not all(numbered):
        return result
    return '\n'.join(match.group(1) for match in numbered) + '\n' * result.endswith('\n')


class Target:
    """The selected skill version and where its loads may come from."""

    def __init__(self, repo: Path, skill: str, loaded_from: Path | None = None) -> None:
        self.repo, self.skill, self.name = repo.resolve(), skill, Path(skill).name
        self.path = loop.safe_path(self.repo, skill) / 'SKILL.md'
        self.full = self.path.read_text()
        self.hash, self.body = loop.digest(self.full), skill_body(self.full)
        self.loaded_from = loaded_from.resolve() if loaded_from else None

    def loads(self, trajectory: AtifTrajectory) -> tuple[list[int], str | None]:
        """Step ids whose tool call loaded exactly this version, plus the first rejection reason."""
        steps, reason = [], None
        injected = {
            step.extra['source_tool_use_id']: loop.content_text(step.message)
            for step in trajectory.steps
            if step.source == 'user' and (step.extra or {}).get('source_tool_use_id')
        }
        for index, step in enumerate(trajectory.steps):
            results = {r.source_call_id: r for r in step.observation.results} if step.observation else {}
            for call in step.tool_calls or []:
                verdict = self.check(call.function_name, call.arguments, results.get(call.tool_call_id))
                if call.function_name == 'Skill' and call.arguments.get('skill') == self.name:
                    # Local transcripts link the injected body to its call; traces only place it next.
                    after = trajectory.steps[index + 1] if index + 1 < len(trajectory.steps) else None
                    fallback = loop.content_text(after.message) if after and after.source == 'user' else None
                    verdict = self.check_injection(injected.get(call.tool_call_id, fallback))
                if verdict is True:
                    steps.append(step.step_id)
                elif verdict:
                    reason = reason or verdict
        return sorted(set(steps)), reason

    def check(self, name: str, arguments: dict[str, Any], result: Any) -> bool | str | None:
        """`read_skill` by name, or any call naming `<skill>/SKILL.md`, whose result holds this version's text.

        The text is the identity, not the path: traces carry paths from another machine or harness
        directory (`.storq/plugins/...`).
        """
        if name == 'read_skill':
            if arguments.get('name') != self.name:
                return None
        elif f'{self.name}/SKILL.md' not in json.dumps(arguments):
            return None
        text = loop.content_text(result.content) if result else None
        if text is None or (result.extra or {}).get('is_error'):
            return 'Observed read lacks a complete result matching the selected skill version'
        # ponytail: containment, so a shell read with a wrapper around the output counts; a skill
        # whose whole body is one line could match a grep of that line.
        return (self.body or self.full) in decoded(text) or (
            'Observed read lacks a complete result matching the selected skill version'
        )

    def check_injection(self, injected: str | None) -> bool | str:
        """Claude Code: the meta user step `Base directory for this skill: <dir>\\n\\n<body>` linked to the call."""
        if injected is None:
            return 'No injected body for the Skill call'
        match = CLAUDE_BASE.match(injected)
        core = injected[match.end() :] if match else ''
        core = core[: core.rfind('\n\nARGUMENTS:')] if '\n\nARGUMENTS:' in core else core
        if not match or (self.loaded_from and Path(match.group(1)).resolve() != self.loaded_from):
            return 'Skill loaded from a different directory'
        return core == self.body or 'Injected body is a different version of the skill'


def claude_trajectory(rows: list[dict[str, Any]], session_id: str) -> AtifTrajectory:
    """Claude Code JSONL as ATIF: rows of one assistant message merge into one agent step.

    Tool results attach to the step holding their call; text rows become user steps. Meta rows
    keep `meta` and `source_tool_use_id` in `extra`. Thinking blocks are dropped.
    """
    steps: list[dict[str, Any]] = []
    owner: dict[str, dict[str, Any]] = {}

    def new_step(source: str, message: str, extra: dict[str, Any]) -> dict[str, Any]:
        step = {'step_id': len(steps) + 1, 'source': source, 'message': message, 'extra': extra}
        steps.append(step)
        return step

    for row in rows:
        if row.get('type') not in {'user', 'assistant'}:
            continue
        message = row.get('message') or {}
        content = message.get('content')
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content or []
        texts = [b['text'] for b in blocks if b.get('type') == 'text' and isinstance(b.get('text'), str)]
        if row['type'] == 'assistant':
            last = steps[-1] if steps else None
            if last and last['source'] == 'agent' and message.get('id') and last['extra'].get('id') == message['id']:
                step = last
                step['message'] = '\n'.join(filter(None, [step['message'], *texts]))
            else:
                step = new_step('agent', '\n'.join(texts), {'id': message.get('id'), 'uuid': row.get('uuid')})
            for block in blocks:
                if block.get('type') == 'tool_use':
                    call = {'tool_call_id': block['id'], 'function_name': block['name']}
                    step.setdefault('tool_calls', []).append(call | {'arguments': block.get('input') or {}})
                    owner[block['id']] = step
            continue
        for block in blocks:
            if block.get('type') != 'tool_result':
                continue
            result = block.get('content')
            if isinstance(result, list):
                result = '\n'.join(p.get('text', '') for p in result if isinstance(p, dict))
            call_id = block.get('tool_use_id')
            step = owner.get(call_id) or next((s for s in reversed(steps) if s['source'] == 'agent'), None)
            step = step or new_step('agent', '', {})
            known = step is owner.get(call_id)
            record = {
                'source_call_id': call_id if known else None,
                'content': result if isinstance(result, str) else json.dumps(result),
                'extra': {'is_error': bool(block.get('is_error'))} | ({} if known else {'call_id': call_id}),
            }
            step.setdefault('observation', {'results': []})['results'].append(record)
        if texts or not any(b.get('type') == 'tool_result' for b in blocks):
            extra = {'uuid': row.get('uuid')}
            if row.get('isMeta'):
                extra |= {'meta': True, 'source_tool_use_id': row.get('sourceToolUseID')}
            new_step('user', '\n'.join(texts), {k: v for k, v in extra.items() if v is not None})
    return AtifTrajectory.model_validate({
        'session_id': session_id,
        'agent': {'name': 'claude-code', 'version': next((r['version'] for r in rows if r.get('version')), 'unknown')},
        'steps': steps,
    })


def collect_claude(repo: Path, skill: str, projects: Path, loaded_from: Path, output: Path) -> dict[str, Any]:
    """Claude Code sessions whose Skill call injected exactly this version from `loaded_from`."""
    target = Target(repo, skill, loaded_from)
    files = sorted(projects.glob('*/*.jsonl'))
    candidates = []
    for path in files:
        raw = path.read_text()
        if '"name":"Skill"' not in raw or f'"skill":"{target.name}"' not in raw:
            continue
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        session_id = next((r['sessionId'] for r in rows if r.get('sessionId')), None)
        cwd = next((r['cwd'] for r in rows if r.get('cwd')), None)
        candidates.append((str(path), session_id, cwd, converted(claude_trajectory, rows, session_id)))
    limits = [
        'Local Claude Code transcripts (top-level sessions only, no subagent files; thinking blocks dropped)',
        'Version identity is the injected body; frontmatter is not injected, so it is taken from the client repo',
    ]
    meta = {'harness': 'claude-code', 'scanned': len(files), 'source_index': str(projects.resolve())}
    meta['loaded_from'] = str(loaded_from.resolve())
    return write(target, candidates, output, meta, limits)


def collect_atif(repo: Path, skill: str, paths: list[Path], output: Path, loaded_from: Path | None) -> dict[str, Any]:
    """ATIF files as-is; session id from `session_id`, cwd from `extra.cwd` (else the client repo)."""
    target = Target(repo, skill, loaded_from)
    candidates = []
    for path in paths:
        trajectory = converted(AtifTrajectory.from_json, path.read_text())
        extra = (trajectory.extra or {}) if isinstance(trajectory, AtifTrajectory) else {}
        session_id = trajectory.session_id if isinstance(trajectory, AtifTrajectory) else path.stem
        candidates.append((str(path), session_id, extra.get('cwd'), trajectory))
    limits = ['Caller-selected ATIF files; repository identity is not recorded in ATIF and is not checked']
    return write(target, candidates, output, {'harness': 'atif', 'scanned': len(paths)}, limits)


def collect_responses(repo: Path, skill: str, index_path: Path, output: Path) -> dict[str, Any]:
    """Orq conversations listed in an index of `{trace_id, session_id, repo_root, conversation_file}`.

    `conversation_file` holds `orq traces get-conversation <trace-id> -o json` output (or a bare item list).
    """
    target = Target(repo, skill)
    index = json.loads(index_path.read_text())
    if not isinstance(index, list):
        raise TypeError('Conversation index must be a JSON array')
    candidates = []
    for entry in index:
        exported = json.loads((index_path.parent / entry['conversation_file']).read_text())
        items = exported['items'] if isinstance(exported, dict) else exported
        if isinstance(exported, dict) and exported.get('trace_id', entry['trace_id']) != entry['trace_id']:
            raise ValueError('Conversation export does not match the trace index')
        root = entry.get('repo_root')
        session_id = entry.get('session_id') if root and Path(root).resolve() == target.repo else None
        trajectory = converted(
            lambda items, sid: responses_to_atif(ResponsesConversation(items=items), agent_name='orq', session_id=sid),
            function_items(items),
            session_id,
        )
        candidates.append((entry['trace_id'], session_id, root, trajectory))
    limits = [
        'Bounded exported candidate set; not an exhaustive workspace scan',
        'Session identity and repository root come from the index; entries lacking either are skipped',
    ]
    meta = {'harness': 'responses', 'scanned': len(index), 'source_index': str(index_path.resolve())}
    meta['source_index_sha256'] = loop.digest(index_path.read_text())
    return write(target, candidates, output, meta, limits)


def function_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Codex's freeform tool items as function calls, so ATIF keeps them as tool calls with results.

    The input goes under `script`, a key evaluatorq's transcript renders for shell-like tools such as `exec`.
    ponytail: `responses_to_atif` keeps `custom_tool_call` only in step extras; drop this once it converts them.
    """
    out = []
    for item in items:
        if item.get('type') == 'custom_tool_call':
            call = {k: v for k, v in item.items() if k != 'input'}
            item = call | {'type': 'function_call', 'arguments': json.dumps({'script': item.get('input', '')})}
        elif item.get('type') == 'custom_tool_call_output':
            item = item | {'type': 'function_call_output'}
        if item.get('status') == '':  # Codex and storq outputs; an empty status fails Responses validation
            item = {k: v for k, v in item.items() if k != 'status'}
        out.append(item)
    return out


METADATA_KEY = re.compile(r'[A-Za-z0-9_-]{1,64}')


def orq_json(*args: str) -> Any:
    """Run one read-only `orq` CLI command and parse its JSON output."""
    binary = shutil.which('orq')
    if binary is None:
        raise ValueError('The orq CLI is not on PATH; install it and run `orq auth login` first')
    done = subprocess.run([binary, *args, '-o', 'json', '--no-input'], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise ValueError(f'orq {args[0]} {args[1]} failed: {done.stderr.strip()[:500]}')
    return json.loads(done.stdout)


def tagged_traces(key: str, value: str, since: str, until: str) -> tuple[str, list[dict[str, Any]]]:
    """Every trace whose metadata `key` equals `value` in the window, following OQL pagination."""
    if not METADATA_KEY.fullmatch(key):
        raise ValueError('Metadata key must be 1-64 letters, digits, hyphens or underscores')
    if not value or any(char in value for char in '"\\\n'):
        raise ValueError('Metadata value must be non-empty and contain no quotes, backslashes or newlines')
    oql = f'fetch traces | filter metadata.{key} == "{value}"'
    rows, token = [], None
    while True:
        paging = ['--page-token', token] if token else []
        page = orq_json('traces', 'query-oql', '--from', since, '--to', until, '--oql', oql, *paging)
        search = page['search']
        rows += search['data']
        token = search.get('next_page_token')
        if not search.get('has_more') or not token:
            return oql, rows


def session_keys(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Trace id to session key, from the fields each harness fills in its router calls."""
    responses = {response_id(r): r for r in rows if response_id(r)}

    def key(row: dict[str, Any]) -> str:
        if row.get('session_id') not in {None, '', row['trace_id']}:
            return row['session_id']  # Claude Code
        call = (row.get('attributes') or {}).get('openresponses') or {}
        if row.get('thread_id') or call.get('prompt_cache_key'):
            return row.get('thread_id') or call['prompt_cache_key']  # thread id; Codex
        seen = set()
        while (parent := responses.get(call.get('previous_response_id'))) and response_id(parent) not in seen:
            row = parent  # storq: walk previous_response_id to the chain's first call
            seen.add(response_id(row))
            call = row['attributes'].get('openresponses') or {}
        return response_id(row) or row['trace_id']

    return {row['trace_id']: key(row) for row in rows}


def response_id(row: dict[str, Any]) -> str | None:
    return (((row.get('attributes') or {}).get('gen_ai') or {}).get('response') or {}).get('id')


def collect_traces(
    repo: Path, skill: str, metadata: str, since: str, until: str, output: Path
) -> dict[str, Any]:
    """Orq traces tagged `key=value`: the latest trace per session, exported and collected as Responses.

    Exports and their index go to `<output>-traces/`, so the run can be repeated offline with `--responses`.
    """
    key, separator, value = metadata.partition('=')
    if not separator:
        raise ValueError('--trace-metadata takes KEY=VALUE')
    oql, rows = tagged_traces(key, value, since, until)
    latest: dict[str, dict[str, Any]] = {}
    keys = session_keys(rows)
    for row in rows:
        session_id = keys[row['trace_id']]
        if session_id not in latest or row.get('start_time', '') > latest[session_id].get('start_time', ''):
            latest[session_id] = row
    exports = output.parent / f'{output.name}-traces'
    exports.mkdir(parents=True, exist_ok=False)
    index = []
    for session_id, row in sorted(latest.items()):
        trace_id = row['trace_id']
        conversation = f'{re.sub(r"[^A-Za-z0-9_.-]", "_", trace_id)}.json'
        loop.write_json(exports / conversation, orq_json('traces', 'get-conversation', trace_id))
        index.append({
            'trace_id': trace_id,
            'session_id': session_id,
            'repo_root': str(repo.resolve()),
            'conversation_file': conversation,
        })
    loop.write_json(exports / 'index.json', index)
    report = collect_responses(repo, skill, exports / 'index.json', output)
    report |= {
        'harness': 'orq-traces',
        'oql': oql,
        'window': {'from': since, 'to': until},
        'matched_traces': len(rows),
        'scanned': len(index),
        'limitations': [
            'Traces selected by one metadata tag in one time window; not an exhaustive workspace scan',
            'One trace per session (the latest); its conversation is assumed to hold the whole session',
            'Sessions are grouped by session id, else thread id, else prompt cache key (Codex), else the '
            'previous_response_id chain (storq); a trace with none of these is its own session',
            'Repository identity and skill paths are not checked in traces; a load is identified by the exact skill '
            'text it returned',
        ],
    }
    loop.write_json(output / 'collection.json', report)
    return report


def converted(convert: Any, *args: Any) -> AtifTrajectory | str:
    """The trajectory, or why the source could not be converted (kept as a skip reason, never dropped)."""
    try:
        return convert(*args)
    except ValueError as error:
        return f'Not convertible to ATIF: {str(error)[:200]}'


Candidate = tuple[str, str | None, str | None, AtifTrajectory | str]


def write(
    target: Target, candidates: list[Candidate], output: Path, meta: dict[str, Any], limits: list[str]
) -> dict[str, Any]:
    """Keep sessions with a confirmed load; record every rejection with its reason."""
    sessions, skipped, seen = [], [], set()
    for source, session_id, cwd, trajectory in candidates:
        if isinstance(trajectory, str):
            skipped.append({'source': source, 'reason': trajectory})
            continue
        if not session_id or session_id in seen:
            skipped.append({'source': source, 'reason': 'Missing, duplicate or unattributable session id'})
            continue
        loads, reason = target.loads(trajectory)
        if not loads:
            skipped.append({'source': source, 'reason': reason or 'No confirmed load of the selected skill'})
            continue
        seen.add(session_id)
        sessions.append({
            'id': session_id,
            'skill_hash': target.hash,
            'cwd': cwd,
            'loads': [{'step': step, 'skill': target.skill} for step in loads],
            'trajectory': trajectory.to_json_dict(),
        })
    output.mkdir(parents=True, exist_ok=False)
    report = {
        'schema_version': 2,
        'skill': target.skill,
        'skill_hash': target.hash,
        **meta,
        'loaded_sessions': len(sessions),
        'skipped': skipped,
        'complete': False,
        'limitations': limits,
    }
    loop.write_json(output / 'sessions.json', sessions)
    loop.write_json(output / 'collection.json', report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--skill', required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--trace-metadata', metavar='KEY=VALUE', help='Orq traces tagged with this metadata pair')
    source.add_argument('--claude-projects', type=Path, help='Claude Code projects dir, e.g. ~/.claude/projects')
    source.add_argument('--atif', type=Path, nargs='+', help='ATIF trajectory JSON files')
    source.add_argument('--responses', type=Path, help='Index of Orq get-conversation exports')
    parser.add_argument('--loaded-from', type=Path, help='Skill directory a Claude Code Skill call loaded from')
    parser.add_argument('--since', default='7d', help='Trace window start for --trace-metadata (default 7d)')
    parser.add_argument('--until', default='now', help='Trace window end for --trace-metadata (default now)')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.trace_metadata:
        report = collect_traces(args.repo, args.skill, args.trace_metadata, args.since, args.until, args.out)
    elif args.claude_projects:
        if not args.loaded_from:
            parser.error('--claude-projects needs --loaded-from')
        report = collect_claude(args.repo, args.skill, args.claude_projects, args.loaded_from, args.out)
    elif args.atif:
        report = collect_atif(args.repo, args.skill, args.atif, args.out, args.loaded_from)
    else:
        report = collect_responses(args.repo, args.skill, args.responses, args.out)
    print(
        json.dumps({k: v for k, v in report.items() if k != 'skipped'} | {'skipped': len(report['skipped'])}, indent=2)
    )


if __name__ == '__main__':
    main()
