"""Pure helpers for data seeding (RES-980 §11) — orq datapoint ↔ trace row.

When a run is starved (< 10 usable datapoints, or a flat instability profile),
the conductor can pull rows from an orq dataset or from conductor-generated
synthetic datapoints. Both arrive in orq's datapoint shape
`{inputs: {...}, messages, expected_output}`; the pipeline consumes trace rows
`{query, output, messages, reference, ...}`. This module maps between them and
resolves/validates the evaluator's `{{variables}}` against a datapoint's fields.

No I/O and no evaluatorq/orq imports — safe to import directly on Windows. The
suffix rules mirror `fetch_traces._assign_io` / `lib.judge.make_replacements` so
a seeded row renders through the judge exactly like a real trace.
"""

from __future__ import annotations

from typing import Any

# Which `{{variable}}` leaf names map onto which trace-row field. Mirrors
# fetch_traces._assign_io — keep in sync so seeded rows judge like real traces.
_QUERY_LEAVES = frozenset({'input', 'query', 'prompt'})
_OUTPUT_LEAVES = frozenset({'output', 'response', 'completion', 'answer'})
_MESSAGE_LEAVES = frozenset({'messages', 'history', 'conversation'})
_REFERENCE_LEAVES = frozenset({'reference', 'expected', 'expected_output'})


def _leaf(name: str) -> str:
    """The trailing dotted segment of a variable name, normalised (`log.input` → `input`)."""
    return name.split('.')[-1].strip().lower()


def map_datapoint(datapoint: dict[str, Any]) -> dict[str, Any]:
    """orq datapoint `{inputs, messages, expected_output}` → trace row fields
    `{query, output, messages, reference}` via the shared suffix rules."""
    row: dict[str, Any] = {'query': '', 'output': '', 'messages': None, 'reference': ''}
    for name, value in (datapoint.get('inputs') or {}).items():
        leaf = _leaf(name)
        if leaf in _QUERY_LEAVES:
            row['query'] = value
        elif leaf in _OUTPUT_LEAVES:
            row['output'] = value
        elif leaf in _MESSAGE_LEAVES:
            row['messages'] = value
    if row['messages'] is None and datapoint.get('messages') is not None:
        row['messages'] = datapoint['messages']
    row['reference'] = datapoint.get('expected_output') or ''
    return row


def unresolved_variables(row: dict[str, Any], variables: list[str]) -> list[str]:
    """The evaluator `{{variables}}` a mapped row can NOT satisfy — either the
    field the variable's leaf maps to is empty/None, or the leaf is one the row
    shape can't carry (only query/output/messages/reference are fillable, mirroring
    `make_replacements`). Empty list ⇒ the row is usable as-is; anything returned
    is what the conductor must map by hand (§11.4) before the row is judged.

    The reference family must stay in step with `judge.make_replacements`: it fills
    `reference | expected | expected_output` from `row['reference']`, so omitting
    them here would skip every row of an evaluator declaring `{{log.reference}}`
    even though the judge renders it fine."""
    missing: list[str] = []
    for var in variables:
        leaf = _leaf(var)
        if leaf in _QUERY_LEAVES:
            ok = bool(row.get('query'))
        elif leaf in _OUTPUT_LEAVES:
            ok = bool(row.get('output'))
        elif leaf in _MESSAGE_LEAVES:
            ok = row.get('messages') is not None
        elif leaf in _REFERENCE_LEAVES:
            ok = bool(row.get('reference'))
        else:
            ok = False  # unknown leaf — a standard datapoint can't fill it
        if not ok:
            missing.append(var)
    return missing


def row_to_datapoint(row: dict[str, Any], variables: list[str]) -> dict[str, Any]:
    """Trace row → orq datapoint for save-back (§11.3 opt 3). `inputs` is keyed by
    the evaluator's own variable names (so the saved dataset is reusable by the
    same evaluator), filled from the row by the suffix rules; `messages` and
    `expected_output` ride along when present."""
    inputs: dict[str, Any] = {}
    for var in variables:
        leaf = _leaf(var)
        if leaf in _QUERY_LEAVES:
            inputs[var] = row.get('query', '')
        elif leaf in _OUTPUT_LEAVES:
            inputs[var] = row.get('output', '')
    datapoint: dict[str, Any] = {'inputs': inputs, 'expected_output': row.get('reference') or ''}
    if row.get('messages') is not None:
        datapoint['messages'] = row['messages']
    return datapoint


def rows_from_datapoints(
    datapoints: list[dict[str, Any]], variables: list[str], *, tag: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert a batch of orq datapoints into trace rows, shared by both the
    dataset-hookup and synthetic paths. Each usable row is `tag`-stamped
    (`{source: "dataset:<id>"}` or `{synthetic: True}`) and carries a per-datapoint
    `rationale` when present. Datapoints missing a required variable are collected
    into `skipped` (never fabricated). Returns `(rows, skipped)`."""
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for i, dp in enumerate(datapoints):
        row = map_datapoint(dp)
        missing = unresolved_variables(row, variables)
        if missing:
            skipped.append({'index': i, 'missing': missing})
            continue
        if tag:
            row.update(tag)
        if dp.get('rationale'):
            row['rationale'] = dp['rationale']
        rows.append(row)
    return rows, skipped
