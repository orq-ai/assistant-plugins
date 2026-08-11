"""Per-row judge instability on one 0..1 scale, for all three orq verdict types (RES-978).

The linchpin of the multi-type alignment work: boolean, categorical and numeric
evaluators each land on the *same* `instability ∈ [0, 1]` scale with shared bands
(`stable < 0.1`, `noisy 0.1–0.3`, `unreliable > 0.3`), so everything downstream —
confuser ranking, sort order, the retest N-recommendation, the low-instability
sanity sample — keys off one number and never branches on type.

Pure by design: no I/O, no judge calls, stdlib only. Canonicalising a raw judge
completion into a clean verdict lives in `lib.judge.parse_verdict`; this module
only ever sees already-canonical verdicts, which keeps it unit-testable and lets
the validation project (`projects/eval-stability-metrics-validation`) import it.

Formulas (RES-978 §4), each returning 0.0 = perfectly stable, 1.0 = maximal:
  - boolean:      2·min(yes, N−yes) / N
  - categorical:  H / ln(k)   with H = −Σ p·ln p  over observed labels
  - numeric:      population stdev / (scale_max − scale_min)
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import pstdev
from typing import Sequence

# Band thresholds on the shared 0..1 scale (RES-978 §2). `stable` is below the
# lower cut; `noisy` spans [lower, upper] inclusive; `unreliable` is above upper.
_STABLE_MAX = 0.1
_NOISY_MAX = 0.3

# output_type strings we accept for the numeric branch. orq's create CLI uses
# `number` (§3); `numeric` is tolerated because the exact spelling is an open
# question at implementation time (§8.1) and both read naturally.
_NUMERIC_TYPES = frozenset({'number', 'numeric'})


def boolean(verdicts: Sequence[bool]) -> float:
    """Flip rate `2·minority/N` — 0.0 unanimous, 1.0 an even split.

    RES-978 adopts `2·minority/N` (not the legacy `1 − mode_rate`) so a boolean
    lands on the same scale as the other two types: a 7/3 split is 0.6, not 0.3.
    """
    n = len(verdicts)
    if n == 0:
        raise ValueError('boolean instability needs at least one verdict')
    yes = sum(1 for v in verdicts if v)
    minority = min(yes, n - yes)
    return 2.0 * minority / n


def categorical(labels: Sequence[str], k: int) -> float:
    """Normalized Shannon entropy `H / ln(k)` over the observed label counts.

    `k` is the count of *declared* allowed labels (the full label set), never the
    number observed — the denominator is fixed by the evaluator's contract so the
    scale does not drift with the sample. `k <= 1` has no possible disagreement
    and is defined as 0.0 (also avoids the `ln 1 = 0` division).
    """
    n = len(labels)
    if n == 0:
        raise ValueError('categorical instability needs at least one label')
    if k <= 1:
        return 0.0
    entropy = 0.0
    for count in Counter(labels).values():
        p = count / n
        entropy -= p * math.log(p)
    return entropy / math.log(k)


def numeric(values: Sequence[float], scale_min: float, scale_max: float) -> float:
    """Population stdev normalized by the *declared* scale range.

    Uses the declared `[scale_min, scale_max]`, not the observed spread, so the
    number means the same thing regardless of what the judge happened to emit. A
    single value (stdev 0) is 0.0; the scale range must be strictly positive.
    """
    if len(values) == 0:
        raise ValueError('numeric instability needs at least one value')
    scale_range = scale_max - scale_min
    if scale_range <= 0:
        raise ValueError(f'numeric scale range must be > 0, got [{scale_min}, {scale_max}]')
    return pstdev(values) / scale_range


def classify(x: float) -> str:
    """Band a 0..1 instability score: `stable` | `noisy` | `unreliable` (§2)."""
    if x < _STABLE_MAX:
        return 'stable'
    if x <= _NOISY_MAX:
        return 'noisy'
    return 'unreliable'


def row_instability(
    output_type: str,
    verdicts: Sequence,
    *,
    k: int | None = None,
    scale: tuple[float, float] | None = None,
) -> float | None:
    """Dispatch one row's canonical verdicts to its type's formula.

    Returns the 0..1 instability, or `None` when the row is *unmeasurable* — the
    numeric case with no declared scale (§4a): the values are real but there is no
    denominator to normalize by, which is the expected path until a scale is known,
    not an error. Fails loud (`ValueError`) on a categorical row missing `k`
    (always derivable from the declared labels) or an unrecognised `output_type`.
    """
    t = (output_type or '').strip().lower()
    if t == 'boolean':
        return boolean(verdicts)
    if t == 'categorical':
        if k is None:
            raise ValueError('categorical row_instability requires k (the declared label count)')
        return categorical(verdicts, k)
    if t in _NUMERIC_TYPES:
        if scale is None:
            return None  # unmeasurable: no scale to normalize by (§4a)
        return numeric(verdicts, scale[0], scale[1])
    raise ValueError(f'unknown output_type {output_type!r} (expected boolean | categorical | number)')
