"""Cheap, dependency-free *workload* projection for the stability run (design §7).

V1 priced the run from a small in-file table keyed by the judge model slug. That
was fragile: ``fetch_traces`` pins ``judge_model`` to the raw slug read off the
production span (e.g. ``glm-5-2``, ``z-ai/glm-5-2``, ``anthropic.claude-...``),
which almost never matched the hand-maintained keys, so every unknown model
silently projected $0. Rather than chase provider-specific slug normalisation,
we drop pricing entirely and report the thing the operator actually needs to
size the run: the **number of judge calls** and the **input/output token
totals**. Convert to money yourself with whatever per-Mtoken rate your judge
model charges.

Deliberately does NOT import `orq_shared` — importing the heavier cost stack can
abort the process on this Windows host (project memory). Tokens are approximated
from string length (~4 chars/token), so these are order-of-magnitude figures,
presented as a ballpark — never false precision.
"""

from __future__ import annotations

from typing import Any

CHARS_PER_TOKEN = 4.0
# Judge-prompt overhead beyond the substituted variables (rubric text), chars.
JUDGE_OVERHEAD_CHARS = 1500
# Typical judge explanation length (completion), chars. Judges vary; this is a
# midpoint used only for the projection.
EXPLANATION_CHARS = 1000


def _tokens(chars: float) -> int:
    return max(1, int(round(chars / CHARS_PER_TOKEN)))


def project_stability_cost(
    *,
    judge_model: str,
    rows: list[dict[str, Any]],
    n_repeats: int,
    num_samples: int | None,
) -> dict[str, Any]:
    """Project the workload of the stability run from a sample of trace rows.

    Returns call count and token totals (input + output) — no pricing. Each of
    the ``num_datapoints`` rows is judged ``n_repeats`` times; per-call input is
    the judge-prompt overhead plus the rendered datapoint, per-call output is a
    typical explanation length.
    """
    sample = rows if num_samples in (None, -1) else rows[:num_samples]
    if not sample:
        # A "~0 judge calls / $0" projection reads as "this run is free" rather
        # than "there's nothing to judge" — refuse instead of misleading.
        raise ValueError('no trace rows to estimate; run fetch_traces.py first (traces.jsonl is empty)')
    n = len(sample)
    avg_in = sum(len(r.get('query', '') or '') + len(r.get('output', '') or '') for r in sample) / n
    in_per_call = _tokens(JUDGE_OVERHEAD_CHARS + avg_in)
    out_per_call = _tokens(EXPLANATION_CHARS)
    total_calls = n * n_repeats

    return {
        'judge_model': judge_model,
        'num_datapoints': n,
        'n_repeats': n_repeats,
        'total_calls': total_calls,
        'input_tokens_per_call': in_per_call,
        'output_tokens_per_call': out_per_call,
        'total_input_tokens': in_per_call * total_calls,
        'total_output_tokens': out_per_call * total_calls,
    }


def _fmt(n: int) -> str:
    """Group thousands so 1500000 reads as 1,500,000."""
    return f'{n:,}'


def format_projection(proj: dict[str, Any]) -> str:
    total_tokens = proj['total_input_tokens'] + proj['total_output_tokens']
    return (
        f'Stability run: ~{_fmt(proj["total_calls"])} judge calls '
        f'({proj["num_datapoints"]} datapoints × {proj["n_repeats"]} repeats), '
        f'judge={proj["judge_model"]}.\n'
        f'  Tokens (approx, ~{CHARS_PER_TOKEN:.0f} chars/token): '
        f'~{_fmt(proj["total_input_tokens"])} input + '
        f'~{_fmt(proj["total_output_tokens"])} output '
        f'= ~{_fmt(total_tokens)} total.\n'
        f'  Per call: ~{_fmt(proj["input_tokens_per_call"])} input + '
        f'~{_fmt(proj["output_tokens_per_call"])} output tokens. '
        f"Order-of-magnitude estimate; multiply by your judge model's "
        f'per-Mtoken rate for a cost.'
    )