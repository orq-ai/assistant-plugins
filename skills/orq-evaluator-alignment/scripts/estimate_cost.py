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
"""Step 3 helper — project the stability run's cost before any judge calls.

The conductor shows this at the experiment-setup confirmation and waits for an
explicit go-ahead (design §2 step 3, §7). Same stop-point as the cost gate.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/estimate_cost.py --run_dir runs/<key>_<ts>
"""

from __future__ import annotations

import fire
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner
from lib.cost import format_projection, project_stability_cost


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    n_repeats: int | None = None,
    num_samples: int | None = None,
) -> str:
    """Print a ballpark cost for the stability run over a run directory."""
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run fetch_traces.py first.')

    evaluator = runner.read_json(out_dir / 'evaluator.json')
    rows = runner.read_jsonl(out_dir / 'traces.jsonl')
    proj = project_stability_cost(
        judge_model=evaluator['judge_model'],
        rows=rows,
        n_repeats=int(n_repeats or cfg.get('n_repeats', 5)),
        num_samples=num_samples if num_samples is not None else cfg.get('num_samples', -1),
    )
    logger.info(format_projection(proj))
    print(format_projection(proj))
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
