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
"""Step 9a — rewrite the judge prompt with PO2 (propose only, no creation).

Feeds the consolidated instructions (`aggregated.md`) and the current judge
prompt into PO2 (the prompt-optimisation prompt) via the configured backend. PO2
rewrites the rubric while preserving the template variables.

**Variable-preservation gate.** The set of `{{...}}` tokens in the rewrite must
exactly equal the audited evaluator's declared set — a judge whose `{{output}}`
vanished would score against nothing. If they differ we re-invoke PO2 with the
violation spelled out, looping up to `max_attempts`. If it still fails we write
the proposal but record `var_check_passed: false` so step 9b refuses to create
the evaluator until a human intervenes. We never auto-create on changed
variables (design §5 step 9a, §8).

The judge prompt embeds its own `{{query}}` / `{{output}}` tokens; the string
backends keep them literal, and `orq_deployment` self-references them
(model_backend), so PO2 sees and preserves the real variables.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/rewrite_eval.py --run_dir runs/<key>_<ts>
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner
from lib.model_backend import get_backend
from lib.orq_client import extract_template_variables

load_dotenv()

PO2_SYSTEM = (runner.SKILL_ROOT / 'prompts' / 'po2.md').read_text(encoding='utf-8')


def _user_message(instructions: str, prompt: str) -> str:
    return f'<input_instructions>\n{instructions}\n</input_instructions>\n<prompt>\n{prompt}\n</prompt>'


def _var_violation_note(source: set[str], got: set[str]) -> str:
    missing = sorted(source - got)
    added = sorted(got - source)
    parts = []
    if missing:
        parts.append(f'You DROPPED these required variables: {", ".join("{{" + v + "}}" for v in missing)}.')
    if added:
        parts.append(f'You INTRODUCED these new variables (not allowed): {", ".join("{{" + v + "}}" for v in added)}.')
    return (
        ' '.join(parts)
        + f' The rewritten prompt MUST contain exactly these template variables and no others: '
        + ', '.join('{{' + v + '}}' for v in sorted(source))
        + '. Rewrite again, preserving every one of them verbatim.'
    )


async def _rewrite(out_dir: Path, cfg: dict[str, Any], max_attempts: int) -> dict[str, Any]:
    evaluator = runner.read_json(out_dir / 'evaluator.json')
    instructions = (out_dir / 'aggregated.md').read_text(encoding='utf-8')
    source_vars = set(evaluator.get('variables', []))
    judge_prompt = evaluator['prompt']

    backend = get_backend(cfg)
    attempts: list[dict[str, Any]] = []
    current_instructions = instructions
    proposed = judge_prompt
    total_cost = 0.0

    for attempt in range(1, max_attempts + 1):
        res = await backend.complete(_user_message(current_instructions, judge_prompt), system=PO2_SYSTEM)
        proposed = res.text.strip()
        total_cost += res.cost_usd
        got_vars = set(extract_template_variables(proposed))
        ok = got_vars == source_vars
        attempts.append({'attempt': attempt, 'var_check_passed': ok, 'got_vars': sorted(got_vars)})
        if ok:
            logger.info(f'✓ Variable preservation holds on attempt {attempt}.')
            break
        logger.warning(
            f'⚠ Attempt {attempt}: variable mismatch '
            f'(want {sorted(source_vars)}, got {sorted(got_vars)}). Re-invoking PO2.'
        )
        # Append the violation to the instructions so PO2 self-corrects.
        current_instructions = instructions + '\n\n## CRITICAL FIX\n' + _var_violation_note(source_vars, got_vars)

    return {
        'proposed_prompt': proposed,
        'var_check_passed': attempts[-1]['var_check_passed'],
        'source_vars': sorted(source_vars),
        'new_vars': attempts[-1]['got_vars'],
        'attempts': attempts,
        'cost_usd': round(total_cost, 6),
    }


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    max_attempts: int = 3,
) -> str:
    """Run PO2 to propose a rewritten judge prompt (no orq object created)."""
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run aggregate.py first.')

    result = asyncio.run(_rewrite(out_dir, cfg, max_attempts))
    (out_dir / 'new_prompt.md').write_text(result['proposed_prompt'], encoding='utf-8')
    runner.write_json(
        out_dir / 'rewrite_status.json',
        {
            'var_check_passed': result['var_check_passed'],
            'source_vars': result['source_vars'],
            'new_vars': result['new_vars'],
            'attempts': result['attempts'],
            'cost_usd': result['cost_usd'],
            'backend': cfg.get('backend'),
        },
    )
    logger.info(f'✓ Wrote {out_dir / "new_prompt.md"} (PROPOSED — not yet created)')
    if not result['var_check_passed']:
        logger.error(
            '✗ Variable preservation FAILED after all attempts: '
            f'want {result["source_vars"]}, got {result["new_vars"]}. '
            'create_eval.py will refuse until this is fixed (edit new_prompt.md or rerun).'
        )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
