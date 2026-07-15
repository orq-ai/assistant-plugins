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
"""Step 8b — consolidate per-annotation recommendations into PO2 instructions.

Reads `recommendations.json` and writes `aggregated.md`: the per-annotation
prompt-level instructions that become PO2's `input_instructions` in step 9.
Splits by whether the human agreed with the judge's modal verdict — agreements
feed "strengths to preserve", disagreements feed "changes to make".

This is deliberately deterministic (no LLM call): the meta-prompt already
guarantees each recommendation is generalizable and prompt-level, so aggregation
is just splitting and grouping. The conductor may still refine `aggregated.md`
in-context before the rewrite — it is a plain markdown file.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/aggregate.py --run_dir runs/<key>_<ts>
"""

from __future__ import annotations

from typing import Any

import fire
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner


def _coerce_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {'true', '1', 'yes'}:
            return True
        if s in {'false', '0', 'no'}:
            return False
    return None


def _dedup(recs: list[dict[str, Any]]) -> list[tuple[str, list[int]]]:
    """Group identical recommendation texts, collecting their source indices."""
    grouped: dict[str, list[int]] = {}
    order: list[str] = []
    for r in recs:
        text = (r.get('recommendation') or '').strip()
        if not text:
            continue
        if text not in grouped:
            grouped[text] = []
            order.append(text)
        grouped[text].append(r['source_index'])
    return [(t, grouped[t]) for t in order]


def _section(title: str, groups: list[tuple[str, list[int]]]) -> str:
    if not groups:
        return f'## {title}\n\n_(none)_\n'
    lines = [f'## {title}\n']
    for text, idxs in groups:
        cites = ', '.join(f'#{i}' for i in sorted(idxs))
        lines.append(f'- {text}  \n  _(from {len(idxs)} datapoint(s): {cites})_')
    return '\n'.join(lines) + '\n'


def main(run_dir: str | None = None, config: str = 'config.toml') -> str:
    """Aggregate recommendations into aggregated.md."""
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run recommend.py first.')

    data = runner.read_json(out_dir / 'recommendations.json')
    recs = [r for r in data.get('recommendations', []) if r.get('success')]
    if not recs:
        raise SystemExit('No successful recommendations to aggregate.')

    # Coerce both sides before comparing: human_value is a real bool, but
    # judge_mode_value is evaluatorq's vote.value passed through — a stringified
    # verdict ("true") would otherwise misclassify every row as a disagreement.
    disagreements = [r for r in recs if _coerce_bool(r.get('human_value')) != _coerce_bool(r.get('judge_mode_value'))]
    agreements = [r for r in recs if _coerce_bool(r.get('human_value')) == _coerce_bool(r.get('judge_mode_value'))]

    n_low_flip = sum(1 for r in recs if r.get('low_flip_sample'))
    header = (
        f'# Aggregated recommendations\n\n'
        f'{len(recs)} annotation(s) analysed: **{len(disagreements)} disagreement(s)** '
        f'(human ≠ judge) and **{len(agreements)} agreement(s)**'
        + (f', incl. {n_low_flip} from the low-flip sanity sample' if n_low_flip else '')
        + '.\n\n'
        '> These instructions are the input to the PO2 rewrite (step 9). Edit this '
        'file before running rewrite_eval.py if you want to adjust, drop, or '
        're-prioritise any item.\n'
    )

    body = '\n'.join(
        [
            header,
            _section('Changes to make (from disagreements)', _dedup(disagreements)),
            _section('Strengths to preserve (from agreements)', _dedup(agreements)),
        ]
    )
    (out_dir / 'aggregated.md').write_text(body, encoding='utf-8')
    logger.info(
        f'✓ Wrote {out_dir / "aggregated.md"} '
        f'({len(disagreements)} changes, {len(agreements)} affirmations)'
    )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
