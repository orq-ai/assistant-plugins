# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "evaluatorq>=1.4.0",
#     "fire>=0.7.0",
#     "httpx>=0.27",
#     "loguru>=0.7.3",
#     "python-dotenv>=1.2.1",
#     "tenacity>=8.0",
#     "truststore>=0.9; sys_platform == 'win32'",
# ]
# ///
"""Step 3 — stability run: re-judge every datapoint N times per model.

Reconstructs the audited judge (judge prompt + judge model from
`evaluator.json`) as an evaluatorq judge and runs it
`repetitions=N` times per datapoint via `run_jury` — the only place the
repetitions flag and a client-side temperature actually take effect (the hosted
orq path supports neither). The N raw verdicts per model and row land in
`stability.json` for the instability and cross-model analysis in step 4.

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
from lib.content import traces_fingerprint
from lib.judge import JudgeSpec, make_judge_client, make_replacements, run_jury_for_row
from lib.panel import resolve_panel

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
    # Verdict space (RES-978 §5.4): the judge emits + parses per output type, and
    # metrics dispatches to the matching instability formula. Carried from
    # evaluator.json; boolean is the default when absent (older run dirs).
    output_type = (evaluator.get('output_type') or 'boolean').strip().lower()
    categorical_labels = evaluator.get('categorical_labels') or []
    scale_raw = evaluator.get('scale')
    scale = tuple(scale_raw) if isinstance(scale_raw, (list, tuple)) and len(scale_raw) == 2 else None
    if not judge_model:
        raise RuntimeError(
            'evaluator.json has no judge_model — cannot reconstruct the judge. '
            'Inspect evaluator.json["raw"] and set the model field.'
        )

    # Jury panel (RES-1638): extra models judge the same rows with the same prompt.
    # The aligned judge stays `judge_model`; the others are evidence about it.
    panel_models = [m for m in resolve_panel(overrides.get('panel_models'), cfg) if m != judge_model]
    default_repeats = cfg.get('panel_repeats', 3) if panel_models else cfg.get('n_repeats', 5)
    n_repeats = int(overrides.get('n_repeats') or default_repeats)
    models = [judge_model, *panel_models]

    sem = asyncio.Semaphore(max_concurrency)
    client = make_judge_client()
    n_calls = len(indexed) * n_repeats * len(models)
    logger.info(
        f'Stability: {len(indexed)} rows × {n_repeats} repeats × {len(models)} model(s) = {n_calls} '
        f'judge calls (judge={judge_model}, panel={panel_models or "none"}, temp={temperature}, '
        f'concurrency={max_concurrency})'
    )

    async def _judge(model: str, spec: JudgeSpec) -> dict[str, Any]:
        async with sem:
            try:
                return await run_jury_for_row(
                    spec, model, client=client, repetitions=n_repeats,
                    output_type=output_type, labels=categorical_labels, scale=scale,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    'success': False, 'error': f'{type(exc).__name__}: {exc}',
                    'repetitions': [], 'repetitions_failed': n_repeats, 'n_wrong_output_type': 0,
                    'value': None, 'explanation': None, '_raised': True,
                }

    async def _one(idx: int, row: dict[str, Any]) -> dict[str, Any]:
        """`idx` is the row's position in traces.jsonl, not in the filtered list."""
        spec = JudgeSpec(
            prompt_template=prompt_template,
            replacements=make_replacements(variables, row),
            temperature=temperature,
        )
        t0 = time.monotonic()
        results = await asyncio.gather(*(_judge(m, spec) for m in models))
        res = results[0]
        n_failed = int(res.get('repetitions_failed') or 0)
        # Keep evaluatorq's successful typed abstention distinct from an
        # all-failed vote. The former has no usable repetitions but enters
        # the annotation queue; the latter remains a provider error.
        if res.get('_raised'):
            ok, err = False, res['error']
            logger.error(f'✗ stability row {idx} failed — {err}')
        elif not res.get('success', False) or n_failed >= n_repeats:
            ok = False
            err = res.get('error') or (
                'all repetitions off-contract (abstained)'
                if int(res.get('n_wrong_output_type') or 0) >= n_repeats and n_failed == 0
                else 'all repetitions failed (no usable verdict)'
            )
            logger.error(f'✗ stability row {idx}: 0/{n_repeats} usable verdicts — {err}')
        else:
            ok = True
            err = None
            if n_failed:
                logger.warning(
                    f'⚠ stability row {idx}: {n_failed}/{n_repeats} repetitions failed (vote still decisive)'
                )
        record = {
            'source_index': idx,
            'query': row.get('query', ''),
            'output': row.get('output', ''),
            'messages': row.get('messages'),
            # Ground truth rides through so metrics.py can score CORRECTNESS, not
            # just self-consistency. A dataset row's `expected_output` landed in
            # `reference` and stopped here, because an evaluator that declares no
            # {{reference}} variable never renders it — so the one field that can
            # close this method's central blind spot was collected and dropped.
            'reference': row.get('reference', ''),
            # Seeded-row provenance (RES-980 §11.6) rides through so build_queue /
            # the report can flag synthetic or dataset-sourced datapoints.
            'synthetic': row.get('synthetic', False),
            'source': row.get('source'),
            'prod_judge_value': row.get('judge_value'),
            'success': ok,
            'error': err,
            'repetitions': res['repetitions'],
            'repetitions_failed': res['repetitions_failed'],
            'n_wrong_output_type': int(res.get('n_wrong_output_type') or 0),
            'aggregate_value': res['value'],
            'representative_explanation': res.get('explanation'),
            'elapsed_s': time.monotonic() - t0,
        }
        if panel_models:
            # Every model's votes, the aligned judge first. A failed panel model is
            # recorded, not raised: it drops out of the panel signals in metrics.py.
            record['panel'] = [
                {
                    'model': m,
                    'success': bool(r.get('success')) and not r.get('_raised'),
                    'error': r.get('error'),
                    'repetitions': r.get('repetitions') or [],
                    'repetitions_failed': r.get('repetitions_failed'),
                    'n_wrong_output_type': int(r.get('n_wrong_output_type') or 0),
                    'value': r.get('value'),
                    'explanation': r.get('explanation'),
                }
                for m, r in zip(models, results)
            ]
        return record

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
            'panel_models': models if panel_models else [],
            'output_type': output_type,
            'n_repeats': n_repeats,
            'temperature': temperature,
            # Identity of the traces.jsonl these source_index values point into, so
            # a later consumer can tell that the file was rewritten underneath them
            # (lib.content.traces_fingerprint). Over the FULL file, not the judged
            # subset — source_index is a position in the file.
            'traces_fingerprint': traces_fingerprint(rows),
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
    panel_models: str | list[str] | None = None,
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
        panel_models: Extra judge models, comma-separated `<provider>/<model>`
            slugs, that judge the same rows with the same prompt (overrides config
            `panel_models`). Each costs as much as the judge itself.
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
                'panel_models': panel_models,
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
