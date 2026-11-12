# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pydantic>=2",
#     "evaluatorq>=1.87",
# ]
# ///
"""Local, evidence-backed skill grading: shared snapshot and hashing helpers."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from evaluatorq.formats.atif import AtifStep, AtifTrajectory

SKILL_DIR = Path(__file__).resolve().parents[1]  # scripts/ sits inside the skill directory


def digest(text: str) -> str:
    """Hash UTF-8 content, rather than its pathname."""
    return hashlib.sha256(text.encode()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + '\n')


def safe_path(root: Path, relative: str) -> Path:
    """Keep reads and generated edits inside the selected skill."""
    path = root / relative
    if (
        Path(relative).is_absolute()
        or '..' in Path(relative).parts
        or not path.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError(f'Path outside skill: {relative}')
    return path


def content_text(content: Any) -> str:
    """Text of an ATIF message or observation: a string, or the text parts of a part list."""
    if isinstance(content, str):
        return content
    return '\n'.join(part.text for part in content or [] if getattr(part, 'text', None) is not None)


def trajectory(session: dict[str, Any]) -> AtifTrajectory:
    return AtifTrajectory.from_json(session['trajectory'])


def step_text(step: AtifStep) -> str:
    """What a pointer at this step indexes: message, each call (name, JSON input, raw string inputs), results."""
    parts = [content_text(step.message)]
    for call in step.tool_calls or []:
        parts.append(f'{call.function_name}: {json.dumps(call.arguments, ensure_ascii=False)}')
        parts += [value for value in call.arguments.values() if isinstance(value, str)]
    parts += [content_text(result.content) for result in step.observation.results] if step.observation else []
    return '\n'.join(part for part in parts if part)


def check_loads(session: dict[str, Any], skill: str) -> None:
    """Loads are step ids of a tool call naming the selected skill or its SKILL.md."""
    steps = trajectory(session).steps
    if not session.get('loads'):
        raise ValueError('Supply observed skill loads, not name mentions')
    for load in session['loads']:
        step = load.get('step')
        if isinstance(step, bool) or not isinstance(step, int) or not 1 <= step <= len(steps):
            raise ValueError('Invalid load step')
        if load.get('skill') != skill:
            raise ValueError('Load must identify the selected skill')
        calls = steps[step - 1].tool_calls or []
        if not any(Path(skill).name in json.dumps(c.arguments) or 'SKILL.md' in json.dumps(c.arguments) for c in calls):
            raise ValueError('Load step must hold an observed SKILL.md read or Skill call')


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def prepare(
    repo: Path,
    skill: str,
    sessions_path: Path,
    run_id: str,
    *,
    allow_empty: bool = False,
) -> Path:
    """Snapshot the client's tracked skill and explicitly versioned trajectories."""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('run_id must contain only letters, digits, underscore or hyphen')
    repo = repo.resolve()
    skill_dir = safe_path(repo, skill)
    if not (skill_dir / 'SKILL.md').is_file():
        raise ValueError('Select a skill directory containing SKILL.md')
    tracked = git(repo, 'ls-files', '--', skill).splitlines()
    files = {}
    for relative in tracked:
        path = safe_path(repo, relative)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Skill file must be a regular tracked file: {relative}')
        if git(repo, 'status', '--porcelain', '--', relative):
            raise ValueError(f'Skill has uncommitted changes: {relative}')
        files[path.relative_to(skill_dir).as_posix()] = path.read_text()
    if 'SKILL.md' not in files:
        raise ValueError('Skill must be tracked in the client repository')
    skill_hash = digest(files['SKILL.md'])
    loop_dir = repo / '.orq/skill-loop'
    loop_dir.mkdir(parents=True, exist_ok=True)
    sessions = json.loads(sessions_path.read_text())
    if not isinstance(sessions, list) or (not sessions and not allow_empty):
        raise ValueError('Supply at least one real trajectory')
    ids = set()
    for session in sessions:
        if session['id'] in ids:
            raise ValueError('Duplicate session id')
        ids.add(session['id'])
        if session.get('skill_hash') != skill_hash:
            raise ValueError(f'Session {session["id"]} has a different or unknown skill version')
        check_loads(session, skill)
    run = loop_dir / 'runs' / run_id / skill_dir.name
    run.mkdir(parents=True, exist_ok=False)
    for relative, content in files.items():
        target = safe_path(run / 'original', relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    write_json(run / 'sessions.json', sessions)
    rules = {}
    for name in ('AGENTS.md', 'CLAUDE.md'):
        if (repo / name).is_file():
            rules[name] = (repo / name).read_text()
    write_json(run / 'rules.json', rules)
    manifest = {
        'schema_version': 1,
        'repo': str(repo),
        'skill': skill,
        'skill_hash': skill_hash,
        'base_commit': git(repo, 'rev-parse', 'HEAD'),
        'run_id': run_id,
        'file_hashes': {path: digest(text) for path, text in files.items()},
        'sessions_hash': digest((run / 'sessions.json').read_text()),
        'rules_hash': digest((run / 'rules.json').read_text()),
    }
    write_json(run / 'input.json', manifest)
    return run


def load_inputs(run: Path) -> tuple[dict[str, Any], dict[str, str], dict[str, dict[str, Any]]]:
    manifest = json.loads((run / 'input.json').read_text())
    originals = {path: safe_path(run / 'original', path).read_text() for path in manifest['file_hashes']}
    if any(digest(text) != manifest['file_hashes'][path] for path, text in originals.items()):
        raise ValueError('Original skill snapshot changed')
    for filename, key in [('sessions.json', 'sessions_hash'), ('rules.json', 'rules_hash')]:
        if digest((run / filename).read_text()) != manifest[key]:
            raise ValueError(f'{filename} snapshot changed')
    sessions = {session['id']: session for session in json.loads((run / 'sessions.json').read_text())}
    expected = {
        filename: manifest[key]
        for filename, key in [
            ('baseline.json', 'baseline_hash'),
            ('comparisons.json', 'comparisons_hash'),
            ('grade-input.json', 'grade_input_hash'),
            ('grade-plan.json', 'grade_plan_hash'),
            ('collection.json', 'collection_hash'),
        ]
        if key in manifest
    } | manifest.get('snapshot_hashes', {})
    for filename, value in expected.items():
        if digest((run / filename).read_text()) != value:
            raise ValueError(f'{filename} snapshot changed')
    return manifest, originals, sessions
