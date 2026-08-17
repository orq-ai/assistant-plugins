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
from lib import content, instability, runner

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
    rows: list[dict[str, Any]], output_type: str, k: int | None, scale: tuple[float, float] | None,
    floor: int, n_req: int,
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
                inst = instability.row_instability(
                    output_type, clean, k=k, scale=scale, n_requested=n_req
                )
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


# ── correctness against dataset ground truth (flow-friction §3) ───────────────
# Instability answers "does the judge agree with itself". When rows carry a
# `reference` label it can also answer "is it RIGHT" — the question the skill
# otherwise says, correctly, that it structurally cannot answer. It was being
# collected and thrown away: `map_datapoint` routed `expected_output` into
# `reference`, and nothing downstream read it, because an evaluator declaring no
# {{reference}} variable never renders it.


def _numeric_tol(cfg: dict[str, Any], scale: tuple[float, float] | None) -> float:
    """The same band the retest gates on, so "correct" means one thing per run."""
    from lib import agreement as agreement_lib  # noqa: PLC0415 — pure stdlib module

    configured = cfg.get('numeric_tol')
    if configured not in (None, ''):
        return float(configured)
    return agreement_lib.default_tolerance(
        scale, fraction=float(cfg.get('numeric_tol_fraction', agreement_lib.DEFAULT_TOL_FRACTION))
    )


def _reference_matches(
    reference: Any, verdict: Any, output_type: str, tol: float,
    categorical_labels: list[str] | None = None,
) -> bool | None:
    """Whether a judge verdict matches a ground-truth label, type-natively.

    Returns None when the label cannot be read into the judge's verdict space —
    excluded from the count and warned about once, never coerced into a False that
    would read as the judge being wrong. For categorical this includes a reference
    that is not one of the judge's own declared labels: comparing it anyway would
    score the dataset's vocabulary mismatch as the judge being wrong.
    """
    if verdict is None or reference in (None, ''):
        return None
    if output_type == 'boolean':
        ref, got = _coerce_bool(reference), _coerce_bool(verdict)
        return None if ref is None or got is None else ref == got
    if output_type == 'categorical':
        norm_ref = str(reference).strip().lower()
        if categorical_labels and norm_ref not in {str(label).strip().lower() for label in categorical_labels}:
            return None
        return norm_ref == str(verdict).strip().lower()
    if output_type in _NUMERIC_TYPES:
        try:
            return abs(float(reference) - float(verdict)) <= tol
        except (TypeError, ValueError):
            return None
    return None  # string: `==` is the wrong comparison; the conductor reads those


def _correctness(
    rows: list[dict[str, Any]], per_row: list[dict[str, Any]], output_type: str, tol: float,
    *, variables: list[str] | None = None, categorical_labels: list[str] | None = None,
    tol_derivable: bool = True,
) -> dict[str, Any] | None:
    """Accuracy against `reference`, overall and **by instability band**.

    `by_band` is the point of the whole block. Accuracy among the rows the judge
    was *stable* on is the direct measurement of the consistently-wrong blind spot:
    a judge that is 100% stable and 60% correct on those rows is the exact failure
    instability-ranking cannot surface, and here it becomes a headline number
    instead of a caveat.

    Three cases omit accuracy outright rather than compute a number that looks
    like a measurement and isn't (§1.2, §3.5):
      - `variables` declares a reference-family variable (`reference`/`expected`/
        `expected_output`) — `reference` was bound INTO the judge prompt by
        `lib.judge.make_replacements`, so grading the verdict against it is
        circular, not a correctness check. Checked first: it invalidates the
        whole run regardless of `output_type`.
      - `output_type == 'string'` — `==` is the wrong comparison for free text.
      - numeric with no derivable tolerance band (`tol_derivable=False`) — a
        fixed absolute band would be arbitrary with no declared scale to size it.
    """
    if content.reference_is_judge_input(variables or []):
        ref_vars = [v for v in (variables or []) if content.field_for_variable(v) == 'reference']
        return {
            'n_labelled': 0,
            'reason_omitted': (
                f'the evaluator declares a reference-family variable ({", ".join(ref_vars)}), so '
                '`reference` is input the judge is shown, not ground truth to grade it against'
            ),
        }
    if output_type == 'string':
        return {
            'n_labelled': 0,
            'reason_omitted': (
                'string verdicts are not comparable with == (two correct answers differ in '
                'wording), so correctness is scored by reading, at the retest step'
            ),
        }
    if output_type in _NUMERIC_TYPES and not tol_derivable:
        return {
            'n_labelled': 0,
            'reason_omitted': (
                'no declared scale and no configured numeric_tol — a fixed absolute band would be '
                'arbitrary (0.5 is half of a 0-1 scale)'
            ),
        }
    verdicts = {r.get('source_index'): r.get('aggregate_value') for r in rows}
    bands = {e['source_index']: e['band'] for e in per_row}
    band_totals = Counter(e['band'] for e in per_row)
    n_unreadable = 0
    confusion: Counter = Counter()
    by_band: dict[str, dict[str, int]] = {}
    n_correct = n_labelled = 0
    wrong_indices: list[int] = []
    labelled_indices: list[int] = []
    for row in rows:
        idx = row.get('source_index')
        match = _reference_matches(row.get('reference'), verdicts.get(idx), output_type, tol, categorical_labels)
        if match is None:
            if row.get('reference') not in (None, '') and verdicts.get(idx) is not None:
                n_unreadable += 1
            continue
        n_labelled += 1
        n_correct += int(match)
        labelled_indices.append(idx)
        if not match:
            wrong_indices.append(idx)
        confusion[f'{row.get("reference")}→{verdicts.get(idx)}'] += 1
        slot = by_band.setdefault(bands.get(idx, 'unmeasurable'), {'n': 0, 'n_correct': 0})
        slot['n'] += 1
        slot['n_correct'] += int(match)
    if not n_labelled:
        return None
    for band_name, slot in by_band.items():
        slot['accuracy'] = slot['n_correct'] / slot['n']
        # Total rows in this band (labelled or not) — the denominator for how much
        # of the band the labelled subset actually speaks for (§4.1).
        slot['n_band_total'] = band_totals.get(band_name, slot['n'])
    return {
        'n_labelled': n_labelled,
        'n_correct': n_correct,
        'accuracy': n_correct / n_labelled,
        'confusion': dict(confusion),
        # Never laundered into a human verdict: a dataset label is someone's prior
        # judgement, possibly stale, possibly from a different rubric version.
        'label_source': 'dataset_reference',
        'by_band': by_band,
        'wrong_source_indices': sorted(wrong_indices),
        # Which rows carried a readable label at all, so downstream can tell
        # "judge was right" from "nobody said" without re-deriving the comparison.
        'labelled_source_indices': sorted(labelled_indices),
        'n_unreadable_labels': n_unreadable,
    }


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
        detail = f"({e.get('n_true', '?')}T/{e.get('n_false', '?')}F)"
    elif output_type == 'categorical':
        detail = f"(counts={e.get('counts')}, k={e.get('k')})"
    elif output_type == 'string':
        detail = f"({e.get('n_distinct')} distinct / {e.get('n_successful_repeats', '?')} reps)"
    elif output_type in _NUMERIC_TYPES:
        detail = f"(mean={_num(e.get('mean'))}, stdev={_num(e.get('stdev'))} on scale {e.get('scale')})"
    else:
        detail = ''
    # Instability is computed over the CLEAN verdicts, so a row can answer
    # off-contract half the time and still band as `stable` on the rest. The
    # dataset-level count shows it in aggregate but not on the row where it
    # happened, which is the row a reader is about to trust.
    n_wrong = e.get('n_wrong_output_type') or 0
    if n_wrong:
        detail += f' [{n_wrong} off-contract rep(s) excluded]'
    return detail


def _correctness_lines(c: dict[str, Any] | None) -> list[str]:
    """The correctness headline, with the stable-row accuracy called out.

    Overall accuracy is the reassuring number; accuracy among the rows the judge
    never wavered on is the one that can contradict the whole run. A judge at 0.0
    instability and 0.6 stable-row accuracy is consistently wrong, which is the
    failure everything else here is blind to — so it gets its own line, not a
    field in a nested dict.

    That line only earns the "measured" claim when the labelled subset actually
    speaks for the band: `n >= 10` (not a handful of rows) AND labelled coverage
    `>= 90%` of the band. Below that floor it is a partial view, not a measurement
    of the blind spot, and is captioned as such (§4.1).
    """
    if not c or not c.get('n_labelled'):
        return []
    lines = [
        f"  - correctness vs dataset labels: {c['n_correct']}/{c['n_labelled']} "
        f"({c['accuracy']:.0%}) — labels are `dataset_reference`, not the user's verdict."
    ]
    stable = c.get('by_band', {}).get('stable')
    if stable:
        n_band_total = stable.get('n_band_total', stable['n'])
        coverage = stable['n'] / n_band_total if n_band_total else 0.0
        line = (
            f"      on rows the judge was STABLE: {stable['n_correct']}/{stable['n']} "
            f"({stable['accuracy']:.0%}) — {stable['n']} of {n_band_total} stable rows carry a "
            f"label ({coverage:.0%} coverage)"
        )
        if stable['n'] >= 10 and coverage >= 0.9:
            line += ' ← the consistently-wrong blind spot, measured'
        else:
            line += ' (partial view — do not conclude "consistent-and-right" from this)'
        lines.append(line)
    if c.get('wrong_source_indices'):
        lines.append(f"      wrong on: {c['wrong_source_indices'][:10]}")
    return lines


def _report(
    per_row: list[dict[str, Any]], output_type: str, mean_inst: float | None,
    bands: Counter, n_rows: int, n_measurable: int, total_wrong: int, total_failed: int,
    correctness: dict[str, Any] | None = None,
) -> str:
    def _fmt(v: Any) -> str:
        return f'{v:.3f}' if isinstance(v, (int, float)) else 'n/a'

    hist = f"{bands.get('stable', 0)} stable / {bands.get('noisy', 0)} noisy / {bands.get('unreliable', 0)} unreliable"
    if bands.get('unmeasurable'):
        hist += f" / {bands['unmeasurable']} unmeasurable"
    # String instability is exact-match over canonical text (§4a has no reading
    # step at this stage) — two paraphrased, equally-correct answers count as a
    # disagreement, so the number can only overstate instability, never understate
    # it. Every other type's scale is the real thing being measured.
    if output_type == 'string':
        inst_annotation = (
            '(exact-match over canonical text: paraphrased same-meaning answers count as '
            'disagreement, so treat this as an upper bound).'
        )
    else:
        inst_annotation = '(0 = judge always agrees with itself, 1 = maximal).'
    lines = [
        f'Instability summary over {n_rows} datapoints ({n_measurable} measurable, type={output_type}):',
        f'  - mean instability: {_fmt(mean_inst)} {inst_annotation}',
        f'  - bands: {hist}.',
        *_correctness_lines(correctness),
    ]
    if total_wrong or total_failed:
        lines.append(f'  - {total_wrong} off-contract (wrong_output_type) reps, {total_failed} failed reps.')
    # Which rows, not just how many. A row whose band was computed from half its
    # repetitions can read `stable` while the other half never parsed, and the
    # dataset-level count above does not say which row that was.
    off_contract = [e for e in per_row if (e.get('n_wrong_output_type') or 0)]
    if off_contract:
        named = ', '.join(
            f"#{e['source_index']} ({e['n_wrong_output_type']} of "
            f"{e['n_wrong_output_type'] + e['n_successful_repeats'] + e['n_failed']}, band={e['band']})"
            for e in sorted(off_contract, key=lambda e: -e['n_wrong_output_type'])[:5]
        )
        lines.append(
            f'  - Rows with off-contract reps (band reflects only the parsed ones): {named}'
            + (f' … +{len(off_contract) - 5} more' if len(off_contract) > 5 else '')
        )
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

    per_row = _per_row(rows, output_type, k, scale, floor, n_req)
    # A fixed absolute band is only meaningful relative to a declared scale (or an
    # explicit override) — with neither, "correct within 0.5" is an arbitrary
    # number dressed up as a measurement (§3.5).
    tol_derivable = (cfg.get('numeric_tol') not in (None, '')) or scale is not None
    correctness = _correctness(
        rows, per_row, output_type, _numeric_tol(cfg, scale),
        variables=ev.get('variables', []), categorical_labels=labels, tol_derivable=tol_derivable,
    )
    if correctness and correctness.get('n_unreadable_labels'):
        logger.warning(
            f"⚠ {correctness['n_unreadable_labels']} row(s) carry a ground-truth label this "
            f"judge's verdict space cannot read; excluded rather than counted as wrong."
        )
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
    report = _report(
        per_row, output_type, mean_inst, bands, len(rows), len(measurable),
        total_wrong, total_failed, correctness,
    )

    metrics = {
        'metadata': {**meta, 'output_type': output_type, 'k': k, 'scale': list(scale) if scale else None},
        'scores': {
            'num_rows': len(rows),
            'measurable_rows': len(measurable),
            'mean_instability': mean_inst,
            'bands': dict(bands),
            'n_unstable': n_unstable,
            # Pre-multi-type alias for `n_unstable`, kept because metrics.json is
            # a published artifact an operator's own scripts may read. Nothing in
            # the skill consumes it now that the boolean-only retest is gone.
            'n_flipped': n_unstable,
            'total_wrong_output_type': total_wrong,
            'total_failed': total_failed,
            # Boolean-only heavy stats — surfaced on request, not in the report.
            'one_flip_consistency': panel['one_flip_consistency'],
            'fleiss_kappa': panel['fleiss_kappa'],
            'gwet_ac1': panel['gwet_ac1'],
            'prevalence_true': panel['prevalence_true'],
        },
        # Present only when rows carried a readable ground-truth label. Absent means
        # "not measured", never "nothing wrong found".
        'correctness': correctness,
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
