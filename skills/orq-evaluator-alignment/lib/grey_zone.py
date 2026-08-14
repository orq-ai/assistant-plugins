"""Pure helpers for the RES-980 grey-zone assessment stage.

The clustering, question generation, and asking-the-human happen *in the
conductor* (SKILL.md), not here. This module holds only the deterministic,
unit-testable pieces around that conversation:

- `assemble_payload` — turn `queue.json` into a compact per-confuser payload the
  conductor reads into its context (verdict split + band + one representative
  rationale + a truncated view of the judged input). Bounded on purpose (§8).
- policy contract — `validate_policy`, `policy_to_guidance`, `policy_labels`:
  the `grey_zone_policy.json` the conductor writes, plus the two adapters that
  feed the *existing* rewrite/retest consumers unchanged.

No I/O and no evaluatorq/orq imports — safe to import directly on Windows.

Known v1 limitation (§3.1): "one reasoning example per distinct verdict" is not
available — evaluatorq's jury layer collapses the N rationales to a single
`representative_explanation` (`lib/judge.py`), so per-verdict rationales don't
exist in any artifact. The payload surfaces the one representative rationale.
"""

from __future__ import annotations

import json
from typing import Any

from lib.cost import CHARS_PER_TOKEN


def _verdict_split(output_type: str, votes: dict[str, Any]) -> dict[str, Any]:
    """The type-native tally the conductor needs to see how the judge disagreed
    with itself: boolean T/F counts, categorical label counts, numeric mean+stdev."""
    if output_type == 'boolean':
        return {'n_true': votes.get('n_true'), 'n_false': votes.get('n_false')}
    if output_type == 'categorical':
        return {'counts': votes.get('counts')}
    if output_type in {'number', 'numeric'}:
        return {'mean': votes.get('mean'), 'stdev': votes.get('stdev')}
    if output_type == 'string':
        return {'counts': votes.get('counts')}
    raise ValueError(f'unsupported output_type {output_type!r} for verdict split')


# Fraction of a value's budget spent on its head; the rest goes to its tail (§12.5).
# Head-only truncation loses the final assistant turn, which is usually the part the
# judge actually scored — so an over-budget value is shown from both ends.
_HEAD_FRACTION = 0.6


def _window(text: str, budget: int) -> tuple[str, int]:
    """Cut `text` to `budget` chars keeping a head AND a tail (§12.5).

    Returns ``(rendered, shown_chars)``. `shown_chars` counts *source* characters
    actually shown — not the elision marker — so the caller can report honestly how
    much of the original the conductor saw.
    """
    if budget <= 0:
        return '', 0
    if len(text) <= budget:
        return text, len(text)
    head_n = max(1, int(budget * _HEAD_FRACTION))
    tail_n = max(0, budget - head_n)
    elided = len(text) - head_n - tail_n
    tail = text[len(text) - tail_n:] if tail_n else ''
    return f'{text[:head_n]}…[{elided} chars elided]…{tail}', head_n + tail_n


def _fair_shares(lengths: list[int], budget: int) -> list[int]:
    """Split `budget` chars across values of the given `lengths` (§12.5).

    Each value starts at an equal share; values under their share release the
    remainder, split evenly among the values that are over. Without this a long
    `{{query}}` consumes the whole budget and starves `{{output}}` — the thing the
    judge actually scored — to nothing.
    """
    n = len(lengths)
    if n == 0:
        return []
    base = max(1, budget // n)
    over = [length for length in lengths if length > base]
    spare = sum(base - length for length in lengths if length <= base)
    bonus = (spare // len(over)) if over else 0
    return [base + bonus if length > base else base for length in lengths]


def _compact_input(item: dict[str, Any], max_chars: int) -> tuple[str, dict[str, Any]]:
    """The judged input, compactly, plus what it cost to get there (§12.5).

    Prefers the inverted `{{variable}}` bindings — what the judge actually scored.
    When inversion failed (`variables` is None) it falls back to the row's captured
    fields, and **that fallback must carry every field it has, not just `output`**.

    The old fallback was `output or query`, which silently dropped half the judged
    input on any row captured in the structured Responses-API shape: `output` there
    is the assistant's answer, not the rendered judge prompt, so a groundedness
    judge's context and question — the only things you can check groundedness
    *against* — never reached the conductor. Worse, the char accounting was computed
    over that single field, so the budget block truthfully reported "0 elided" while
    most of the input was missing. Elision accounting can only see what the budget
    dropped; it cannot see a field that was never collected. Hence `input_source`:
    a fallback render is flagged so the conductor caveats it instead of trusting it.

    Returns ``(rendered, stats)`` where stats carries the elision accounting the
    conductor is required to disclose (§12.8).
    """
    variables = item.get('variables')
    if variables:
        source = 'variables'
        names = [str(v.get('name')) for v in variables]
        values = [str(v.get('value') or '') for v in variables]
    else:
        source = 'fallback'
        names, values = [], []
        # Every captured field, labelled, most-context-first. `messages` is included
        # because an evaluator with a {{conversation}} variable is judged on it.
        for label, raw in (
            ('query', item.get('query')),
            ('output', item.get('output')),
            ('messages', item.get('messages')),
        ):
            if raw in (None, '', [], {}):
                continue
            names.append(label)
            values.append(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))
        if not values:
            names, values = ['(empty)'], ['']

    shares = _fair_shares([len(v) for v in values], max_chars)
    parts: list[str] = []
    shown_total = 0
    for i, value in enumerate(values):
        rendered, shown = _window(value, shares[i])
        shown_total += shown
        parts.append(f'{names[i]}: {rendered}')

    original = sum(len(v) for v in values)
    return '\n'.join(parts), {
        'input_chars_original': original,
        'input_chars_shown': shown_total,
        'input_truncated': shown_total < original,
        # 'variables' = the exact bindings the judge saw. 'fallback' = reassembled
        # from the row's captured fields because the template could not be inverted;
        # it may be missing something the judge had. MUST be disclosed (§12.8).
        'input_source': source,
        'input_fields': list(names),
    }


# Rationale stand-ins emitted by the jury layer when it has nothing real to report.
# Matched case-insensitively as substrings; these are notices *about* the vote, not
# reasoning *from* the judge, and presenting them as the latter misleads the reader.
_PLACEHOLDER_RATIONALES = (
    'tied without a decisive tie-break',
    'no explanation',
    'not available',
)


def _is_real_rationale(text: str) -> bool:
    """Whether `text` is actual judge reasoning rather than a jury-layer notice."""
    stripped = (text or '').strip()
    if not stripped:
        return False
    low = stripped.lower()
    return not any(marker in low for marker in _PLACEHOLDER_RATIONALES)


def estimate_tokens(obj: Any) -> int:
    """Rough token count of `obj` as the conductor will actually read it (§12.6).

    Serialized JSON bytes over the ~4 chars/token convention shared with
    `lib/cost.py` — JSON is the honest unit because the JSON file is what gets
    read. Order-of-magnitude, never presented as precise.
    """
    return max(1, round(len(json.dumps(obj, ensure_ascii=False)) / CHARS_PER_TOKEN))


# Stand-in for the `budget` block while sizing the envelope, so the block's own
# cost is not understated when deciding how many confusers fit.
_BUDGET_PLACEHOLDER = {
    'estimated_tokens': 000000, 'budget_tokens': 000000, 'within_budget': True,
    'budget_top_k': 000000, 'n_dropped_by_budget': 000000, 'n_truncated': 000000,
    'total_chars_elided': 000000, 'n_low_flip_excluded': 000000, 'n_confusers': 000000,
    'n_fallback_input': 000000, 'n_no_rationale': 000000,
}


def _fitting_prefix(confusers: list[dict[str, Any]], envelope: dict[str, Any], max_tokens: int) -> int:
    """How many leading confusers fit under `max_tokens` (§12.6).

    A prefix, never a subset: `queue.json` is most-unstable-first, so dropping from
    the middle would discard the highest-signal points. Returns at least 1 when any
    confuser exists — reporting "over budget" beats handing the conductor an empty
    item list it would read as "no confusers found".
    """
    if not confusers:
        return 0
    total = estimate_tokens(envelope)
    for i, confuser in enumerate(confusers):
        total += estimate_tokens(confuser)
        if total > max_tokens:
            return max(1, i)
    return len(confusers)


def assemble_payload(
    queue: dict[str, Any],
    *,
    top_k: int | None = None,
    max_chars: int = 600,
    max_tokens: int | None = None,
    include_low_flip: bool = False,
) -> dict[str, Any]:
    """`queue.json` dict → compact confuser payload for the conductor's context.

    Bounded on purpose (§8, §12): `top_k` caps how many confusers enter context
    (queue items are already most-unstable-first), `max_chars` fair-shares a
    windowing budget across each confuser's judged input, and `max_tokens` clamps
    the assembled payload to the fitting prefix. The verdict split + band + one
    representative rationale is what the conductor open-codes into grey zones.

    `include_low_flip` is False because the queue is not all confusers: `build_queue`
    appends `low_flip_sample_size` (default 5) *stable* rows as a spot-check against
    the consistently-wrong blind spot. Those rows are a control group for annotation,
    not material to open-code — a judge that never wavered on them has no grey zone to
    find. Including them made "review 3 examples with me" put 3 + 5 = 8 into context,
    silently overriding the count the user chose at step 5.

    The returned `budget` block is what the conductor is required to disclose
    (§12.8): what entered context, what the budget dropped, and how much of each
    input was actually seen.
    """
    verdict_space = queue.get('meta', {}).get('verdict_space', {})
    output_type = (verdict_space.get('type') or 'boolean').strip().lower()

    items = queue.get('items', [])
    n_low_flip_excluded = 0
    if not include_low_flip:
        kept_items = [i for i in items if not i.get('low_flip_sample', False)]
        n_low_flip_excluded = len(items) - len(kept_items)
        items = kept_items
    if top_k is not None and top_k >= 0:
        items = items[:top_k]

    confusers: list[dict[str, Any]] = []
    for item in items:
        ambiguity = item.get('ambiguity', {})
        votes = item.get('judge_votes', {})
        compact_input, input_stats = _compact_input(item, max_chars)
        reasoning_raw = votes.get('representative_explanation') or ''
        reasoning, reasoning_shown = _window(reasoning_raw, max_chars)
        confuser = {
            'source_index': item.get('source_index'),
            'band': ambiguity.get('band'),
            'instability': ambiguity.get('instability'),
            'low_flip_sample': item.get('low_flip_sample', False),
            'input': compact_input,
            'verdict_split': _verdict_split(output_type, votes),
            'representative_reasoning': reasoning,
            'reasoning_chars_original': len(reasoning_raw),
            'reasoning_chars_shown': reasoning_shown,
            'reasoning_truncated': reasoning_shown < len(reasoning_raw),
            # False when the jury returned no real rationale — most often on an exact
            # tie, where it emits a tie-break notice instead. That is the perverse
            # case: the most evenly-split confuser, the one where seeing both sides
            # would help most, is the one that arrives with nothing to read. Flagged
            # so the conductor says so rather than quoting the placeholder as if it
            # were the judge's reasoning.
            'reasoning_available': _is_real_rationale(reasoning_raw),
        }
        confuser.update(input_stats)
        confusers.append(confuser)

    n_total = len(confusers)
    envelope = {
        'verdict_space': verdict_space, 'n_confusers': n_total,
        'confusers': [], 'budget': _BUDGET_PLACEHOLDER,
    }
    if max_tokens is not None and max_tokens > 0:
        keep = _fitting_prefix(confusers, envelope, max_tokens)
    else:
        keep = n_total
    kept = confusers[:keep]

    payload = {
        'verdict_space': verdict_space,
        'n_confusers': len(kept),
        'confusers': kept,
    }
    estimated = estimate_tokens({**payload, 'budget': _BUDGET_PLACEHOLDER})
    payload['budget'] = {
        'estimated_tokens': estimated,
        'budget_tokens': max_tokens,
        # Mirrored into the budget block so build_queue's step-5 projection can
        # report the count without reassembling — the two must never disagree.
        'n_confusers': len(kept),
        'within_budget': max_tokens is None or estimated <= max_tokens,
        # The top_k that would have produced this payload — what a human would pass
        # to reproduce it, or raise past deliberately.
        'budget_top_k': keep if keep < n_total else None,
        'n_dropped_by_budget': n_total - len(kept),
        # Stable spot-check rows held out of the open-coding payload. Reported so the
        # count the conductor states matches what the user asked for at step 5.
        'n_low_flip_excluded': n_low_flip_excluded,
        # Rows whose input was reassembled from captured fields because the judge
        # template could not be inverted. These may be missing something the judge
        # saw, and no elision count can reveal that — so it is counted separately.
        'n_fallback_input': sum(1 for c in kept if c.get('input_source') == 'fallback'),
        # Confusers arriving with no usable judge rationale (usually exact ties).
        'n_no_rationale': sum(1 for c in kept if not c.get('reasoning_available')),
        'n_truncated': sum(
            1 for c in kept if c['input_truncated'] or c['reasoning_truncated']
        ),
        'total_chars_elided': sum(
            (c['input_chars_original'] - c['input_chars_shown'])
            + (c['reasoning_chars_original'] - c['reasoning_chars_shown'])
            for c in kept
        ),
    }
    return payload


# ── grey_zone_policy.json contract ────────────────────────────────────────────
# The artifact the conductor writes after the chat Q&A. It records, per grey zone,
# the question asked, the human's answer, the resolved rule, and its member points;
# plus the per-point policy label the conductor derived by applying the rules.
# Shape:
#   {output_type, verdict_space,
#    grey_zones: [{id, question, answer, rule, member_source_indices}],
#    labels:     [{source_index, value, tolerance?, grey_zone_id}]}
# It replaces annotations.json while honouring the same downstream contract:
# `policy_labels` feeds retest, `policy_to_guidance` feeds rewrite_eval (§7).

_NUMERIC_TYPES = frozenset({'number', 'numeric'})


def validate_policy(policy: dict[str, Any]) -> None:
    """Raise ``ValueError`` naming the first thing wrong with a grey_zone_policy.

    Fail loud so a malformed policy never silently drops a point's label or lets a
    numeric label through without its tolerance band (§3.5). Returns None on success.
    """
    output_type = (policy.get('output_type') or '').strip().lower()
    if not output_type:
        raise ValueError('grey_zone_policy is missing output_type')

    labels = policy.get('labels')
    if not isinstance(labels, list):
        raise ValueError('grey_zone_policy.labels must be a list of per-point labels')

    for label in labels:
        if 'source_index' not in label:
            raise ValueError(f'grey_zone_policy label is missing source_index: {label!r}')
        if 'value' not in label or label['value'] is None:
            raise ValueError(f'grey_zone_policy label is missing value: {label!r}')
        if output_type in _NUMERIC_TYPES and 'tolerance' not in label:
            raise ValueError(
                f'numeric grey_zone_policy label needs a tolerance band (target_score ± tolerance): {label!r}'
            )


def policy_labels(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-point policy labels keyed by ``str(source_index)`` — the annotations.json
    shape retest already reads (`value`, plus a per-point numeric `tolerance`)."""
    out: dict[str, dict[str, Any]] = {}
    for label in policy.get('labels', []):
        entry: dict[str, Any] = {'value': label['value']}
        if 'tolerance' in label:
            entry['tolerance'] = label['tolerance']
        out[str(label['source_index'])] = entry
    return out


def policy_to_guidance(policy: dict[str, Any]) -> str:
    """Render the policy as free-text rubric guidance (the aggregated.md that
    rewrite_eval consumes unchanged). Each grey zone becomes a change-to-make with
    its resolved rule, the human's answer as grounding, and its member points."""
    output_type = (policy.get('output_type') or '').strip().lower()
    lines = [f'# Grey-zone policy ({output_type} evaluator)', '']
    grey_zones = policy.get('grey_zones', [])
    if grey_zones:
        lines.append('## Changes to make (from the grey-zone answers)')
        lines.append('')
        for gz in grey_zones:
            members = ', '.join(f'#{i}' for i in gz.get('member_source_indices', []))
            lines.append(f'### {gz.get("question", "").strip()}')
            if members:
                lines.append(f'_({len(gz.get("member_source_indices", []))} datapoint(s): {members})_')
            lines.append(f'- **Resolved rule:** {gz.get("rule", "").strip()}')
            lines.append(f'- Grounding (human answer): {gz.get("answer", "").strip()}')
            lines.append('')
    return '\n'.join(lines).strip() + '\n'
