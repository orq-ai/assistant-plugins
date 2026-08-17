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

from lib.content import derive_io_from_messages, field_for_variable


def map_datapoint(datapoint: dict[str, Any], mapping: dict[str, str] | None = None) -> dict[str, Any]:
    """orq datapoint `{inputs, messages, expected_output}` → trace row fields
    `{query, output, messages, reference}` via the shared suffix rules.

    `inputs` always wins. When a field is *still* empty afterwards it is derived
    from `messages` (`lib.content.derive_io_from_messages`): the last assistant
    turn is the `output`, the last user turn the `query`. A dataset whose `inputs`
    carry only metadata — category, difficulty, ground_truth — while the exchange
    itself lives under `messages` used to map **zero** rows, reporting every one as
    `missing ['log.output']` when the output was sitting right there. Production
    traces never hit this because orq's `gen_ai.input` supplies `output` outright;
    the dataset path is the one that has to derive, and had no rule to derive with.
    """
    row: dict[str, Any] = {'query': '', 'output': '', 'messages': None, 'reference': ''}
    for name, value in (datapoint.get('inputs') or {}).items():
        field = field_for_variable(name)
        if field is not None:
            row[field] = value
    if row['messages'] is None and datapoint.get('messages') is not None:
        row['messages'] = datapoint['messages']
    derived = derive_io_from_messages(row['messages'])
    for field in ('query', 'output'):
        if not row[field] and derived[field]:
            row[field] = derived[field]
    # An explicit --map is the user answering the question themselves, so it wins
    # over both the inputs pass and the derivation.
    for var, source in (mapping or {}).items():
        field = field_for_variable(var)
        if field is not None:
            value = resolve_map_source(datapoint, source)
            if value not in (None, ''):
                row[field] = value
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


# ── explicit mapping (§2.4) ───────────────────────────────────────────────────
# A deliberately tiny grammar. Anything outside it is an error rather than a
# silent skip: a typo'd mapping that quietly maps nothing is the same failure the
# inventory above exists to end.
_MAP_GRAMMAR = (
    'inputs.<key> | messages.<role>.last | messages.<role>.first | messages.all | expected_output'
)


def parse_map_spec(specs: list[str] | str | None) -> dict[str, str]:
    """`['log.output=messages.assistant.last']` → `{'log.output': '...'}`.

    Raises `ValueError` naming the offending term — never returns a partial map,
    because a mapping the user believes is applied and isn't is worse than a stop.
    """
    if not specs:
        return {}
    if isinstance(specs, str):
        specs = [specs]
    out: dict[str, str] = {}
    for spec in specs:
        if '=' not in str(spec):
            raise ValueError(f'--map needs <variable>=<source>, got {spec!r}. Sources: {_MAP_GRAMMAR}')
        var, _, source = str(spec).partition('=')
        var, source = var.strip(), source.strip()
        if not var or not _valid_map_source(source):
            raise ValueError(f'--map source {source!r} is not one of: {_MAP_GRAMMAR}')
        out[var] = source
    return out


def _valid_map_source(source: str) -> bool:
    if source == 'expected_output' or source == 'messages.all':
        return True
    if source.startswith('inputs.'):
        return len(source.split('.', 1)[1]) > 0
    parts = source.split('.')
    return len(parts) == 3 and parts[0] == 'messages' and parts[2] in ('last', 'first') and bool(parts[1])


def resolve_map_source(datapoint: dict[str, Any], source: str) -> Any:
    """The value a validated `--map` source names, or '' when the datapoint lacks it."""
    from lib.content import message_text, stringify_messages

    if source == 'expected_output':
        return datapoint.get('expected_output') or ''
    messages = datapoint.get('messages')
    if source == 'messages.all':
        return stringify_messages(messages) if messages else ''
    if source.startswith('inputs.'):
        return (datapoint.get('inputs') or {}).get(source.split('.', 1)[1], '')
    _, role, which = source.split('.')
    matches = [
        m for m in (messages or [])
        if isinstance(m, dict) and str(m.get('role', '')).strip().lower() == role.lower()
    ]
    if not matches:
        return ''
    return message_text(matches[-1] if which == 'last' else matches[0])


# ── inventory diagnostics (§2.3) ──────────────────────────────────────────────


def dataset_inventory(
    datapoints: list[dict[str, Any]], variables: list[str]
) -> dict[str, Any]:
    """What the dataset actually holds, against what the judge needs.

    Reported **per dataset, not per row**: 42 identical `missing ['log.output']`
    lines say nothing 1 line doesn't, and they bury the one fact that resolves the
    problem — which fields *are* present and what they could map to.
    """
    from lib.content import derive_io_from_messages

    inputs_keys: dict[str, str | None] = {}
    roles: set[str] = set()
    n_messages = n_expected = 0
    derivable: dict[str, int] = {'query': 0, 'output': 0}
    for dp in datapoints:
        for key in (dp.get('inputs') or {}):
            inputs_keys.setdefault(key, field_for_variable(key))
        messages = dp.get('messages')
        if messages:
            n_messages += 1
            for m in messages if isinstance(messages, list) else []:
                if isinstance(m, dict) and isinstance(m.get('role'), str):
                    roles.add(m['role'].strip().lower())
            for field, value in derive_io_from_messages(messages).items():
                if value:
                    derivable[field] += 1
        if dp.get('expected_output'):
            n_expected += 1
    return {
        'n_datapoints': len(datapoints),
        'needed': [{'variable': v, 'field': field_for_variable(v)} for v in variables],
        'inputs_keys': inputs_keys,
        'n_with_messages': n_messages,
        'message_roles': sorted(roles),
        'n_with_expected_output': n_expected,
        'derivable_from_messages': derivable,
    }


def format_inventory(inv: dict[str, Any], n_mapped: int, missing_fields: list[str]) -> str:
    """The one-read diagnostic block (§2.3): what was needed, what is there, what to do."""
    n = inv['n_datapoints']
    needed = ', '.join(
        f"{d['variable']!r} → row field {d['field']!r}" if d['field'] else f"{d['variable']!r} → (unfillable leaf)"
        for d in inv['needed']
    ) or '(no declared variables)'
    lines = [f'{n_mapped}/{n} rows mapped. The judge needs: {needed}.', '  Dataset fields seen:']

    keys = inv['inputs_keys']
    if keys:
        mapped_note = ', '.join(
            f'{k} → {v}' if v else k for k, v in sorted(keys.items())
        )
        lines.append(f'    inputs keys     : {mapped_note}')
    else:
        lines.append('    inputs keys     : (none)')

    if inv['n_with_messages']:
        lines.append(
            f"    messages        : {inv['n_with_messages']}/{n} rows, roles {inv['message_roles']}"
        )
        for field, count in sorted(inv['derivable_from_messages'].items()):
            if count:
                role = 'assistant' if field == 'output' else 'user'
                lines.append(f"        └ {field!r} derivable from the last {role} turn ({count}/{n} rows)")
    else:
        lines.append('    messages        : (none)')

    if inv['n_with_expected_output']:
        lines.append(f"    expected_output : {inv['n_with_expected_output']}/{n} rows → 'reference'")

    if missing_fields:
        lines.append(
            f"  Still missing: {sorted(set(missing_fields))}. Map it explicitly, e.g. "
            f"--map \"{missing_fields[0]}=messages.assistant.last\"  (sources: {_MAP_GRAMMAR})"
        )
    return '\n'.join(lines)


def rows_from_datapoints(
    datapoints: list[dict[str, Any]], variables: list[str], *, tag: dict[str, Any] | None = None,
    mapping: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert a batch of orq datapoints into trace rows, shared by both the
    dataset-hookup and synthetic paths. Each usable row is `tag`-stamped
    (`{source: "dataset:<id>"}` or `{synthetic: True}`) and carries a per-datapoint
    `rationale` when present. Datapoints missing a required variable are collected
    into `skipped` (never fabricated). Returns `(rows, skipped)`."""
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for i, dp in enumerate(datapoints):
        row = map_datapoint(dp, mapping)
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
