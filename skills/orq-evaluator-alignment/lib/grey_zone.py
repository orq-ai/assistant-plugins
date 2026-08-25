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
import re
from typing import Any

from lib.content import stringify_messages
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
    raise ValueError(f'unsupported output_type {output_type!r} for verdict split')


# Fraction of a value's budget spent on its head; the rest goes to its tail (§12.5).
# Head-only truncation loses the final assistant turn, which is usually the part the
# judge actually scored — so an over-budget value is shown from both ends.
_HEAD_FRACTION = 0.6


def _window(text: str, budget: int) -> tuple[str, int]:
    """Cut `text` so the RENDERED result fits `budget` chars, head AND tail (§12.5).

    Returns ``(rendered, shown_chars)``. `shown_chars` counts *source* characters
    actually shown — not the elision marker — so the caller can report honestly how
    much of the original the conductor saw.

    The marker counts against the budget: it used to be added on top, so a `600`
    char budget spread over three variables rendered ~1000 characters and the
    ceiling the caller thought it had set was not the one it got.
    """
    if budget <= 0:
        return '', 0
    if len(text) <= budget:
        return text, len(text)
    # Upper bound on the marker (the real elided count is <= len(text), so its
    # rendering is never longer than this). Reserving the bound rather than
    # solving the fixed point keeps this exact in the direction that matters:
    # rendered length never exceeds `budget`.
    marker_max = len(f'…[{len(text)} chars elided]…')
    content = max(1, budget - marker_max)
    head_n = max(1, int(content * _HEAD_FRACTION))
    tail_n = max(0, content - head_n)
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
    if budget < n:
        # `max(1, ...)` here used to hand out n chars against a budget of fewer
        # than n. A budget too small to give every value a character is a budget
        # of zero, not a licence to overspend.
        return [0] * n
    base = budget // n
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
            if isinstance(raw, str):
                values.append(raw)
            elif label == 'messages':
                # Render the conversation the way the judge is about to see it
                # (lib.content.stringify_messages, parts-aware). A raw json.dumps
                # spends the char budget on structural noise and, on a
                # Responses-API row, buries the text under parts[] — the
                # conductor would be reading a different artifact than the judge.
                values.append(stringify_messages(raw))
            else:
                values.append(json.dumps(raw, ensure_ascii=False))
        if not values:
            names, values = ['(empty)'], ['']

    # The `name: ` prefixes and the newlines between them are part of what the
    # conductor reads, so they come out of the budget before it is shared. Without
    # this the rendered block overshoots `max_chars` by the label overhead.
    overhead = sum(len(n) + 2 for n in names) + max(0, len(names) - 1)
    shares = _fair_shares([len(v) for v in values], max(0, max_chars - overhead))
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
# These are notices *about* the vote, not reasoning *from* the judge, and presenting
# them as the latter misleads the reader.
#
# Split by how distinctive the wording is, because a blanket substring test threw
# real reasoning away: `_is_real_rationale('the tool was not available at inference
# time, so the claim is ungrounded')` returned False, and the conductor was then told
# there was nothing to read when there was.
#
# `_PLACEHOLDER_PHRASES` are jury jargon that no judge writes by accident, so they
# match wherever they appear. `_PLACEHOLDER_EXACT` are ordinary English, so they only
# count when they are the WHOLE rationale.
_PLACEHOLDER_PHRASES = (
    re.compile(r'tied without a decisive tie-break', re.IGNORECASE),
)
_PLACEHOLDER_EXACT = (
    re.compile(r'^\(?\s*no explanation(\s+(?:was\s+)?(?:provided|available|given))?\s*[.!]?\)?$', re.IGNORECASE),
    re.compile(r'^\(?\s*(?:explanation\s+)?not available\s*[.!]?\)?$', re.IGNORECASE),
    re.compile(r'^\(?\s*(?:none|n/a)\s*[.!]?\)?$', re.IGNORECASE),
)


def _is_real_rationale(text: str) -> bool:
    """Whether `text` is actual judge reasoning rather than a jury-layer notice."""
    stripped = (text or '').strip()
    if not stripped:
        return False
    if any(marker.search(stripped) for marker in _PLACEHOLDER_PHRASES):
        return False
    return not any(marker.match(stripped) for marker in _PLACEHOLDER_EXACT)


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
    'n_fallback_input': 000000, 'n_no_rationale': 000000, 'n_dropped_cross_model': 000000,
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
            # Why this example is here: 'instability' (the judge disagreed with
            # itself) or 'cross_model' (a second model disagreed with it while it
            # stayed steady). They are different kinds of hard and the conductor
            # should not describe one as the other. Cross-model rows also sit after
            # the instability-ranked ones, so they are the first the token clamp
            # drops — hence `n_dropped_cross_model` in the budget block.
            'reason': item.get('reason', 'instability'),
            # Ground truth, when the dataset carried it. This is what lets the
            # conductor group by HOW the judge is wrong — "it over-flags fiction" —
            # rather than only by what it was unsure about. `judge_correct` is None
            # when no label was readable: absent, not False.
            'reference': item.get('reference', ''),
            'judge_correct': item.get('judge_correct'),
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
        # Of those, how many were cross-model disagreers. The queue appends them
        # after the instability ranking, so a tight budget eats them first — and on
        # a judge that never wavers they are the entire signal.
        'n_dropped_cross_model': sum(
            1 for c in confusers[len(kept):] if c.get('reason') == 'cross_model'
        ),
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
#    labels:     [{source_index, value, tolerance?, grey_zone_id, label_source?}]}
# It replaces annotations.json while honouring the same downstream contract:
# `policy_labels` feeds retest, `policy_to_guidance` feeds rewrite_eval (§7).

_NUMERIC_TYPES = frozenset({'number', 'numeric'})

# Who actually decided a point's label. The human answers a *rule* question; the
# conductor then applies that rule to derive each point's value — so by default the
# ground truth the retest scores against is model-derived, not human-given. That is
# a real gap in the evidence and it is recorded rather than assumed away: the
# conductor reads the derived labels back and marks the ones the human confirmed.
#   dataset_reference — the label came from the dataset's own ground truth, not
#   from this conversation at all. Counted separately and never merged into the
#   human-answer total: a dataset label is someone's prior judgement, possibly
#   stale, possibly written against a different version of the rubric.
LABEL_SOURCES = frozenset({'derived', 'human_confirmed', 'dataset_reference'})
_DEFAULT_LABEL_SOURCE = 'derived'


def _check_value_in_space(value: Any, output_type: str, verdict_space: dict[str, Any], label: Any) -> None:
    """Raise if `value` is not something the judge can actually emit.

    SKILL.md §6.4 tells the conductor never to invent a label or move the scale;
    nothing enforced it. An invented categorical label passed validation, entered
    the rewrite guidance, and then scored 0 accuracy at step 8 — which reads as a
    judge failure rather than as the policy error it is.
    """
    if output_type == 'boolean':
        if not isinstance(value, bool):
            raise ValueError(
                f'boolean grey_zone_policy label must be true or false, got {value!r}: {label!r}'
            )
        return
    if output_type == 'categorical':
        declared = list(verdict_space.get('labels') or [])
        if declared and value not in declared:
            raise ValueError(
                f'grey_zone_policy label {value!r} is not one of the evaluator\'s declared labels '
                f'({", ".join(map(str, declared))}) — the rewritten judge answers in the same terms '
                f'as the old one, so a new label makes nothing comparable: {label!r}'
            )
        return
    if output_type in _NUMERIC_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'numeric grey_zone_policy label must be a number, got {value!r}: {label!r}')
        scale = verdict_space.get('scale')
        if isinstance(scale, (list, tuple)) and len(scale) == 2 and not (scale[0] <= value <= scale[1]):
            raise ValueError(
                f'grey_zone_policy label {value} is outside the evaluator\'s scale '
                f'[{scale[0]}, {scale[1]}] — the rewrite may not move the scale: {label!r}'
            )


def validate_policy(policy: dict[str, Any]) -> None:
    """Raise ``ValueError`` naming the first thing wrong with a grey_zone_policy.

    Fail loud so a malformed policy never silently drops a point's label, lets a
    numeric label through without its tolerance band (§3.5), or carries a value the
    judge's verdict space does not contain. Returns None on success.
    """
    output_type = (policy.get('output_type') or '').strip().lower()
    if not output_type:
        raise ValueError('grey_zone_policy is missing output_type')

    labels = policy.get('labels')
    if not isinstance(labels, list):
        raise ValueError('grey_zone_policy.labels must be a list of per-point labels')

    verdict_space = policy.get('verdict_space') or {}
    zone_ids = {gz.get('id') for gz in policy.get('grey_zones', []) if isinstance(gz, dict)}

    for label in labels:
        if 'source_index' not in label:
            raise ValueError(f'grey_zone_policy label is missing source_index: {label!r}')
        if 'value' not in label or label['value'] is None:
            raise ValueError(f'grey_zone_policy label is missing value: {label!r}')
        if output_type in _NUMERIC_TYPES:
            if 'tolerance' not in label:
                raise ValueError(
                    f'numeric grey_zone_policy label needs a tolerance band (target_score ± tolerance): {label!r}'
                )
            tolerance = label['tolerance']
            if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0:
                raise ValueError(
                    f'numeric grey_zone_policy tolerance must be a non-negative number: {label!r}'
                )
        _check_value_in_space(label['value'], output_type, verdict_space, label)
        source = label.get('label_source', _DEFAULT_LABEL_SOURCE)
        if source not in LABEL_SOURCES:
            raise ValueError(
                f'grey_zone_policy label_source must be one of {sorted(LABEL_SOURCES)}, got {source!r}: {label!r}'
            )
        zone = label.get('grey_zone_id')
        if zone is not None and zone_ids and zone not in zone_ids:
            raise ValueError(
                f'grey_zone_policy label references grey_zone_id {zone!r}, which no grey zone declares: {label!r}'
            )


def label_provenance(policy: dict[str, Any]) -> dict[str, int]:
    """How many per-point labels the human confirmed vs the conductor derived.

    Reported by retest so the agreement number states what it was scored against:
    a rewrite validated entirely against labels the same model derived from its own
    reading of the rule is a weaker claim than one a human signed off on, and the
    number alone cannot tell them apart.
    """
    counts = {source: 0 for source in sorted(LABEL_SOURCES)}
    for label in policy.get('labels', []):
        counts[label.get('label_source', _DEFAULT_LABEL_SOURCE)] = (
            counts.get(label.get('label_source', _DEFAULT_LABEL_SOURCE), 0) + 1
        )
    return counts


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
