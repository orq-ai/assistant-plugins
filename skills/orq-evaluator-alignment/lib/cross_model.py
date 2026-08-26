"""Pure per-datapoint cross-model disagreement (RES-980 §11.3, option 4).

When the primary judge is unanimous everywhere (flat instability, empty confuser
queue), re-judging the same datapoints with a *different* model and comparing the
two majority verdicts surfaces the ambiguous points a single self-consistent judge
hides. Disagreement is type-native; a datapoint where the two models disagree is a
confuser even though each model agrees with itself.

No I/O and no evaluatorq/orq imports — safe to import directly on Windows.
"""

from __future__ import annotations

from typing import Any

_NUMERIC_TYPES = frozenset({'number', 'numeric'})


def disagrees(output_type: str, a: Any, b: Any, *, tol: float = 0.5) -> bool:
    """Do model-A and model-B verdicts disagree on one datapoint, per type?

    - boolean → unequal values.
    - categorical → unequal after casefold+strip (mirrors `lib.agreement`).
    - numeric → `|a − b| > tol` (reuses the retest within-tolerance band).
    """
    t = (output_type or 'boolean').strip().lower()
    if t in _NUMERIC_TYPES:
        return abs(float(a) - float(b)) > tol
    if t == 'boolean':
        return bool(a) != bool(b)
    if t == 'categorical':
        return str(a).casefold().strip() != str(b).casefold().strip()
    raise ValueError(f'unsupported output_type {output_type!r} for cross-model comparison')


def cross_model_rows(
    output_type: str, a_by_idx: dict[Any, Any], b_by_idx: dict[Any, Any], *, tol: float = 0.5
) -> list[dict[str, Any]]:
    """Per-datapoint comparison over the source_indices both models judged.

    Skips any index where either model produced no usable verdict (None) — a
    missing verdict can't inform disagreement. Returns
    `{source_index, model_a, model_b, disagree}`, index-sorted.
    """
    rows: list[dict[str, Any]] = []
    for idx in sorted(set(a_by_idx) & set(b_by_idx), key=lambda x: (x is None, x)):
        a, b = a_by_idx[idx], b_by_idx[idx]
        if a is None or b is None:
            continue
        rows.append(
            {'source_index': idx, 'model_a': a, 'model_b': b,
             'disagree': disagrees(output_type, a, b, tol=tol)}
        )
    return rows


def disagreeing_indices(
    output_type: str, a_by_idx: dict[Any, Any], b_by_idx: dict[Any, Any], *, tol: float = 0.5
) -> list[Any]:
    """The source_indices where the two models disagree — the new confuser set."""
    return [r['source_index'] for r in cross_model_rows(output_type, a_by_idx, b_by_idx, tol=tol) if r['disagree']]
