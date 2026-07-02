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
"""Step 10 — optional retest: does the rewritten judge align better?

Re-judges the annotated datapoints (the ones with human ground truth) using the
NEW prompt and compares its agreement with the human labels against the original
judge's. Writes `experiment_report.md`.

Scope note (design §5 step 10, §1): this measures alignment ONLY on the
annotated, mostly high-flip items. It does not — and V1 cannot — surface the
consistently-wrong blind spot, so the report states that limitation explicitly.
The resumable run-directory is the hook a future scheduler would re-enter; no
scheduler is built here.

Repeats (N) default to 5 and should be confirmed with the user at the retest
gate. The recommended N is variance-aware: rows that flipped a lot during the
stability run get more repeats here too, so a noisy item's new-judge verdict is
as trustworthy as the 5-rep `old_judge` it is compared against. Run with
`--recommend_only` to print the suggestion (and its basis) without judging.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/run_experiment.py --run_dir runs/<key>_<ts> --recommend_only
    uv run scripts/run_experiment.py --run_dir runs/<key>_<ts> --repeats 9
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
from lib.judge import JudgeSpec, make_judge_client, make_replacements, run_jury_for_row

load_dotenv()


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


def _labeled_indices(annotations: dict[str, Any]) -> list[int]:
    """source_index of every datapoint the human actually labeled True/False."""
    return [
        int(k)
        for k, a in annotations.items()
        if a.get('status') == 'labeled' and isinstance(a.get('value'), bool)
    ]


def _recommend_from_flips(
    flip_rates: list[float], stability_n: int, base: int = 5, cap: int = 15
) -> tuple[int, dict[str, Any]]:
    """Variance-aware repeat count for the retest (pure, so it is unit-testable).

    Floor at ``max(base, stability_n)`` — never re-judge with fewer repeats than
    produced ``old_judge``, else the old-vs-new agreement comparison is unfair —
    then add repeats in proportion to how much the retested rows flipped during
    the stability run (mean flip-rate, 0..0.5 for a binary judge; 0.5 is a
    coin-flip and earns the full +10). Forced odd to avoid majority ties, capped.
    """
    floor = max(base, stability_n)
    mean_flip = sum(flip_rates) / len(flip_rates) if flip_rates else 0.0
    max_flip = max(flip_rates) if flip_rates else 0.0
    extra = round((mean_flip / 0.5) * 10)
    rec = floor + extra
    if rec % 2 == 0:
        rec += 1
    rec = min(rec, cap)
    basis = {
        'recommended': rec,
        'floor': floor,
        'stability_n': stability_n,
        'base': base,
        'cap': cap,
        'n_rows': len(flip_rates),
        'mean_flip_rate': round(mean_flip, 3),
        'max_flip_rate': round(max_flip, 3),
        'extra': extra,
    }
    return rec, basis


def recommend_repeats(out_dir: Path, cfg: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Suggest a retest N from the flip-rates of the rows that will be retested."""
    metrics = runner.read_json(out_dir / 'metrics.json')
    annotations = runner.read_json(out_dir / 'annotations.json')
    flip_by_idx = {
        e.get('source_index'): e.get('flip_rate')
        for e in metrics.get('per_row', [])
        if e.get('flip_rate') is not None
    }
    labeled = _labeled_indices(annotations)
    flip_rates = [flip_by_idx[i] for i in labeled if i in flip_by_idx]
    stability_n = int(metrics.get('metadata', {}).get('n_repeats', cfg.get('n_repeats', 5)) or 5)
    base = int(cfg.get('retest_repeats', 5))
    cap = int(cfg.get('retest_repeats_cap', 15))
    return _recommend_from_flips(flip_rates, stability_n, base=base, cap=cap)


async def _retest(out_dir: Path, cfg: dict[str, Any], repeats: int, temperature: float) -> dict[str, Any]:
    evaluator = runner.read_json(out_dir / 'evaluator.json')
    stability = runner.read_json(out_dir / 'stability.json')
    annotations = runner.read_json(out_dir / 'annotations.json')

    new_prompt_path = out_dir / 'new_prompt.md'
    if (out_dir / 'new_evaluator.json').exists():
        new_prompt = runner.read_json(out_dir / 'new_evaluator.json')['prompt']
    elif new_prompt_path.exists():
        new_prompt = new_prompt_path.read_text(encoding='utf-8').strip()
    else:
        raise RuntimeError('No new_prompt.md / new_evaluator.json — run rewrite_eval.py first.')

    rows_by_idx = {r['source_index']: r for r in stability.get('rows', [])}
    variables = evaluator.get('variables', [])
    judge_model = evaluator['judge_model']
    client = make_judge_client()
    sem = asyncio.Semaphore(int(cfg.get('max_concurrency', 8)))

    labeled = [
        (int(k), _coerce_bool(a.get('value')))
        for k, a in annotations.items()
        if a.get('status') == 'labeled' and isinstance(a.get('value'), bool)
    ]

    async def _one(idx: int, human: bool) -> dict[str, Any]:
        row = rows_by_idx.get(idx)
        if row is None:
            return {'source_index': idx, 'skipped': True}
        spec = JudgeSpec(
            prompt_template=new_prompt,
            replacements=make_replacements(variables, row),
            temperature=temperature,
        )
        async with sem:
            res = await run_jury_for_row(spec, judge_model, client=client, repetitions=repeats)
        return {
            'source_index': idx,
            'human': human,
            'old_judge': _coerce_bool(row.get('aggregate_value')),
            'new_judge': _coerce_bool(res['value']),
        }

    results = [r for r in await asyncio.gather(*(_one(i, h) for i, h in labeled)) if not r.get('skipped')]
    return {'results': results, 'temperature': temperature, 'repeats': repeats}


def _report(data: dict[str, Any], evaluator_id: str) -> tuple[str, dict[str, Any]]:
    rows = data['results']
    n = len(rows)

    def _agree(key: str) -> float | None:
        usable = [r for r in rows if isinstance(r['human'], bool) and isinstance(r[key], bool)]
        if not usable:
            return None
        return sum(1 for r in usable if r[key] == r['human']) / len(usable)

    old_a = _agree('old_judge')
    new_a = _agree('new_judge')

    def _fmt(v: float | None) -> str:
        return f'{v:.1%}' if isinstance(v, float) else 'n/a'

    lines = [
        '# Evaluator alignment — retest report (step 10)',
        '',
        f'Source evaluator: `{evaluator_id}`. Retested on **{n} annotated datapoint(s)** '
        f'({data["repeats"]} repeat(s), temperature {data["temperature"]}).',
        '',
        '| Judge | Agreement with human labels |',
        '|---|---|',
        f'| Original | {_fmt(old_a)} |',
        f'| Rewritten | {_fmt(new_a)} |',
        '',
    ]
    if isinstance(old_a, float) and isinstance(new_a, float):
        delta = new_a - old_a
        verdict = 'improved' if delta > 0 else ('regressed' if delta < 0 else 'unchanged')
        lines.append(f'**Alignment {verdict} by {delta:+.1%}** on the annotated set.')
        lines.append('')
    lines += [
        '> **Limitation (V1).** This compares alignment only on the annotated, '
        'mostly high-flip datapoints. A judge that is *consistently* wrong has a '
        'flip-rate near zero, never enters the queue, and is not measured here. '
        'Do not read a high score as proof the evaluator is well-aligned overall '
        '(design §1 known limitation).',
        '',
        '## Per-datapoint',
        '',
        '| # | human | old judge | new judge |',
        '|---|---|---|---|',
    ]
    for r in sorted(rows, key=lambda x: x['source_index']):
        lines.append(f'| {r["source_index"]} | {r["human"]} | {r["old_judge"]} | {r["new_judge"]} |')
    return '\n'.join(lines) + '\n', {'old_agreement': old_a, 'new_agreement': new_a, 'n': n}


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    repeats: int = 5,
    temperature: float = 1.0,
    recommend_only: bool = False,
) -> str | int:
    """Retest the rewritten judge against human labels and write the report.

    `--recommend_only` prints a variance-aware suggested N (and its basis) then
    exits without judging — the conductor shows this and confirms repeats with the
    user before the real run.
    """
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory.')

    rec, basis = recommend_repeats(out_dir, cfg)
    if recommend_only:
        logger.info(
            f'Suggested retest repeats: {rec}  '
            f"(floor={basis['floor']} = max(base {basis['base']}, stability N {basis['stability_n']}); "
            f"+{basis['extra']} for instability over {basis['n_rows']} retested rows, "
            f"mean flip-rate {basis['mean_flip_rate']}, max {basis['max_flip_rate']}; capped at {basis['cap']})."
        )
        print(rec)
        return rec

    if repeats < basis['stability_n']:
        logger.warning(
            f'⚠ repeats={repeats} is below the stability N ({basis["stability_n"]}) that produced '
            f'old_judge — the old-vs-new comparison will not be apples-to-apples.'
        )
    logger.info(f'Retesting with repeats={repeats} (variance-aware suggestion was {rec}).')

    evaluator = runner.read_json(out_dir / 'evaluator.json')
    data = asyncio.run(_retest(out_dir, cfg, repeats, temperature))
    report, scores = _report(data, evaluator['id'])
    (out_dir / 'experiment_report.md').write_text(report, encoding='utf-8')
    logger.info(f'✓ Wrote {out_dir / "experiment_report.md"}')
    logger.info(f'  old agreement={scores["old_agreement"]}  new agreement={scores["new_agreement"]}  (n={scores["n"]})')
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
