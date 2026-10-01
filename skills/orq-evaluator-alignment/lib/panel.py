"""Pure jury-panel signals: several judge models, each repeated (RES-1638).

The judge being aligned is still one evaluator with one model; the panel is extra
evidence about it. Two signals per datapoint, kept apart because they mean different
things:

  - within-judge wobble: one model gives different verdicts across its repetitions
    (that model's 0..1 instability, `lib.instability`);
  - panel disagreement: two models' aggregate verdicts land on different sides
    (type-native, `lib.cross_model.disagrees`).

A model whose vote failed, or that has fewer usable repetitions than the floor, is
left out of both signals rather than counted as a disagreement. With fewer than two
measured models, panel disagreement is `None` (unmeasurable), not `False`.

No I/O and no evaluatorq/orq imports — safe to import directly on Windows.
"""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Callable, Sequence

from lib import agreement, cross_model, instability

_NUMERIC_TYPES = frozenset({'number', 'numeric'})


def _model_aggregate(output_type: str, clean: Sequence[Any]) -> Any:
    if output_type in _NUMERIC_TYPES:
        return sum(clean) / len(clean)
    (value, _), = Counter(clean).most_common(1)
    return value


def row_signals(
    panel: Sequence[dict[str, Any]],
    output_type: str,
    *,
    clean: Callable[[Sequence[Any]], list[Any]],
    floor: int,
    k: int | None = None,
    scale: tuple[float, float] | None = None,
    tol: float = 0.5,
) -> dict[str, Any]:
    """Wobble and disagreement for one datapoint's panel votes.

    `panel` is `stability.json`'s per-row list of `{model, repetitions, success}`.
    `clean` canonicalises a repetition list (drops failed / off-contract reps), the
    same function the single-judge instability uses, so both count the same reps.
    """
    per_model: dict[str, dict[str, Any]] = {}
    for vote in panel:
        reps = clean(vote.get('repetitions') or [])
        if not vote.get('success', True) or len(reps) < floor or not reps:
            per_model[vote['model']] = {'instability': None, 'value': None, 'measured': False}
            continue
        try:
            inst = instability.row_instability(output_type, reps, k=k, scale=scale)
        except ValueError:
            inst = None
        per_model[vote['model']] = {
            'instability': inst,
            # Use evaluatorq's actual aggregate when present: the retest scores
            # that same value, including its tie resolution. Older saved votes
            # without an aggregate can still be analysed from repetitions.
            'value': vote.get('value') if vote.get('value') is not None else _model_aggregate(output_type, reps),
            'measured': inst is not None,
        }
    measured = {m: e for m, e in per_model.items() if e['measured']}
    values = [e['value'] for e in measured.values()]
    if len(values) < 2:
        disagreement: bool | None = None
        panel_agreement: float | None = None
    else:
        disagreement = any(
            cross_model.disagrees(output_type, a, b, tol=tol)
            for i, a in enumerate(values) for b in values[i + 1:]
        )
        if output_type in _NUMERIC_TYPES:
            mid = median(values)
            panel_agreement = sum(1 for v in values if abs(v - mid) <= tol) / len(values)
        else:
            panel_agreement = Counter(values).most_common(1)[0][1] / len(values)
    unstable = sorted(m for m, e in measured.items() if (e['instability'] or 0.0) > 0.0)
    # A genuine split in an even-sized panel is an unresolved boundary question.
    # Failed/off-contract votes were removed above and cannot create a tie.
    tied = (
        output_type not in _NUMERIC_TYPES and len(values) >= 2
        and Counter(values).most_common(1)[0][1] <= len(values) / 2
    )
    insts = [e['instability'] for e in measured.values() if e['instability'] is not None]
    return {
        'per_model': per_model,
        'n_models_measured': len(measured),
        'disagreement': disagreement,
        'tied': tied,
        'panel_agreement': panel_agreement,
        'unstable_models': unstable,
        'max_instability': max(insts) if insts else None,
    }


def per_model_agreement(
    output_type: str,
    labels: dict[int, Any],
    model_values: dict[str, dict[int, Any]],
    *,
    tol: float = 0.5,
) -> dict[str, dict[str, Any]]:
    """Each panel model's agreement with the human labels, over the rows it measured.

    This is the diagnosis the aggregate hides: in the PyData 2026 run one model caught
    two of three human failures (kappa 0.63) while the other two passed everything, so
    the majority said "pass" on all three. A model with no labelled row it measured is
    omitted.
    """
    out: dict[str, dict[str, Any]] = {}
    for model, by_idx in model_values.items():
        pairs = [(labels[i], v) for i, v in by_idx.items() if i in labels and v is not None]
        if pairs:
            out[model] = agreement.agreement(output_type, pairs, tol=tol)
    return out


def resolve_panel(flag: Any, cfg: dict[str, Any]) -> list[str]:
    """Extra panel models: the `--panel_models` flag, else config `panel_models`.

    Accepts a comma-separated string (the shell form) or a list (TOML, or fire's
    parse of `a,b`). Blank entries and duplicates are dropped, order kept.
    """
    raw = cfg.get('panel_models') if flag is None else flag
    if raw is None:
        return []
    items = raw.split(',') if isinstance(raw, str) else list(raw)
    return list(dict.fromkeys(str(m).strip() for m in items if str(m).strip()))
