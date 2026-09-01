# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fire>=0.7.0",
#     "httpx>=0.27",
#     "loguru>=0.7.3",
#     "python-dotenv>=1.2.1",
#     "truststore>=0.9; sys_platform == 'win32'",
# ]
# ///
"""Option 3 — synthetic hard-point seeding (RES-980 §11.3).

The synthetic-data-generation skill (`orq-generate-synthetic-dataset`) is
conductor-prose with no callable library, so — mirroring the grey-zone pattern —
the CONDUCTOR generates the datapoints (its Mode 3: few-shot from real examples
when they exist, rubric-only when none) and writes `synthetic_datapoints.json`
(a list of orq datapoints `{inputs, messages?, expected_output?, rationale?}`).
This script then:

    uv run scripts/seed_inputs.py convert --run_dir <run_dir>
        validate each against the evaluator's variable schema + convert to trace
        rows tagged `synthetic: true`; append to traces.jsonl. Invalid datapoints
        are reported, never silently dropped.

    uv run scripts/seed_inputs.py save --run_dir <run_dir> --dataset_name "<name>"
        persist the synthetic datapoints to orq so the effort is reusable (a new
        dataset, or --dataset_id to append to an existing one). Option 2 can then
        pull them back on a later run.
"""

from __future__ import annotations

import asyncio
from typing import Any

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner, seed
from lib.orq_client import OrqClient

load_dotenv()

# orq datapoint fields accepted by POST /datapoints; drop our local `rationale`.
_ORQ_DATAPOINT_FIELDS = ('inputs', 'messages', 'expected_output')


def _resolve(run_dir: str | None, cfg: dict) -> Any:
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run fetch_evaluator.py first.')
    return out_dir


def _read_synthetic(out_dir: Any) -> list[dict[str, Any]]:
    path = out_dir / 'synthetic_datapoints.json'
    if not path.exists():
        raise SystemExit(
            f'No synthetic_datapoints.json in {out_dir}. The conductor writes it from the '
            'synthetic-data-generation step before this runs.'
        )
    data = runner.read_json(path)
    return data if isinstance(data, list) else data.get('datapoints', [])


def convert(run_dir: str | None = None, config: str = 'config.toml') -> str:
    """Validate + convert synthetic_datapoints.json → traces.jsonl (flagged synthetic)."""
    cfg = runner.load_config(config)
    out_dir = _resolve(run_dir, cfg)
    evaluator = runner.read_json(out_dir / 'evaluator.json')
    variables = evaluator.get('variables', [])

    datapoints = _read_synthetic(out_dir)
    rows, skipped = seed.rows_from_datapoints(datapoints, variables, tag={'synthetic': True})

    traces_path = out_dir / 'traces.jsonl'
    existing = runner.read_jsonl(traces_path) if traces_path.exists() else []
    runner.write_jsonl(traces_path, existing + rows)
    logger.info(
        f'✓ Added {len(rows)} synthetic rows to traces.jsonl '
        f'({len(existing) + len(rows)} total); {len(skipped)} rejected (schema-invalid).'
    )
    for s in skipped[:5]:
        logger.warning(f"  rejected synthetic datapoint #{s['index']}: missing {s['missing']}")
    print(out_dir)
    return str(out_dir)


def save(run_dir: str | None = None, config: str = 'config.toml',
         dataset_name: str | None = None, dataset_id: str | None = None) -> str:
    """Persist the synthetic datapoints to orq (new dataset or append to an existing one)."""
    if not dataset_name and not dataset_id:
        raise SystemExit('Pass --dataset_name "<name>" to create a dataset, or --dataset_id to append.')
    cfg = runner.load_config(config)
    out_dir = _resolve(run_dir, cfg)
    datapoints = _read_synthetic(out_dir)
    # Strip local-only fields (e.g. rationale) to orq's accepted datapoint shape.
    clean = [{k: dp[k] for k in _ORQ_DATAPOINT_FIELDS if k in dp} for dp in datapoints]

    async def _run() -> str:
        async with OrqClient() as client:
            target = dataset_id
            if target is None:
                created = await client.create_dataset(dataset_name)
                target = created.id
                logger.info(f"✓ Created dataset {target} ({dataset_name!r}).")
            await client.create_datapoints(target, clean)
            return target

    target = asyncio.run(_run())
    logger.info(f'✓ Saved {len(clean)} synthetic datapoints to orq dataset {target}.')
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire({'convert': convert, 'save': save})
