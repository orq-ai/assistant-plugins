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

from typing import Any


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


def _truncate(text: str, max_chars: int) -> str:
    """Cut `text` to `max_chars`, flagging the cut with a trailing ellipsis so the
    conductor can tell a value was elided (payload size is bounded on purpose, §8)."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + '…'


def _compact_input(item: dict[str, Any], max_chars: int) -> str:
    """The judged input, compactly. Prefer the inverted `{{variable}}` bindings
    (what the judge actually scored) over the whole rendered prompt; fall back to
    the raw `output` when template inversion failed (`variables` is None)."""
    variables = item.get('variables')
    if variables:
        rendered = '\n'.join(f'{v.get("name")}: {v.get("value")}' for v in variables)
    else:
        rendered = item.get('output') or item.get('query') or ''
    return _truncate(rendered, max_chars)


def assemble_payload(
    queue: dict[str, Any], *, top_k: int | None = None, max_chars: int = 600
) -> dict[str, Any]:
    """`queue.json` dict → compact confuser payload for the conductor's context.

    Bounded on purpose (§8): `top_k` caps how many confusers enter context (queue
    items are already most-unstable-first), and `max_chars` truncates each judged
    input. The verdict split + band + one representative rationale is what the
    conductor open-codes into grey zones.
    """
    verdict_space = queue.get('meta', {}).get('verdict_space', {})
    output_type = (verdict_space.get('type') or 'boolean').strip().lower()

    items = queue.get('items', [])
    if top_k is not None and top_k >= 0:
        items = items[:top_k]

    confusers: list[dict[str, Any]] = []
    for item in items:
        ambiguity = item.get('ambiguity', {})
        votes = item.get('judge_votes', {})
        confusers.append(
            {
                'source_index': item.get('source_index'),
                'band': ambiguity.get('band'),
                'instability': ambiguity.get('instability'),
                'low_flip_sample': item.get('low_flip_sample', False),
                'input': _compact_input(item, max_chars),
                'verdict_split': _verdict_split(output_type, votes),
                'representative_reasoning': _truncate(votes.get('representative_explanation') or '', max_chars),
            }
        )

    return {
        'verdict_space': verdict_space,
        'n_confusers': len(confusers),
        'confusers': confusers,
    }


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
