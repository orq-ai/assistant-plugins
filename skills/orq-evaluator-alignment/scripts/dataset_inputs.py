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
         dataset_id: str | None = None, limit: int = 200) -> str:
    """Append a dataset's datapoints to traces.jsonl as fresh rows."""
    if not dataset_id:
        raise SystemExit('Pass --dataset_id (run `dataset_inputs.py list` to find it).')
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
    rows, skipped = seed.rows_from_datapoints(datapoints, variables, tag={'source': f'dataset:{dataset_id}'})

    traces_path = out_dir / 'traces.jsonl'
    existing = runner.read_jsonl(traces_path) if traces_path.exists() else []
    runner.write_jsonl(traces_path, existing + rows)
    logger.info(
        f'✓ Added {len(rows)} dataset rows to traces.jsonl '
        f'({len(existing) + len(rows)} total); {len(skipped)} skipped (unmappable).'
    )
    for s in skipped[:5]:
        logger.warning(f"  skipped datapoint #{s['index']}: missing {s['missing']}")
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire({'list': list_datasets, 'pull': pull})
