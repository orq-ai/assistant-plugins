"""Run the documented commands as subprocesses: plain `python <abs path>` from an unrelated directory."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pipeline
import support
from support import SKILL, Client

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
PEP723 = re.compile(r'^# /// script\s*$(.*?)^# ///\s*$', re.MULTILINE | re.DOTALL)


def run(script: str, *args: str, cwd: Path) -> dict:
    """The no-uv fallback: the current interpreter (which has the deps) and the script's absolute path."""
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_collect_prepare_grade_baseline_report_from_any_directory(client: Client, tmp_path: Path) -> None:
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    files = []
    for session in client.sessions:
        path = tmp_path / 'atif' / f'{session["id"]}.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(session['trajectory'] | {'extra': {'cwd': str(client.repo)}}))
        files.append(str(path))
    out = tmp_path / 'collected'
    collected = run(
        'collect.py', '--repo', str(client.repo), '--skill', SKILL, '--atif', *files, '--out', str(out), cwd=elsewhere
    )
    assert (collected['loaded_sessions'], collected['skipped']) == (10, 0)
    prepared = run(
        'pipeline.py', 'prepare', '--repo', str(client.repo), '--skill', SKILL,
        '--sessions', str(out / 'sessions.json'), '--run-id', 'cli-run', cwd=elsewhere,
    )  # fmt: skip
    rundir = Path(prepared['run'])
    assert (rundir / 'agents/grader.md').is_file()
    plan = json.loads((rundir / 'grade-plan.json').read_text())
    for batch in pipeline.plan_batches(plan):
        ids = [Path(t).stem for t in batch['transcripts']]
        payload = {
            'schema_version': 1,
            'batch_id': batch['batch_id'],
            'sessions': [
                {
                    'session_id': sid,
                    'efficiency': support.FAILED if sid.startswith('failure') else support.FINE,
                    'quality': support.UNSEEN,
                    'usage': 'loaded',
                    'usage_reason': 'Recorded skill read.',
                }
                for sid in ids
            ],
            'findings': [support.finding(support.issue())] if 'failure-0' in ids else [],
        }
        (rundir / batch['output']).write_text(json.dumps(payload))
        checked = run(
            'pipeline.py', 'validate-batch', '--run', str(rundir), '--batch-id', batch['batch_id'], cwd=elsewhere
        )
        assert checked['valid'] is True
    baseline = run('pipeline.py', 'baseline', '--run', str(rundir), cwd=elsewhere)
    assert [i['id'] for i in baseline['filed_issues']] == ['result-check']
    assert (rundir / 'baseline.md').read_text().startswith('# Skill baseline\n')
    page = (rundir / 'baseline.html').read_text()
    assert run('pipeline.py', 'report', '--run', str(rundir), cwd=elsewhere) == {
        'report': str(rundir / 'baseline.html')
    }
    assert (rundir / 'baseline.html').read_text() == page


def test_pipeline_failure_is_a_clean_nonzero_exit(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / 'pipeline.py'), 'baseline', '--run', str(tmp_path / 'missing')],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.returncode == 1
    assert done.stderr.startswith('Skill pipeline failed:')


def test_every_runnable_script_declares_its_dependencies_inline() -> None:
    """`uv run <script>` builds its env from the PEP 723 block alone; the PyPI pin must be there."""
    for name in ('collect.py', 'pipeline.py', 'loop.py'):
        match = PEP723.search((SCRIPTS / name).read_text())
        assert match, name
        body = ''.join(line.removeprefix('# ').removeprefix('#') for line in match.group(1).splitlines(keepends=True))
        meta = tomllib.loads(body)
        assert set(meta['dependencies']) == {'pydantic>=2', 'evaluatorq>=1.87'}, name
