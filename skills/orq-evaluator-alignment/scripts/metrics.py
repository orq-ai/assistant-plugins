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
"""Step 5 — per-row judge instability across the stability run (RES-978).

Consumes `stability.json` (per-row list of N repeated verdicts) + `evaluator.json`
(output_type, categorical_labels, scale) and writes `metrics.json`:
  - per-row unified `instability` (0..1) + `band` (stable | noisy | unreliable |
    unmeasurable), most-unstable-first, plus type-native detail
  - dataset-level mean instability + a one-line band histogram
  - a lean human-readable report the conductor reports back (§6)

All three orq output types (boolean / categorical / number) land on ONE 0..1
scale via `lib.instability`, so nothing downstream branches on type. The heavier
boolean agreement stats (Fleiss κ, Gwet AC1, pairwise) are still computed but kept
OUT of the default summary — surfaced on request. Cheap to rerun; never
re-invokes the judge.
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import fmean, pstdev
from typing import Any

import fire
from loguru import logger

import _bootstrap  # noqa: F401
from lib import instability, runner

_NUMERIC_TYPES = {'number', 'numeric'}


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


def _clean_verdicts(reps: list[Any], output_type: str) -> list[Any]:
    """The usable, canonical verdicts of a row: drop None (failed/off-contract),
    coerce to the type. instability.py only ever sees this clean list (§4a)."""
    out: list[Any] = []
    for v in reps or []:
        if v is None:
            continue
        if output_type == 'boolean':
            b = _coerce_bool(v)
            if b is not None:
                out.append(b)
        elif output_type == 'categorical':
            out.append(str(v))
        elif output_type in _NUMERIC_TYPES:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                pass
        elif output_type == 'string':
            out.append(str(v))
        else:
            out.append(v)
    return out


def _type_detail(output_type: str, clean: list[Any], k: int | None, scale: tuple[float, float] | None) -> dict[str, Any]:
    """Type-native per-row detail. For boolean this also carries the legacy
    flip_rate / mode / pairwise fields (kept for boolean-only consumers)."""
    n = len(clean)
    if output_type == 'boolean':
        n_true = sum(1 for b in clean if b)
        n_false = n - n_true
        detail: dict[str, Any] = {'n_true': n_true, 'n_false': n_false}
        if n >= 2:
            (mode_value, mode_count), = Counter(clean).most_common(1)
            mode_rate = mode_count / n
            pairwise = (n_true * (n_true - 1) + n_false * (n_false - 1)) / (n * (n - 1))
            detail.update({'mode_value': mode_value, 'mode_rate': mode_rate,
                           'flip_rate': 1.0 - mode_rate, 'pairwise_agreement': pairwise})
        else:
            detail.update({'mode_value': None, 'mode_rate': None, 'flip_rate': None, 'pairwise_agreement': None})
        return detail
    if output_type == 'categorical':
        return {'counts': dict(Counter(clean)), 'k': k}
    if output_type == 'string':
        # No declared K — report observed distinct count (denominator is ln(N)).
        return {'counts': dict(Counter(clean)), 'n_distinct': len(set(clean))}
    if output_type in _NUMERIC_TYPES:
        return {
            'mean': (fmean(clean) if clean else None),
            'stdev': (pstdev(clean) if clean else None),
            'scale': list(scale) if scale else None,
        }
    return {}


def _per_row(
    rows: list[dict[str, Any]], output_type: str, k: int | None, scale: tuple[float, float] | None, floor: int
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for row in rows:
        clean = _clean_verdicts(row.get('repetitions', []), output_type)
        n_clean = len(clean)
        # NB: the judged input (query/output/messages) is deliberately NOT copied
        # here — it lives canonically in stability.json. Duplicating it into
        # metrics.json would dump every rendered judge prompt into the conductor's
        # context when it reads the instability report, defeating the bounded
        # grey-zone payload. build_queue / recommend read the input from
        # stability.json by source_index instead.
        entry: dict[str, Any] = {
            'source_index': row.get('source_index'),
            'representative_explanation': row.get('representative_explanation'),
            'n_successful_repeats': n_clean,
            'n_failed': int(row.get('repetitions_failed') or 0),
            'n_wrong_output_type': int(row.get('n_wrong_output_type') or 0),
        }
        entry.update(_type_detail(output_type, clean, k, scale))
        # Unmeasurable: too few usable verdicts to trust, or no denominator to
        # normalize by (numeric with no scale, surfaced as instability None). Not
        # an error — the expected path in those cases (§4a).
        if n_clean == 0 or n_clean < floor:
            entry.update({'instability': None, 'band': 'unmeasurable'})
        else:
            try:
                inst = instability.row_instability(output_type, clean, k=k, scale=scale)
            except ValueError:
                inst = None
            if inst is None:
                entry.update({'instability': None, 'band': 'unmeasurable'})
            else:
                entry.update({'instability': inst, 'band': instability.classify(inst)})
        entries.append(entry)
    # Most-unstable first; unmeasurable rows (instability None) sort to the end.
    entries.sort(key=lambda e: (e['instability'] is None, -(e['instability'] or 0.0)))
    return entries


def _row_bools(row: dict[str, Any]) -> list[bool]:
    return [b for b in (_coerce_bool(v) for v in row.get('repetitions', [])) if b is not None]


def _panel_agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fleiss' κ and Gwet's AC1 across the N repeats (binary, variable raters).

    Boolean-only heavy stats — computed for completeness, kept out of the default
    report. Math unchanged from V1.
    """
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
        return {'fleiss_kappa': None, 'gwet_ac1': None, 'prevalence_true': None, 'one_flip_consistency': None}
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
        'one_flip_consistency': p_bar,
    }


def _detail_str(output_type: str, e: dict[str, Any]) -> str:
    def _num(v: Any) -> str:
        return f'{v:.2f}' if isinstance(v, (int, float)) else 'n/a'

    if output_type == 'boolean':
        return f"({e.get('n_true', '?')}T/{e.get('n_false', '?')}F)"
    if output_type == 'categorical':
        return f"(counts={e.get('counts')}, k={e.get('k')})"
    if output_type == 'string':
        return f"({e.get('n_distinct')} distinct / {e.get('n_successful_repeats', '?')} reps)"
    if output_type in _NUMERIC_TYPES:
        return f"(mean={_num(e.get('mean'))}, stdev={_num(e.get('stdev'))} on scale {e.get('scale')})"
    return ''


def _report(
    per_row: list[dict[str, Any]], output_type: str, mean_inst: float | None,
    bands: Counter, n_rows: int, n_measurable: int, total_wrong: int, total_failed: int
) -> str:
    def _fmt(v: Any) -> str:
        return f'{v:.3f}' if isinstance(v, (int, float)) else 'n/a'

    hist = f"{bands.get('stable', 0)} stable / {bands.get('noisy', 0)} noisy / {bands.get('unreliable', 0)} unreliable"
    if bands.get('unmeasurable'):
        hist += f" / {bands['unmeasurable']} unmeasurable"
    lines = [
        f'Instability summary over {n_rows} datapoints ({n_measurable} measurable, type={output_type}):',
        f'  - mean instability: {_fmt(mean_inst)} (0 = judge always agrees with itself, 1 = maximal).',
        f'  - bands: {hist}.',
    ]
    if total_wrong or total_failed:
        lines.append(f'  - {total_wrong} off-contract (wrong_output_type) reps, {total_failed} failed reps.')
    unstable = [e for e in per_row if e['instability'] not in (None, 0.0)]
    if unstable:
        lines.append('  - Most-unstable datapoints:')
        for e in unstable[:5]:
            lines.append(
                f"      #{e['source_index']}: instability={_fmt(e['instability'])} "
                f"({e['band']}) {_detail_str(output_type, e)}"
            )
    return '\n'.join(lines)


def main(run_dir: str | None = None, config: str = 'config.toml') -> str:
    """Compute per-row instability for a run directory's stability.json."""
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run stability.py first.')

    stability = runner.read_json(out_dir / 'stability.json')
    rows = stability.get('rows', [])
    meta = stability.get('metadata', {})
    n_req = int(meta.get('n_repeats') or cfg.get('n_repeats', 5))
    floor = max(1, math.ceil(n_req / 2))

    # Verdict space (output_type / K / scale) is authoritative in evaluator.json;
    # fall back to stability metadata's output_type for older runs.
    ev_path = out_dir / 'evaluator.json'
    ev = runner.read_json(ev_path) if ev_path.exists() else {}
    output_type = (ev.get('output_type') or meta.get('output_type') or 'boolean').strip().lower()
    labels = ev.get('categorical_labels') or []
    k = len(labels) if labels else None
    scale_raw = ev.get('scale')
    scale = tuple(scale_raw) if isinstance(scale_raw, (list, tuple)) and len(scale_raw) == 2 else None

    per_row = _per_row(rows, output_type, k, scale, floor)
    measurable = [e for e in per_row if e['instability'] is not None]
    bands = Counter(e['band'] for e in per_row)
    mean_inst = fmean(e['instability'] for e in measurable) if measurable else None
    n_unstable = sum(1 for e in measurable if e['instability'] > 0.0)
    total_wrong = sum(e['n_wrong_output_type'] for e in per_row)
    total_failed = sum(e['n_failed'] for e in per_row)

    # Heavy boolean-only agreement stats: computed, but not in the lean report.
    panel = _panel_agreement(rows) if output_type == 'boolean' else {
        'fleiss_kappa': None, 'gwet_ac1': None, 'prevalence_true': None, 'one_flip_consistency': None
    }
    report = _report(per_row, output_type, mean_inst, bands, len(rows), len(measurable), total_wrong, total_failed)

    metrics = {
        'metadata': {**meta, 'output_type': output_type, 'k': k, 'scale': list(scale) if scale else None},
        'scores': {
            'num_rows': len(rows),
            'measurable_rows': len(measurable),
            'mean_instability': mean_inst,
            'bands': dict(bands),
            'n_unstable': n_unstable,
            'n_flipped': n_unstable,  # boolean-compat alias (run_experiment reads flips)
            'total_wrong_output_type': total_wrong,
            'total_failed': total_failed,
            # Boolean-only heavy stats — surfaced on request, not in the report.
            'one_flip_consistency': panel['one_flip_consistency'],
            'fleiss_kappa': panel['fleiss_kappa'],
            'gwet_ac1': panel['gwet_ac1'],
            'prevalence_true': panel['prevalence_true'],
        },
        'report': report,
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
