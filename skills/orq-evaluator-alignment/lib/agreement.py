"""Human-vs-judge agreement metrics per verdict type (RES-978 §2.5, signal (b)).

The retest needs two signals to call a rewrite a success. Signal (a) — instability
*dropped* — lives in `lib.instability` / `metrics.py`; alone it is gameable by a
judge that is consistently wrong but stable (the Part 1 blind spot). Signal (b) is
this module: does the new evaluator's verdict actually *agree with the human label*
on the same confusers?

  - boolean     → TPR / TNR (+ overall accuracy). Positive class = True.
  - categorical → exact-match accuracy (case/whitespace-normalized).
  - numeric     → MAE and within-tolerance rate on the raw scale, with the default
    band derived from the evaluator's DECLARED scale (`default_tolerance`).

Free-form `string` evaluators are out of scope: `==` is the wrong comparison for
prose and the alternative is a human reading every pair, which is the manual
labelling this skill exists to remove. See `orq_client.SUPPORTED_OUTPUT_TYPES`.

Pure by design, mirroring `lib.instability`: no I/O, no judge calls, stdlib only,
no evaluatorq/orq import — so it stays unit-testable and imports safely on the
Windows host where the heavy deps abort at import time. Every function takes
`pairs`, a list of `(human_value, judge_value)` tuples, and fails loud on an empty
set or an unknown `output_type`.
"""

from __future__ import annotations

from typing import Any, Sequence

Pair = tuple[Any, Any]

# output_type strings routed to the numeric branch, matching lib.instability's
# tolerance of both spellings (the exact spelling is an open question, §8.1).
_NUMERIC_TYPES = frozenset({'number', 'numeric'})

_TRUE_TOKENS = frozenset({'true', 'yes', 'pass', '1'})
_FALSE_TOKENS = frozenset({'false', 'no', 'fail', '0'})

# Default numeric band as a fraction of the DECLARED scale range, and the absolute
# band used when no scale is declared. 10% of the range is one point on a 1–10
# judge, which is the shape most rubrics are written to.
DEFAULT_TOL_FRACTION = 0.1
FALLBACK_TOL = 0.5


def default_tolerance(
    scale: Sequence[float] | None,
    *,
    fraction: float = DEFAULT_TOL_FRACTION,
    fallback: float = FALLBACK_TOL,
) -> float:
    """The numeric agreement band for an evaluator, derived from its scale.

    A fixed absolute band is the wrong default, and in the same way `lib.instability`
    already argues against: `tol=0.5` is 50% of a 0–1 groundedness scale and 0.5% of
    a 0–100 one, so signal (b) waved through a rewrite that missed the human by half
    the scale on one judge and rejected one that was within a point everywhere on the
    other. Scaling the band by `(max − min)` puts both judges on the same footing,
    exactly as normalizing the spread does for signal (a).

    Falls back to an absolute `fallback` when no usable scale is declared — the band
    has to be *something*, and an undeclared scale is the case where there is nothing
    to derive it from.
    """
    if isinstance(scale, (list, tuple)) and len(scale) == 2:
        try:
            span = float(scale[1]) - float(scale[0])
        except (TypeError, ValueError):
            return float(fallback)
        if span > 0:
            return fraction * span
    return float(fallback)


def _coerce_bool(value: Any) -> bool:
    """Best-effort bool from a human label or judge verdict.

    Accepts real bools, 0/1, and the usual string tokens; raises on anything
    genuinely ambiguous so a mislabelled pair fails loud rather than silently
    counting as False.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE_TOKENS:
            return True
        if v in _FALSE_TOKENS:
            return False
    raise ValueError(f'cannot interpret {value!r} as a boolean verdict')


def _norm_label(value: Any) -> str:
    """Canonicalise a categorical label: string, trimmed, lowercased."""
    return str(value).strip().lower()


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'cannot interpret {value!r} as a numeric verdict') from exc


def _require_pairs(pairs: Sequence[Pair], output_type: str) -> None:
    if not pairs:
        raise ValueError(f'{output_type} agreement needs at least one (human, judge) pair')


def boolean_agreement(pairs: Sequence[Pair]) -> dict[str, Any]:
    """TPR, TNR and overall accuracy over `(human, judge)` boolean pairs.

    Positive class is True (human label is ground truth). TPR = TP/(TP+FN); TNR =
    TN/(TN+FP). Each rate is `None` when its denominator is 0 (no human positives
    → TPR undefined; no human negatives → TNR undefined) rather than a misleading
    0 or a crash. Accuracy is always defined over a non-empty set.
    """
    _require_pairs(pairs, 'boolean')
    tp = fn = tn = fp = 0
    for human, judge in pairs:
        h = _coerce_bool(human)
        j = _coerce_bool(judge)
        if h and j:
            tp += 1
        elif h and not j:
            fn += 1
        elif (not h) and (not j):
            tn += 1
        else:
            fp += 1
    n = tp + fn + tn + fp
    n_pos = tp + fn
    n_neg = tn + fp
    return {
        'tpr': (tp / n_pos) if n_pos else None,
        'tnr': (tn / n_neg) if n_neg else None,
        'accuracy': (tp + tn) / n,
        'tp': tp,
        'fn': fn,
        'tn': tn,
        'fp': fp,
        'n': n,
    }


def categorical_agreement(pairs: Sequence[Pair]) -> dict[str, Any]:
    """Exact-match accuracy over `(human, judge)` categorical pairs.

    Labels are compared case-insensitively after trimming so `'Safe'` and
    `'safe '` agree. No confusion detail here — that richer breakdown lives in the
    recommend/aggregate step; this is the honest scalar the retest gates on.
    """
    _require_pairs(pairs, 'categorical')
    n_correct = sum(1 for human, judge in pairs if _norm_label(human) == _norm_label(judge))
    n = len(pairs)
    return {'accuracy': n_correct / n, 'n_correct': n_correct, 'n': n}


def numeric_agreement(
    pairs: Sequence[Pair], tol: float = FALLBACK_TOL, tols: Sequence[float | None] | None = None
) -> dict[str, Any]:
    """Mean absolute error and within-tolerance rate over numeric pairs.

    Both computed on the raw scale (no normalization) so `tol` is expressed in the
    judge's own units. A pair counts as within tolerance when `|human − judge| <=
    tol` (boundary inclusive).

    `tols` gives a **per-pair** band, positionally aligned with `pairs`, for the
    points the human banded individually; `None` at a position falls back to the
    run-wide `tol`. The grey-zone policy requires a `tolerance` on every numeric
    label, and honouring only a uniform one meant three different bands from the
    human were silently replaced by the configured default. `tol_source` says which
    regime was in play so the retest report can state it.
    """
    _require_pairs(pairs, 'numeric')
    if tols is not None and len(tols) != len(pairs):
        raise ValueError(f'tols has {len(tols)} entries for {len(pairs)} pairs')
    abs_errors = [abs(_coerce_float(human) - _coerce_float(judge)) for human, judge in pairs]
    bands = [
        float(tol if tols is None or tols[i] is None else tols[i])
        for i in range(len(abs_errors))
    ]
    n = len(abs_errors)
    n_within = sum(1 for e, band in zip(abs_errors, bands) if e <= band)
    per_point = tols is not None and len(set(bands)) > 1
    return {
        'mae': sum(abs_errors) / n,
        'within_tolerance_rate': n_within / n,
        'n_within': n_within,
        'tol': float(tol),
        'tol_source': 'per_point' if per_point else 'uniform',
        'n': n,
    }


def agreement(output_type: str, pairs: Sequence[Pair], **kw: Any) -> dict[str, Any]:
    """Dispatch `(human, judge)` pairs to the metric for `output_type`.

    boolean / categorical ignore extra kwargs; numeric reads `tol` (the run-wide
    band) and `tols` (optional per-pair bands). Fails loud on an unrecognised
    `output_type`, mirroring `lib.instability`.
    """
    t = (output_type or '').strip().lower()
    if t == 'boolean':
        return boolean_agreement(pairs)
    if t == 'categorical':
        return categorical_agreement(pairs)
    if t in _NUMERIC_TYPES:
        return numeric_agreement(pairs, tol=kw.get('tol', FALLBACK_TOL), tols=kw.get('tols'))
    raise ValueError(
        f'unknown output_type {output_type!r} (expected boolean | categorical | number)'
    )
