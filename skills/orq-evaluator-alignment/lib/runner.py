"""Run-directory contract + config loading shared by every step.

A single alignment run owns one working directory under `runs/`:

    runs/<evaluator-key>_<timestamp>/
      evaluator.json  traces.jsonl  stability.json  metrics.json
      queue.json  annotations.json  recommendations.json  aggregated.md
      new_prompt.md  approval.json  new_evaluator.json  experiment_report.md

Each step reads the artifacts it consumes and writes the one it produces, so
any step is re-runnable in isolation against an existing run directory. This
module owns directory creation/resolution, config parsing, and the small
JSON/JSONL read-write helpers; it deliberately holds no orq or evaluatorq
imports so it stays cheap to import on Windows (project memory: heavy deps can
abort the process at import time).
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent

_SLUG_CLEANUP = re.compile(r'[^a-z0-9]+')


def slugify(text: str) -> str:
    """Lowercase and collapse every non-alphanumeric run into a single dash."""
    return _SLUG_CLEANUP.sub('-', text.lower()).strip('-')


def load_config(config: str | Path = 'config.toml') -> dict[str, Any]:
    """Load the TOML config, resolving a relative path against skill/."""
    path = Path(config)
    if not path.is_absolute():
        path = (SKILL_ROOT / path).resolve()
    with path.open('rb') as f:
        return tomllib.load(f)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')


def new_run_dir(evaluator_key: str, runs_dir: str | Path = 'runs') -> Path:
    """Create and return a fresh `runs/<key>_<ts>/` directory."""
    parent = Path(runs_dir)
    if not parent.is_absolute():
        parent = (SKILL_ROOT / parent).resolve()
    run_dir = parent / f'{slugify(evaluator_key)}_{utc_timestamp()}'
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def resolve_run_dir(run_dir: str | Path) -> Path:
    """Resolve an existing run directory, erroring if it is missing."""
    path = Path(run_dir)
    if not path.is_absolute():
        path = (SKILL_ROOT / path).resolve()
    if not path.is_dir():
        raise FileNotFoundError(
            f'Run directory not found: {path}\n'
            'Pass --run_dir pointing at an existing runs/<key>_<ts>/ folder, '
            'or run step 1 (fetch_evaluator.py) first to create one.'
        )
    return path


def latest_run_dir(runs_dir: str | Path = 'runs') -> Path | None:
    """Return the most recently created run directory, or None if there are none."""
    parent = Path(runs_dir)
    if not parent.is_absolute():
        parent = (SKILL_ROOT / parent).resolve()
    if not parent.is_dir():
        return None
    candidates = [p for p in parent.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# Trailing `_<model-slug>_<N>dp` appended to a run dir once the judge model and
# datapoint count are known (end of fetch_traces). Anchored at the end so a
# re-fetch strips the old meta and re-appends fresh values instead of stacking.
_RUN_META_SUFFIX = re.compile(r'_[a-z0-9-]+_\d+dp$')


def apply_run_meta(run_dir: Path, model: str, n_datapoints: int) -> Path:
    """Rename a run dir to embed the judge model and set size, and return it.

    The run dir is born as `<key>_<ts>` in step 1, before the model (resolved
    from spans) or the datapoint count (rows fetched) are known. Once step 2 has
    both, this appends `_<model>_<N>dp` so the folder name is self-describing.
    Idempotent: re-running the trace fetch strips any prior meta suffix first, so
    the count/model stay current instead of accumulating. No-ops (keeps the
    current dir) if the target already exists or the name would be unchanged.
    """
    model_slug = slugify(model)[:30] or 'model-unknown'
    base = _RUN_META_SUFFIX.sub('', run_dir.name)
    new_dir = run_dir.parent / f'{base}_{model_slug}_{n_datapoints}dp'
    if new_dir == run_dir or new_dir.exists():
        return run_dir
    run_dir.rename(new_dir)
    return new_dir


def read_json(path: Path) -> Any:
    # utf-8 explicit: judge explanations carry non-cp1252 bytes (emoji, smart
    # quotes) and the Windows default codec aborts on them.
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path: Path, data: Any) -> None:
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8'
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    body = '\n'.join(json.dumps(r, ensure_ascii=False) for r in rows)
    Path(path).write_text(body + ('\n' if body else ''), encoding='utf-8')
