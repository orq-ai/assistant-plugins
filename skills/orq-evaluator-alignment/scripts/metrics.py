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
"""Step 5 — binary flip-rate and agreement metrics over the stability run.

Consumes `stability.json` (per-row list of N repeated verdicts) and produces
`metrics.json`:
  - per-row flip-rate (1 − mode_rate) + pairwise agreement, most-ambiguous-first
  - dataset-level 1-Flip Consistency, Fleiss' κ and Gwet's AC1, True prevalence
  - a human-readable flip summary the conductor reports back to the user

Binary-only: the formulas are lifted from the validated
`stability_metrics/compute_metrics.py` and trimmed to the boolean case (the
verbosity-GLM / swap-invariance families are out of V1 scope). Cheap to rerun —
it never re-invokes the judge.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import fire
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value) if value in (0, 1) else None
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {'true', 'yes', 'pass', '1'}:
            return True
        if v in {'false', 'no', 'fail', '0'}:
            return False
    return None


def _row_bools(row: dict[str, Any]) -> list[bool]:
    out: list[bool] = []
    for v in row.get('repetitions', []):
        b = _coerce_bool(v)
        if b is not None:
            out.append(b)
    return out


def _per_row(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    pairwise_sum = 0.0
    measurable = 0
    for row in rows:
        bools = _row_bools(row)
        k = len(bools)
        entry: dict[str, Any] = {
            'source_index': row.get('source_index'),
            'n_successful_repeats': k,
            'query': row.get('query', ''),
            'output': row.get('output', ''),
            'messages': row.get('messages'),
            'representative_explanation': row.get('representative_explanation'),
        }
        if k < 2:
            entry.update({'mode_rate': None, 'mode_value': None, 'flip_rate': None, 'pairwise_agreement': None})
            entries.append(entry)
            continue
        n_true = sum(bools)
        n_false = k - n_true
        pairwise = (n_true * (n_true - 1) + n_false * (n_false - 1)) / (k * (k - 1))
        (mode_value, mode_count), = Counter(bools).most_common(1)
        mode_rate = mode_count / k
        entry.update(
            {
                'mode_rate': mode_rate,
                'mode_value': mode_value,
                'flip_rate': 1.0 - mode_rate,
                'pairwise_agreement': pairwise,
                'n_true': n_true,
                'n_false': n_false,
            }
        )
        entries.append(entry)
        measurable += 1
        pairwise_sum += pairwise

    # Most-ambiguous-first: highest flip-rate (lowest pairwise) leads; rows with
    # too few repeats sort to the end.
    entries.sort(
        key=lambda e: (
            e['pairwise_agreement'] is None,
            e['pairwise_agreement'] if e['pairwise_agreement'] is not None else 1.0,
        )
    )
    summary = {
        'measurable_rows': measurable,
        'one_flip_consistency': (pairwise_sum / measurable if measurable else None),
    }
    return entries, summary


def _panel_agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fleiss' κ and Gwet's AC1 across the N repeats (binary, variable raters)."""
    n_true_total = 0
    n_total = 0
    p_i_sum = 0.0
    measurable = 0
    for row in rows:
        bools = _row_bools(row)
        k = len(bools)
        if k < 2:
            continue
        n_true = sum(bools)
        n_false = k - n_true
        p_i_sum += (n_true * (n_true - 1) + n_false * (n_false - 1)) / (k * (k - 1))
        n_true_total += n_true
        n_total += k
        measurable += 1
    if measurable == 0:
        return {'fleiss_kappa': None, 'gwet_ac1': None, 'prevalence_true': None, 'measurable_rows': 0}
    p_bar = p_i_sum / measurable
    pi_true = n_true_total / n_total
    pi_false = 1.0 - pi_true

    def _coef(p_e: float) -> float | None:
        denom = 1.0 - p_e
        return None if denom == 0 else (p_bar - p_e) / denom

    return {
        'fleiss_kappa': _coef(pi_true**2 + pi_false**2),
        'gwet_ac1': _coef(2.0 * pi_true * pi_false),
        'prevalence_true': pi_true,
        'measurable_rows': measurable,
    }


def _flip_report(per_row: list[dict[str, Any]], panel: dict[str, Any], one_flip: float | None, n_rows: int) -> str:
    flipped = [e for e in per_row if e.get('flip_rate') not in (None, 0.0)]
    measurable = panel['measurable_rows']

    def _fmt(v: Any) -> str:
        return f'{v:.3f}' if isinstance(v, (int, float)) else 'n/a'

    lines = [
        f'Flip summary over {n_rows} datapoints ({measurable} with ≥2 valid repeats):',
        f'  - {len(flipped)} datapoints flipped (judge not unanimous).',
        f'  - 1-Flip Consistency: {_fmt(one_flip)} (1.0 = never flips).',
        f"  - Gwet AC1: {_fmt(panel['gwet_ac1'])}   Fleiss κ: {_fmt(panel['fleiss_kappa'])}"
        f"   (True prevalence {_fmt(panel['prevalence_true'])}).",
    ]
    if flipped:
        worst = flipped[:5]
        lines.append('  - Most-ambiguous datapoints (highest flip-rate):')
        for e in worst:
            lines.append(
                f"      #{e['source_index']}: flip_rate={_fmt(e['flip_rate'])} "
                f"({e.get('n_true', '?')}T/{e.get('n_false', '?')}F)"
            )
    return '\n'.join(lines)


def main(run_dir: str | None = None, config: str = 'config.toml') -> str:
    """Compute flip metrics for a run directory's stability.json."""
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run stability.py first.')

    stability = runner.read_json(out_dir / 'stability.json')
    rows = stability.get('rows', [])
    per_row, sc = _per_row(rows)
    panel = _panel_agreement(rows)
    report = _flip_report(per_row, panel, sc['one_flip_consistency'], len(rows))

    n_flipped = sum(1 for e in per_row if e.get('flip_rate') not in (None, 0.0))
    metrics = {
        'metadata': stability.get('metadata', {}),
        'scores': {
            'num_rows': len(rows),
            'measurable_rows': sc['measurable_rows'],
            'n_flipped': n_flipped,
            'one_flip_consistency': sc['one_flip_consistency'],
            'fleiss_kappa': panel['fleiss_kappa'],
            'gwet_ac1': panel['gwet_ac1'],
            'prevalence_true': panel['prevalence_true'],
        },
        'flip_report': report,
        'per_row': per_row,
    }
    runner.write_json(out_dir / 'metrics.json', metrics)
    logger.info(f'✓ Wrote {out_dir / "metrics.json"}')
    for line in report.splitlines():
        logger.info(line)
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
