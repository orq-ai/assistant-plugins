"""Human-vs-judge agreement metrics per verdict type (RES-978 §2.5, signal (b)).

The retest needs two signals to call a rewrite a success. Signal (a) — instability
*dropped* — lives in `lib.instability` / `metrics.py`; alone it is gameable by a
judge that is consistently wrong but stable (the Part 1 blind spot). Signal (b) is
this module: does the new evaluator's verdict actually *agree with the human label*
on the same confusers?

  - boolean     → TPR / TNR (+ overall accuracy). Positive class = True.
  - categorical → exact-match accuracy (case/whitespace-normalized).
  - numeric     → MAE and within-tolerance rate on the raw scale (default tol 0.5).

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


def numeric_agreement(pairs: Sequence[Pair], tol: float = 0.5) -> dict[str, Any]:
    """Mean absolute error and within-tolerance rate over numeric pairs.

    Both computed on the raw scale (no normalization) so `tol` is expressed in the
    judge's own units. A pair counts as within tolerance when `|human − judge| <=
    tol` (boundary inclusive). Default `tol=0.5` — half a scale point.
    """
    _require_pairs(pairs, 'numeric')
    abs_errors = [abs(_coerce_float(human) - _coerce_float(judge)) for human, judge in pairs]
    n = len(abs_errors)
    n_within = sum(1 for e in abs_errors if e <= tol)
    return {
        'mae': sum(abs_errors) / n,
        'within_tolerance_rate': n_within / n,
        'n_within': n_within,
        'tol': float(tol),
        'n': n,
    }


def agreement(output_type: str, pairs: Sequence[Pair], **kw: Any) -> dict[str, Any]:
    """Dispatch `(human, judge)` pairs to the metric for `output_type`.

    boolean / categorical ignore extra kwargs; numeric reads `tol` (default 0.5).
    Fails loud on an unrecognised `output_type`, mirroring `lib.instability`.
    """
    t = (output_type or '').strip().lower()
    if t == 'boolean':
        return boolean_agreement(pairs)
    if t == 'categorical':
        return categorical_agreement(pairs)
    if t in _NUMERIC_TYPES:
        return numeric_agreement(pairs, tol=kw.get('tol', 0.5))
    raise ValueError(f'unknown output_type {output_type!r} (expected boolean | categorical | number)')
