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


def _diff(old: str, new: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile='current_prompt', tofile='proposed_prompt', lineterm=''
    )
    return '\n'.join(lines)


async def _create(evaluator: dict[str, Any], prompt: str, key: str, path: str) -> dict[str, Any]:
    async with OrqClient() as client:
        result = await client.create_boolean_evaluator(
            key=key,
            path=path,
            prompt=prompt,
            model=evaluator['judge_model'],
            description=f'Human-aligned rewrite of evaluator {evaluator["id"]} (RES-930).',
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
    new_prompt = (Path(edits).read_text(encoding='utf-8') if edits else (out_dir / 'new_prompt.md').read_text(encoding='utf-8')).strip()
    aggregated = (out_dir / 'aggregated.md').read_text(encoding='utf-8')

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

    if not status['var_check_passed'] and not force:
        raise SystemExit(
            'Refusing to create: variable-preservation check failed. '
            'Fix new_prompt.md (or rerun rewrite_eval.py), or pass --force to override (unsafe).'
        )

    approval = {
        'approved': True,
        'edits': edits,
        'used_prompt_source': 'edits' if edits else 'new_prompt.md',
        'forced_var_check': bool(force and not status['var_check_passed']),
        'timestamp': runner.utc_timestamp(),
    }
    runner.write_json(out_dir / 'approval.json', approval)

    source_key = evaluator.get('key') or runner.slugify(evaluator['id'])
    new_key = f'{source_key}-aligned-{runner.utc_timestamp()}'
    path = evaluator.get('raw', {}).get('path') or source_key
    created = asyncio.run(_create(evaluator, new_prompt, new_key, path))

    new_eval = {
        'id': created['id'],
        'key': created['key'],
        'source_evaluator_id': evaluator['id'],
        'judge_model': evaluator['judge_model'],
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
