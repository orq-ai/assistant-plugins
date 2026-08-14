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
"""Step 4 — stability run: re-judge every datapoint N times.

Reconstructs the audited judge (judge prompt + judge model from
`evaluator.json`) as an evaluatorq single-judge panel and runs it
`repetitions=N` times per datapoint via `run_jury` — the only place the
repetitions flag and a client-side temperature actually take effect (the hosted
orq path supports neither). The N raw verdicts per row land in `stability.json`
for the flip analysis in step 5.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/stability.py --run_dir runs/<key>_<ts>
    uv run scripts/stability.py --run_dir runs/<key>_<ts> --num_samples 2  # smoke
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner
from lib.judge import JudgeSpec, make_judge_client, make_replacements, run_jury_for_row

load_dotenv()


async def _run(out_dir, cfg: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    evaluator = runner.read_json(out_dir / 'evaluator.json')
    rows = runner.read_jsonl(out_dir / 'traces.jsonl')

    # Pair every row with its index in traces.jsonl BEFORE filtering. `source_index`
    # is the row's identity across artifacts — annotations.json keys off it, and a
    # rerun under a different --include_degraded would otherwise renumber the rows
    # and land last run's labels on different datapoints.
    n_fetched = len(rows)
    indexed = list(enumerate(rows))

    # Drop degraded/hollow rows before sampling. fetch_traces marks a row
    # ``degraded`` when its span detail couldn't be recovered (empty query/output);
    # re-judging an empty output wastes calls and pollutes the flip metrics with a
    # trivially-stable verdict. Kept only when the operator opts in.
    dropped_degraded = 0
    if not bool(overrides.get('include_degraded')):
        kept = [(i, r) for i, r in indexed if not r.get('degraded')]
        dropped_degraded = len(indexed) - len(kept)
        if dropped_degraded:
            logger.warning(
                f'⚠ skipping {dropped_degraded}/{len(indexed)} degraded/hollow datapoints '
                f'(empty output, cannot be meaningfully re-judged). '
                f'Pass --include_degraded to keep them.'
            )
        indexed = kept

    n_repeats = int(overrides.get('n_repeats') or cfg.get('n_repeats', 5))
    num_samples = overrides.get('num_samples')
    num_samples = cfg.get('num_samples', -1) if num_samples is None else num_samples
    num_samples = None if num_samples in (None, -1) else int(num_samples)
    max_concurrency = int(overrides.get('max_concurrency') or cfg.get('max_concurrency', 8))
    temp_cfg = cfg.get('temperature', 1.0) if overrides.get('temperature') is None else overrides['temperature']
    temperature = None if temp_cfg is None else float(temp_cfg)

    if num_samples is not None:
        indexed = indexed[:num_samples]
    if not indexed:
        # The remedy differs: an empty file means step 2 never ran (or wrote
        # nothing), whereas rows filtered away by the degraded skip means the file
        # is fine and every datapoint was hollow — pointing that operator back at
        # fetch_traces.py sends them to re-fetch data they already have.
        if dropped_degraded:
            raise RuntimeError(
                f'No datapoints left to judge: all {dropped_degraded}/{n_fetched} rows in '
                'traces.jsonl are degraded/hollow and were skipped. The extraction is the '
                'problem, not the fetch — inspect hollow_debug.json / the traces.jsonl rows, '
                'or pass --include_degraded to judge them as-is.'
            )
        raise RuntimeError('No datapoints in traces.jsonl — run fetch_traces.py first.')

    prompt_template = evaluator['prompt']
    judge_model = evaluator['judge_model']
    variables = evaluator.get('variables', [])
    if not judge_model:
        raise RuntimeError(
            'evaluator.json has no judge_model — cannot reconstruct the judge. '
            'Inspect evaluator.json["raw"] and set the model field.'
        )

    sem = asyncio.Semaphore(max_concurrency)
    client = make_judge_client()
    logger.info(
        f'Stability: {len(indexed)} rows × {n_repeats} repeats = {len(indexed) * n_repeats} '
        f'judge calls (judge={judge_model}, temp={temperature}, concurrency={max_concurrency})'
    )

    async def _one(idx: int, row: dict[str, Any]) -> dict[str, Any]:
        """`idx` is the row's position in traces.jsonl, not in the filtered list."""
        spec = JudgeSpec(
            prompt_template=prompt_template,
            replacements=make_replacements(variables, row),
            temperature=temperature,
        )
        async with sem:
            t0 = time.monotonic()
            try:
                res = await run_jury_for_row(spec, judge_model, client=client, repetitions=n_repeats)
                n_failed = int(res.get('repetitions_failed') or 0)
                # A row counts as judged only if >=1 repetition produced a usable
                # verdict. An all-failed vote (success=False) used to be recorded
                # as ok=True with an all-None repetitions list, hiding the real
                # judge error. Surface it loudly instead.
                if not res.get('success', False) or n_failed >= n_repeats:
                    ok = False
                    err = res.get('error') or 'all repetitions failed (no usable verdict)'
                    logger.error(f'✗ stability row {idx}: 0/{n_repeats} usable verdicts — {err}')
                else:
                    ok = True
                    err = None
                    if n_failed:
                        logger.warning(
                            f'⚠ stability row {idx}: {n_failed}/{n_repeats} repetitions failed (vote still decisive)'
                        )
            except Exception as exc:  # noqa: BLE001
                logger.exception(f'✗ stability row {idx} failed')
                res = {'repetitions': [], 'repetitions_failed': n_repeats, 'value': None, 'explanation': None}
                ok, err = False, f'{type(exc).__name__}: {exc}'
        return {
            'source_index': idx,
            'query': row.get('query', ''),
            'output': row.get('output', ''),
            'messages': row.get('messages'),
            'prod_judge_value': row.get('judge_value'),
            'success': ok,
            'error': err,
            'repetitions': res['repetitions'],
            'repetitions_failed': res['repetitions_failed'],
            'aggregate_value': res['value'],
            'representative_explanation': res.get('explanation'),
            'elapsed_s': time.monotonic() - t0,
        }

    tasks = [asyncio.create_task(_one(i, r)) for i, r in indexed]
    records: list[dict[str, Any]] = []
    done = 0
    for fut in asyncio.as_completed(tasks):
        records.append(await fut)
        done += 1
        if done % max(1, len(tasks) // 10) == 0 or done == len(tasks):
            logger.info(f'  [{done}/{len(tasks)}] rows judged')
    records.sort(key=lambda r: r['source_index'])

    experiment_path = cfg.get('experiment_path') or f'evaluator-alignment/{evaluator.get("key") or evaluator["id"]}'
    return {
        'metadata': {
            'evaluator_id': evaluator['id'],
            'evaluator_key': evaluator.get('key'),
            'judge_model': judge_model,
            'n_repeats': n_repeats,
            'temperature': temperature,
            'num_rows': len(records),
            'experiment_path': experiment_path,
            'timestamp': runner.utc_timestamp(),
        },
        'rows': records,
    }


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    num_samples: int | None = None,
    n_repeats: int | None = None,
    max_concurrency: int | None = None,
    temperature: float | None = None,
    metrics: bool = True,
    include_degraded: bool = False,
) -> str:
    """Run the stability protocol over a run directory's traces.

    Args:
        run_dir: Run directory (defaults to most recent).
        config: TOML config path.
        num_samples: Cap datapoints (-1 = all). Use 2 for a smoke run.
        n_repeats: Repeats per datapoint (overrides config).
        max_concurrency: Parallel judge calls (overrides config).
        temperature: Per-call judge temperature (overrides config).
        metrics: When True (default), compute metrics on the result.
        include_degraded: Keep degraded/hollow rows (empty output) instead of
            skipping them. Off by default.
    """
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run fetch_evaluator.py / fetch_traces.py first.')

    payload = asyncio.run(
        _run(
            out_dir,
            cfg,
            {
                'num_samples': num_samples,
                'n_repeats': n_repeats,
                'max_concurrency': max_concurrency,
                'temperature': temperature,
                'include_degraded': include_degraded,
            },
        )
    )
    runner.write_json(out_dir / 'stability.json', payload)
    rows = payload['rows']
    ok = sum(1 for r in rows if r['success'])
    failed = [r for r in rows if not r['success']]
    if failed:
        # Surface the distinct underlying errors so a credentials/config problem
        # (e.g. router 500 "insufficient credits") is impossible to miss.
        by_msg: dict[str, int] = {}
        for r in failed:
            msg = r.get('error') or 'unknown error'
            by_msg[msg] = by_msg.get(msg, 0) + 1
        logger.error(f'✗ {len(failed)}/{len(rows)} rows produced no usable verdict. Distinct errors:')
        for msg, n in by_msg.items():
            logger.error(f'    [{n}x] {msg}')
    if ok == 0:
        raise SystemExit(
            f'Stability run failed: 0/{len(rows)} rows produced a usable verdict '
            f'(see judge errors above). No metrics computed — fix the judge/credentials and retry.'
        )
    logger.info(f'✓ Wrote {out_dir / "stability.json"} ({ok}/{len(rows)} rows judged)')

    if metrics:
        from metrics import main as metrics_main

        metrics_main(run_dir=str(out_dir), config=config)
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
