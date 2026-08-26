# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fire>=0.7.0",
#     "httpx>=0.27",
#     "loguru>=0.7.3",
#     "python-dotenv>=1.2.1",
# ]
# ///
"""Option 2 — hook up an existing orq dataset as inputs (RES-980 §11.3).

For a starved run, pull rows from a workspace dataset into `traces.jsonl` as fresh
(un-judged) datapoints, so the normal stability → metrics → build_queue chain has
something to measure. Two steps:

    uv run scripts/dataset_inputs.py list --config config.toml
        list workspace datasets so the user can pick one (id + display_name)

    uv run scripts/dataset_inputs.py pull --run_dir <run_dir> --dataset_id <id>
        fetch its datapoints, map each to a trace row (§11.4), append to
        traces.jsonl tagged `source: "dataset:<id>"`. Rows carry no verdict —
        stability judges them fresh. Datapoints missing a required variable are
        reported and skipped, never fabricated.
"""

from __future__ import annotations

import asyncio

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner, seed
from lib.orq_client import OrqClient

load_dotenv()


def list_datasets(config: str = 'config.toml', limit: int = 100) -> list[dict]:
    """List workspace datasets (id + display_name) for the user to choose from."""

    async def _run() -> list[dict]:
        async with OrqClient() as client:
            return await client.list_datasets(limit=limit)

    datasets = asyncio.run(_run())
    logger.info(f'{len(datasets)} dataset(s):')
    for d in datasets:
        logger.info(f"  {d.get('_id')}  {(d.get('display_name') or d.get('name'))!r}")
    return datasets


def pull(run_dir: str | None = None, config: str = 'config.toml',
         dataset_id: str | None = None, limit: int = 200,
         map: list[str] | str | None = None) -> str:  # noqa: A002 — CLI flag name
    """Append a dataset's datapoints to traces.jsonl as fresh rows.

    Args:
        map: Explicit variable→source mappings for fields the automatic rules
            cannot resolve, e.g. `--map "log.output=messages.assistant.last"`.
            Repeatable. Sources: `inputs.<key>`, `messages.<role>.last|first`,
            `messages.all`, `expected_output`. An unknown source is an error, not
            a silent skip. The resolved mapping is written to `input_mapping.json`
            so the run is reproducible and the conductor can state what it did.
    """
    if not dataset_id:
        raise SystemExit('Pass --dataset_id (run `dataset_inputs.py list` to find it).')
    try:
        mapping = seed.parse_map_spec(map)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run fetch_evaluator.py first.')

    evaluator = runner.read_json(out_dir / 'evaluator.json')
    variables = evaluator.get('variables', [])

    async def _run() -> list[dict]:
        async with OrqClient() as client:
            return await client.list_datapoints(dataset_id, limit=limit)

    datapoints = asyncio.run(_run())
    rows, skipped = seed.rows_from_datapoints(
        datapoints, variables, tag={'source': f'dataset:{dataset_id}'}, mapping=mapping
    )

    # Report per DATASET, not per row. N identical `missing ['log.output']` lines
    # say nothing one line doesn't, and they bury the fact that resolves the
    # problem: which fields ARE present and what they could map to. Emitted
    # whenever anything was skipped, and on a total miss it is the whole answer.
    if skipped:
        inventory = seed.dataset_inventory(datapoints, variables)
        missing_fields = sorted({v for s in skipped for v in s['missing']})
        report = seed.format_inventory(inventory, len(rows), missing_fields)
        log = logger.error if not rows else logger.warning
        for line in report.splitlines():
            log(line)
        runner.write_json(out_dir / 'dataset_inventory.json', inventory)

    if not rows:
        raise SystemExit(
            f'No datapoints from dataset {dataset_id} could be mapped to what this judge reads. '
            'The inventory above lists what the dataset holds; map the missing field explicitly '
            'with --map, or pick a different dataset. Nothing was written.'
        )

    traces_path = out_dir / 'traces.jsonl'
    existing = runner.read_jsonl(traces_path) if traces_path.exists() else []
    runner.write_jsonl(traces_path, existing + rows)
    if mapping:
        n_hit = seed.count_mapping_hits(datapoints, mapping)
        runner.write_json(
            out_dir / 'input_mapping.json',
            {'dataset_id': dataset_id, 'mapping': mapping, 'n_rows': len(rows), 'n_hit': n_hit},
        )
        if n_hit:
            logger.info(f'✓ Applied --map {mapping} to {n_hit}/{len(datapoints)} datapoints (recorded in input_mapping.json)')
        else:
            logger.warning(f'⚠ --map {mapping} matched no datapoints — every row used the automatic value (recorded in input_mapping.json)')
    logger.info(
        f'✓ Added {len(rows)} dataset rows to traces.jsonl '
        f'({len(existing) + len(rows)} total); {len(skipped)} skipped (unmappable).'
    )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire({'list': list_datasets, 'pull': pull})
