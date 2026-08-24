# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "evaluatorq>=1.4.0",
#     "fire>=0.7.0",
#     "httpx>=0.27",
#     "loguru>=0.7.3",
#     "python-dotenv>=1.2.1",
#     "tenacity>=8.0",
# ]
# ///
"""Step 9b — create the rewritten evaluator, only after human approval.

Default (no `--approve`): a presentation pass — prints the aggregated
recommendation summary, the old→new prompt diff, and the variable-check status,
then writes nothing. This is what the conductor shows the human.

With `--approve` (and optional `--edits` to fold in inline human edits): writes
`approval.json` and, if the variable-preservation gate passed, creates a NEW
boolean orq evaluator carrying the rewritten prompt and recording
`source_evaluator_id` lineage. The original is never mutated. Creation is
refused when the variable check failed unless `--force` is passed (design §5,
§8).

Usage:
    # 1) present for review
    uv run scripts/create_eval.py --run_dir runs/<key>_<ts>
    # 2) after the human says yes
    uv run scripts/create_eval.py --run_dir runs/<key>_<ts> --approve
"""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Any

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner
from lib.orq_client import OrqClient

load_dotenv()


def _enforce_create_guards(status: dict[str, Any], force: bool) -> list[str]:
    """Refuse creation unless every rewrite_eval guard passed, naming which failed.

    rewrite_eval.py writes three checks to `rewrite_status.json`
    (`var_check_passed`, `verdict_space_ok`, `preservation_ok`), but this used to
    gate on `var_check_passed` alone — a rewrite whose verdict space had shifted
    (a dropped categorical label, a moved numeric scale) still created cleanly as
    long as the `{{...}}` variable set matched. `verdict_space_ok`/`preservation_ok`
    are newer keys than `var_check_passed`, so a pre-existing run dir written
    before they existed has neither — absence must default to passing, or every
    legacy run dir would be blocked.

    Returns the checks `--force` overrode (empty when nothing needed forcing).
    """
    checks = [
        ('var_check_passed', bool(status.get('var_check_passed', True))),
        ('verdict_space_ok', status.get('verdict_space_ok', True) is not False),
        ('preservation_ok', bool(status.get('preservation_ok', True))),
    ]
    failed = [name for name, ok in checks if not ok]
    if failed and not force:
        raise SystemExit(
            f'Refusing to create: {", ".join(failed)} failed. '
            'Fix new_prompt.md (or rerun rewrite_eval.py), or pass --force to override (unsafe).'
        )
    return failed


def _diff(old: str, new: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile='current_prompt', tofile='proposed_prompt', lineterm=''
    )
    return '\n'.join(lines)


def _source_labels(evaluator: dict[str, Any]) -> list[Any]:
    """Pull the source's declared label set, preferring the rich record shape.

    The rich ``[{value, description}]`` form lives on the API record
    (`evaluator['raw']['categorical_labels']`); the normalised top-level
    `categorical_labels` is a flat ``[value, ...]`` mirror. Prefer the rich form so
    label descriptions survive the copy; fall back to the flat strings.
    """
    rich = evaluator.get('raw', {}).get('categorical_labels')
    if isinstance(rich, list) and rich:
        return rich
    flat = evaluator.get('categorical_labels')
    return flat if isinstance(flat, list) else []


def source_name(evaluator: dict[str, Any]) -> str:
    """The source evaluator's human-readable name, for naming the aligned copy.

    Tried in order, because no single field is reliably populated — and the two
    populated fields are each other's inverse depending on how the evaluator was
    created (both shapes probed live 2026-08-14):
      1. ``key`` — empty on evaluators created through the current UI, but the ONLY
         name on ones created over the API (those come back `display_name: null`);
      2. ``raw.display_name`` — what orq shows in the evaluator list, and the only
         name a UI-created record carries;
      3. ``raw.key`` — the same field off the untouched API record;
      4. the evaluator id — last resort, and the reason an un-named source used to
         produce keys like ``01kzxt86ac82d1fvzw3s8wfv83-aligned-<ts>``.
    """
    raw = evaluator.get('raw', {})
    for candidate in (evaluator.get('key'), raw.get('display_name'), raw.get('key')):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return evaluator['id']


def aligned_key(evaluator: dict[str, Any], timestamp: str) -> str:
    """The aligned copy's key: `<source-name>-aligned-<ts>`.

    orq overwrites the `display_name` we send with the key, so the key *is* the name
    the user reads in the evaluator list — it has to mirror the source's name rather
    than its opaque id. The timestamp keeps repeat alignment runs on one evaluator
    from colliding, and ties each copy back to the run that produced it.
    """
    return f'{runner.slugify(source_name(evaluator))}-aligned-{timestamp}'


async def _resolve_path(evaluator: dict[str, Any], new_key: str) -> str:
    """Where the aligned copy is created: the source's project, same as the source.

    The evaluator record carries no `path`, so the source's own folder can't be read
    back directly — but it does name its project, and a create `path`'s first
    segment names the project. Resolving one into the other puts the copy in the
    project the source lives in instead of a new folder named after its id.

    **Which field names it depends on the endpoint** (probed live 2026-08-14):
    `GET /v2/evaluators/{id}` returns `domain_id` **and** `project_id` carrying the
    same value, while `GET /v2/evaluators` (the list) returns `project_id` with
    `domain_id: null`. Neither spelling is safe alone, so both are read.

    Falls back to a bare key (workspace root) when the project can't be resolved.
    The common reason is scope, not a missing field: a project-scoped API key sees
    only its own project on `GET /v2/projects` (and 403s on any other project by
    id), so an evaluator living elsewhere in the workspace can never be resolved to
    a key. `resolve_project_key` says which case it hit.
    """
    raw = evaluator.get('raw', {})
    project_id = raw.get('domain_id') or raw.get('project_id') or ''
    async with OrqClient() as client:
        project_key = await client.resolve_project_key(project_id)
    return f'{project_key}/{new_key}' if project_key else new_key


async def _create(evaluator: dict[str, Any], prompt: str, key: str, path: str) -> dict[str, Any]:
    # Preserve the source's verdict space: same output_type, the same label set
    # for categorical, and the same scale for numeric (forwarded only if the
    # source declared one — build_create_body never invents a scale). The
    # original evaluator is only read, never mutated.
    output_type = evaluator.get('output_type', 'boolean')
    # Preserve the source's categorical guardrail "pass" set (orq requires a
    # `values` array on a categorical guardrail_config).
    guardrail_values = (
        evaluator.get('raw', {}).get('guardrail_config', {}).get('values')
        if output_type == 'categorical'
        else None
    )
    # Send a readable name too, but do NOT rely on it: orq overwrites `display_name`
    # with the `key` on create (verified against the live API — a copy created with
    # display_name='… (aligned)' reads back with the key as its display_name). The
    # key is therefore the name the user sees, which is why `aligned_key` mirrors the
    # source's name. This stays in the body so the copy is still labelled correctly
    # if that behaviour changes.
    display_name = f'{source_name(evaluator)} (aligned)'
    async with OrqClient() as client:
        result = await client.create_evaluator(
            key=key,
            path=path,
            prompt=prompt,
            model=evaluator['judge_model'],
            description=f'Human-aligned rewrite of evaluator {evaluator["id"]} (RES-978).',
            output_type=output_type,
            display_name=display_name,
            categorical_labels=_source_labels(evaluator) if output_type == 'categorical' else None,
            # 'number' and 'numeric' are both accepted spellings across the codebase
            # (fetch_evaluator, instability, judge); gate on both so a source stored
            # as 'numeric' does not silently lose its scale on creation.
            scale=evaluator.get('scale') if output_type in ('number', 'numeric') else None,
            guardrail_values=guardrail_values,
        )
    return {'id': result.id, 'key': result.key, 'raw': result.raw}


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    approve: bool = False,
    edits: str | None = None,
    force: bool = False,
) -> str:
    """Present the rewrite for review, or create it on approval.

    Args:
        run_dir: Run directory (defaults to most recent).
        config: TOML config path.
        approve: Create the new evaluator. Without this, only presents the diff.
        edits: Path to a file with a human-edited prompt to use instead of
            new_prompt.md (folds inline edits into the created evaluator).
        force: Create even if the variable-preservation check failed (unsafe).
    """
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run rewrite_eval.py first.')

    evaluator = runner.read_json(out_dir / 'evaluator.json')
    status = runner.read_json(out_dir / 'rewrite_status.json')
    # utf-8-sig on these human-editable artifacts: a Windows editor can save
    # new_prompt.md / aggregated.md / an --edits file with a leading BOM, which
    # would otherwise be prepended to the prompt's first line and silently
    # corrupt the created evaluator. -sig strips it; no-op when absent.
    new_prompt = (
        Path(edits).read_text(encoding='utf-8-sig') if edits
        else (out_dir / 'new_prompt.md').read_text(encoding='utf-8-sig')
    ).strip()
    aggregated = (out_dir / 'aggregated.md').read_text(encoding='utf-8-sig')

    # Presentation (always shown).
    logger.info('── Aggregated recommendations ──')
    print(aggregated)
    logger.info('── Prompt diff (current → proposed) ──')
    print(_diff(evaluator['prompt'], new_prompt) or '(no textual difference)')
    logger.info(
        f'── Variable check: {"PASSED" if status["var_check_passed"] else "FAILED"} '
        f'(source={status["source_vars"]}, new={status["new_vars"]}) ──'
    )

    if not approve:
        logger.info('Presentation only. Re-run with --approve to create the new evaluator.')
        print(out_dir)
        return str(out_dir)

    forced_checks = _enforce_create_guards(status, force)

    approval = {
        'approved': True,
        'edits': edits,
        'used_prompt_source': 'edits' if edits else 'new_prompt.md',
        # Kept for artifact compat alongside the generalised `forced_checks` below;
        # set from the var check alone, as it always was.
        'forced_var_check': bool(force and not status.get('var_check_passed', True)),
        'forced_checks': forced_checks,
        'timestamp': runner.utc_timestamp(),
    }
    runner.write_json(out_dir / 'approval.json', approval)

    new_key = aligned_key(evaluator, approval['timestamp'])
    # Co-locate the aligned evaluator with its source by resolving the source's
    # project_id back into the project key that heads a create `path`.
    path = asyncio.run(_resolve_path(evaluator, new_key))
    logger.info(f'  creating {new_key!r} at path {path!r}')
    created = asyncio.run(_create(evaluator, new_prompt, new_key, path))

    new_eval = {
        'id': created['id'],
        'key': created['key'],
        'source_evaluator_id': evaluator['id'],
        'judge_model': evaluator['judge_model'],
        # Record the preserved verdict space so the retest (2.5) knows how to
        # score the created evaluator against the human labels.
        'output_type': evaluator.get('output_type', 'boolean'),
        'categorical_labels': evaluator.get('categorical_labels', []),
        'scale': evaluator.get('scale'),
        'prompt': new_prompt,
        'created_at': approval['timestamp'],
        'raw': created['raw'],
    }
    runner.write_json(out_dir / 'new_evaluator.json', new_eval)
    logger.info(f'✓ Created new evaluator {created["id"]} (key={created["key"]!r})')
    logger.info(f'  lineage: source_evaluator_id={evaluator["id"]}')
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
