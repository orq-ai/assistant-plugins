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
"""Option 4 — second-model disagreement probe (RES-980 §11.3).

When the primary judge is unanimous everywhere (flat instability, empty confuser
queue), re-judge the SAME datapoints with a DIFFERENT model and compare the two
majority verdicts. Datapoints where the two models disagree are confusers even
though each model agrees with itself — the grey zone a single flat judge hides.

Orchestration mirrors retest: build a `cross_model/` sub-run whose evaluator.json
carries the 2nd model, re-run the EXISTING stability main over it, then compare
per-type via `lib.cross_model`. Writes `cross_model.json` in the parent run dir.

Usage:
    uv run scripts/cross_model.py --run_dir runs/<key>_<ts> --model anthropic/claude-haiku-4-5
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import cross_model as cm
from lib import runner

load_dotenv()

_DEFAULT_NUMERIC_TOL = 0.5


def _verdicts_by_index(stability: dict[str, Any]) -> dict[Any, Any]:
    return {r['source_index']: r.get('aggregate_value') for r in stability.get('rows', [])}


def _build_probe_dir(out_dir: Path, model: str) -> Path:
    """Materialize `cross_model/`: the same datapoints, judged by the 2nd model."""
    evaluator = runner.read_json(out_dir / 'evaluator.json')
    traces = runner.read_jsonl(out_dir / 'traces.jsonl')
    if not traces:
        raise SystemExit(f'No datapoints in {out_dir / "traces.jsonl"} — nothing to probe.')
    probe = out_dir / 'cross_model'
    probe.mkdir(parents=True, exist_ok=True)
    runner.write_json(probe / 'evaluator.json', {**evaluator, 'judge_model': model})
    runner.write_jsonl(probe / 'traces.jsonl', traces)
    return probe


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    model: str | None = None,
    num_samples: int | None = None,
    n_repeats: int | None = None,
    tol: float | None = None,
) -> str:
    """Re-judge with a 2nd model and record the cross-model disagreers as confusers.

    `--model` is the alternate judge slug (a routable `<provider>/<model>`, resolved
    the same way as the primary judge override in step 2). `--tol` is the numeric
    disagreement band (default 0.5 raw, as in retest).
    """
    if not model:
        raise SystemExit('Pass --model <2nd judge slug> (a routable provider/model, e.g. anthropic/claude-haiku-4-5).')
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run stability.py first.')
    if not (out_dir / 'stability.json').exists():
        raise SystemExit(f'No stability.json in {out_dir}. Run stability.py (model A) before the probe.')

    evaluator = runner.read_json(out_dir / 'evaluator.json')
    output_type = (evaluator.get('output_type') or 'boolean').strip().lower()
    model_a = evaluator.get('judge_model')
    a_by_idx = _verdicts_by_index(runner.read_json(out_dir / 'stability.json'))

    probe = _build_probe_dir(out_dir, model)
    from stability import main as stability_main  # noqa: PLC0415 — heavy import guarded

    stability_main(run_dir=str(probe), config=config, num_samples=num_samples, n_repeats=n_repeats, metrics=False)
    b_by_idx = _verdicts_by_index(runner.read_json(probe / 'stability.json'))

    resolved_tol = float(tol if tol is not None else cfg.get('numeric_tol', _DEFAULT_NUMERIC_TOL))
    rows = cm.cross_model_rows(output_type, a_by_idx, b_by_idx, tol=resolved_tol)
    disagree = [r for r in rows if r['disagree']]
    payload = {
        'metadata': {
            'output_type': output_type,
            'model_a': model_a,
            'model_b': model,
            'n_compared': len(rows),
            'n_disagree': len(disagree),
            'tol': resolved_tol,
            'timestamp': runner.utc_timestamp(),
        },
        'rows': rows,
        'disagreeing_indices': [r['source_index'] for r in disagree],
    }
    runner.write_json(out_dir / 'cross_model.json', payload)
    logger.info(
        f'✓ Cross-model probe ({model_a} vs {model}): {len(disagree)}/{len(rows)} '
        'datapoints disagree → confusers for the grey-zone stage.'
    )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
