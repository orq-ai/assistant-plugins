"""Pure helpers for data seeding (RES-980 §11) — orq datapoint ↔ trace row.

When a run is starved (< 10 usable datapoints, or a flat instability profile),
the conductor can pull rows from an orq dataset or from conductor-generated
synthetic datapoints. Both arrive in orq's datapoint shape
`{inputs: {...}, messages, expected_output}`; the pipeline consumes trace rows
`{query, output, messages, reference, ...}`. This module maps between them and
resolves/validates the evaluator's `{{variables}}` against a datapoint's fields.

No I/O and no evaluatorq/orq imports — safe to import directly on Windows. The
suffix rules come from `lib.content.field_for_variable`, the single table the
trace scanner reverses with and the judge renders with, so a seeded row renders
through the judge exactly like a real trace. This module used to keep its own
copy and "mirror" it by hand; the copy drifted (it had no branch for the
`reference | expected | expected_output` leaves, which silently skipped *every*
row of an evaluator declaring `{{log.reference}}`), which is why there is now
one table and no mirrors.
"""

from __future__ import annotations

from typing import Any

from lib.content import field_for_variable


def map_datapoint(datapoint: dict[str, Any]) -> dict[str, Any]:
    """orq datapoint `{inputs, messages, expected_output}` → trace row fields
    `{query, output, messages, reference}` via the shared suffix rules."""
    row: dict[str, Any] = {'query': '', 'output': '', 'messages': None, 'reference': ''}
    for name, value in (datapoint.get('inputs') or {}).items():
        field = field_for_variable(name)
        if field is not None:
            row[field] = value
    if row['messages'] is None and datapoint.get('messages') is not None:
        row['messages'] = datapoint['messages']
    # The datapoint's own `expected_output` is the canonical ground truth, but it
    # must not blank a reference an `inputs` key already supplied — the leaf table
    # maps `{{...expected_output}}` too, so both routes can carry it.
    if datapoint.get('expected_output') or not row['reference']:
        row['reference'] = datapoint.get('expected_output') or row['reference'] or ''
    return row


def unresolved_variables(row: dict[str, Any], variables: list[str]) -> list[str]:
    """The evaluator `{{variables}}` a mapped row can NOT satisfy — either the
    field the variable's leaf maps to is empty/None, or the leaf is one the row
    shape can't carry (only query/output/messages/reference are fillable, mirroring
    `make_replacements`). Empty list ⇒ the row is usable as-is; anything returned
    is what the conductor must map by hand (§11.4) before the row is judged.

    Fillability is decided by the same `field_for_variable` table the judge renders
    with, so this cannot fall out of step with it again — the reference family
    (`reference | expected | expected_output`) had no branch here while the judge
    filled it fine, which silently skipped every row of an evaluator declaring
    `{{log.reference}}`."""
    missing: list[str] = []
    for var in variables:
        field = field_for_variable(var)
        if field is None:
            ok = False  # unknown leaf — a standard datapoint can't fill it
        elif field == 'messages':
            ok = row.get('messages') is not None
        else:
            ok = bool(row.get(field))
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
        # Only query/output go in `inputs`; `messages` and `expected_output` are
        # top-level datapoint fields in orq's shape, added below.
        if field_for_variable(var) in ('query', 'output'):
            inputs[var] = row.get(field_for_variable(var), '')
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
