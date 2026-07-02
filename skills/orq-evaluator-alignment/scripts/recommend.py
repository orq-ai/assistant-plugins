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
"""Step 8a — one meta-prompt call per annotated datapoint (parallel).

For each human-labelled item we feed the RES-916 meta-prompt the audited judge
prompt, the datapoint, the judge's N verdicts + reasoning, and the human's label
+ note. Positives and negatives go through indiscriminately: the meta-prompt
affirms what works on agreement and pinpoints the rubric gap on disagreement.
Each call returns one structured `{reasoning, recommendation}` — written to
`recommendations.json`.

The meta-prompt embeds the judge prompt (which carries its own `{{query}}` /
`{{output}}` tokens) as a variable value. `render_template` substitutes the four
meta-prompt variables in a single pass and does not re-scan the inserted text,
so those nested tokens stay literal — the model sees the real rubric. (The one
backend that would re-template them, `orq_deployment`, self-references them; see
model_backend.)

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/recommend.py --run_dir runs/<key>_<ts>
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

load_dotenv()

META_PROMPT = (runner.SKILL_ROOT / 'prompts' / 'meta_prompt.md').read_text(encoding='utf-8')


def _render_meta(judge_prompt: str, row: dict[str, Any], annotation: dict[str, Any]) -> str:
    from evaluatorq.common.judge import render_template

    messages = row.get('messages')
    msg_str = messages if isinstance(messages, str) else (str(messages) if messages else '')
    input_block = (
        (f'<conversation>\n{msg_str}\n</conversation>\n' if msg_str else '')
        + f'<query>{row.get("query", "")}</query>\n'
        + f'<assistant_output>{row.get("output", "")}</assistant_output>'
    )
    reps = row.get('repetitions', [])
    judge_block = (
        f'verdicts across {len(reps)} repeats: {reps}\n'
        f'representative explanation: {row.get("representative_explanation") or "(none)"}'
    )
    human_block = (
        f'explanation: {annotation.get("explanation") or "(none)"}\n'
        f'correction: {annotation.get("value")}'
    )
    return render_template(
        META_PROMPT,
        {
            'evaluator_prompt': judge_prompt,
            'input': input_block,
            'judge_output': judge_block,
            'human_annotation': human_block,
        },
    )


def _parse_recommendation(text: str) -> dict[str, Any]:
    import json
    import re

    # Tolerate a ```json fence or leading prose around the JSON object.
    m = re.search(r'\{.*\}', text, re.DOTALL)
    blob = m.group(0) if m else text
    obj = json.loads(blob)
    return {'reasoning': obj.get('reasoning', ''), 'recommendation': obj.get('recommendation', '')}


async def _run(out_dir: Path, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    evaluator = runner.read_json(out_dir / 'evaluator.json')
    stability = runner.read_json(out_dir / 'stability.json')
    annotations = runner.read_json(out_dir / 'annotations.json')

    rows_by_idx = {r['source_index']: r for r in stability.get('rows', [])}
    labeled = [
        (int(k), a)
        for k, a in annotations.items()
        if a.get('status') == 'labeled' and isinstance(a.get('value'), bool)
    ]
    if not labeled:
        raise RuntimeError('No labeled annotations in annotations.json — run the annotation step first.')

    backend = get_backend(cfg)
    sem = asyncio.Semaphore(int(cfg.get('recommend_concurrency', 4)))
    judge_prompt = evaluator['prompt']

    async def _one(idx: int, annotation: dict[str, Any]) -> dict[str, Any]:
        row = rows_by_idx.get(idx)
        if row is None:
            return {'source_index': idx, 'error': 'no stability row for this annotation', 'success': False}
        prompt = _render_meta(judge_prompt, row, annotation)
        async with sem:
            try:
                res = await backend.complete(prompt)
                parsed = _parse_recommendation(res.text)
                return {
                    'source_index': idx,
                    'success': True,
                    'human_value': annotation.get('value'),
                    'judge_mode_value': row.get('aggregate_value'),
                    'low_flip_sample': annotation.get('provenance', {}).get('low_flip_sample', False),
                    'reasoning': parsed['reasoning'],
                    'recommendation': parsed['recommendation'],
                    'cost_usd': res.cost_usd,
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception(f'✗ recommendation failed for #{idx}')
                return {'source_index': idx, 'success': False, 'error': f'{type(exc).__name__}: {exc}'}

    results = await asyncio.gather(*(_one(idx, a) for idx, a in labeled))
    return sorted(results, key=lambda r: r['source_index'])


def main(run_dir: str | None = None, config: str = 'config.toml') -> str:
    """Generate per-annotation recommendations via the configured backend."""
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run the annotation step first.')

    results = asyncio.run(_run(out_dir, cfg))
    ok = [r for r in results if r.get('success')]
    total_cost = sum(r.get('cost_usd', 0.0) for r in ok)
    runner.write_json(
        out_dir / 'recommendations.json',
        {
            'metadata': {
                'backend': cfg.get('backend'),
                'n_annotations': len(results),
                'n_ok': len(ok),
                'total_cost_usd': round(total_cost, 6),
            },
            'recommendations': results,
        },
    )
    logger.info(f'✓ Wrote {out_dir / "recommendations.json"} ({len(ok)}/{len(results)} ok, ${total_cost:.4f})')
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
