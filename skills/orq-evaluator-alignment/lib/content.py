"""The vocabulary shared by the trace scanner and the judge reconstruction.

Two things have to agree between `scripts/fetch_traces.py` (which reads content
*out of* production spans) and `lib/judge.py` (which renders that content back
*into* the judge prompt). When they drift, the failure is silent — a row that
looks fine in `traces.jsonl` re-judges against a blank variable:

- **How a message's text is found.** orq emits at least three message shapes
  (flat `content` string, Responses-API `parts[].content`, multimodal
  `content: [...]`). The scanner learned the parts shape in this skill's
  Responses-API fix; `lib.judge` rendering the same messages with a
  `content`-only reader would undo it.
- **Which row field a `{{var}}` maps to.** The scanner reverses the judge
  prompt's substitution (`_assign_io`) and the judge re-applies it
  (`make_replacements`). Two copies of the suffix table means a variable can be
  recovered into a field the renderer never reads back.

Deliberately stdlib-only: `lib/judge` imports evaluatorq at module scope, and
`fetch_traces` must stay importable without it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Suffix → row field. Evaluators name their variables differently
# (`log.input`/`log.output`, `input.user_query`/`output.response`, ...), so both
# directions match on the last dotted segment rather than the full name. Do not
# annotate entries with the orq version that introduced a spelling: matching is
# leaf-only and version-agnostic, so such a label goes stale the next time the
# platform renames a namespace and nothing here has to change.
_FIELD_BY_LEAF: dict[str, str] = {
    'input': 'query',
    'query': 'query',
    'prompt': 'query',
    'user_query': 'query',
    'output': 'output',
    'response': 'output',
    'completion': 'output',
    'answer': 'output',
    'messages': 'messages',
    'history': 'messages',
    'conversation': 'messages',
    'all_messages': 'messages',
    'reference': 'reference',
    'expected': 'reference',
    'expected_output': 'reference',
    'retrievals': 'retrievals',
    'system_instructions': 'system_instructions',
    'tools_called': 'tools_called',
    'tool_calls': 'tools_called',
}


def field_for_variable(var: str) -> str | None:
    """Row field a judge template variable reads from, or None if unmappable.

    None is meaningful in both directions: the renderer leaves an unmapped
    `{{var}}` literal (evaluatorq's `render_template` keeps it rather than
    blanking it, so nothing silently vanishes) and the scanner drops an
    unmappable recovered value instead of guessing a field for it.
    """
    return _FIELD_BY_LEAF.get(var.split('.')[-1].strip().lower())


def reference_is_judge_input(variables: list[str]) -> bool:
    """True iff a declared template variable is bound from the `reference` row field.

    `metrics.py`'s correctness check wants to grade the judge's verdict against
    `reference` as ground truth. But `lib.judge.make_replacements` binds row
    fields into the judge prompt through this same `_FIELD_BY_LEAF` table — so an
    evaluator that declares `{{log.reference}}` (or `{{expected}}` /
    `{{expected_output}}`) was handed the answer before it answered. Grading that
    verdict against `reference` afterwards is circular, not a measurement: it
    reports the judge's ability to read back what it was just shown.
    """
    return any(field_for_variable(v) == 'reference' for v in variables)


def message_text(m: Any) -> str:
    """Flatten one message's text across the shapes orq emits.

    - Chat Completions: a flat ``content`` string.
    - Responses API: text nests under ``parts[].content`` (or ``parts[].text``).
    - Multimodal chat: ``content`` is itself a list of parts.
    Returns '' for a message with no recoverable text.
    """
    if not isinstance(m, dict):
        return ''
    content = m.get('content')
    if isinstance(content, str) and content:
        return content
    chunks: list[str] = []
    containers = [m.get('parts')]
    if isinstance(content, list):
        containers.append(content)
    for container in containers:
        if not isinstance(container, list):
            continue
        for p in container:
            if isinstance(p, str):
                chunks.append(p)
            elif isinstance(p, dict):
                t = p.get('content') or p.get('text')
                if isinstance(t, str) and t:
                    chunks.append(t)
    return '\n\n'.join(chunks)


# Roles that can stand in for a judged input/output. `system` and `tool` never
# can: a system turn is instructions to the model, and a tool turn is machinery.
_OUTPUT_ROLE = 'assistant'
_QUERY_ROLE = 'user'


def derive_io_from_messages(messages: Any) -> dict[str, str]:
    """Recover `{query, output}` from a conversation, by rule.

    A dataset row often carries the exchange under `messages` and nothing under
    `inputs` — its `inputs` hold metadata (category, difficulty, ground_truth)
    while the thing being judged is the conversation itself. `log.output` is then
    unmistakably the assistant's answer, but no code path said so, and every such
    row was skipped as unmappable while carrying exactly what the judge needed.

    The rule, deliberately fixed and documented rather than inferred:
      - `output` ← text of the **last** `assistant` message
      - `query`  ← text of the **last** `user` message

    This is not a guess about intent, which the skill refuses to make; it is a
    contract, applied the same way every time and disclosed by the caller. A role
    that is absent yields `''` and the row stays unmappable — the failure mode is
    preserved for data that genuinely cannot be mapped. Messages with no `role`
    are ignored rather than assigned positionally: "the last message is probably
    the answer" is exactly the inference this avoids.

    Extraction goes through `message_text`, so a Responses-API `parts[]` turn and
    a multimodal `content: [...]` turn resolve identically to the trace scanner's
    reading of the same conversation.
    """
    out = {'query': '', 'output': ''}
    if not isinstance(messages, list):
        return out
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get('role')
        if not isinstance(role, str):
            continue  # roleless: do not guess positionally
        role = role.strip().lower()
        if role == _OUTPUT_ROLE:
            out['output'] = message_text(message) or out['output']
        elif role == _QUERY_ROLE:
            out['query'] = message_text(message) or out['query']
    return out


def stringify_messages(messages: Any) -> str:
    """Render a conversation as ``role: text`` lines for a {{messages}} variable.

    Uses `message_text` per turn, so a Responses-API conversation renders its
    real text instead of the empty ``user:`` a ``content``-only reader produces.
    """
    if not messages:
        return ''
    if isinstance(messages, str):
        return messages
    if isinstance(messages, list):
        lines = []
        for m in messages:
            if isinstance(m, dict):
                lines.append(f'{m.get("role", "?")}: {message_text(m)}')
            else:
                lines.append(str(m))
        return '\n'.join(lines)
    return str(messages)


def stringify_retrievals(retrievals: Any) -> str:
    """Render knowledge-base retrievals for a `{{...retrievals}}` variable.

    Newline-joined chunks, verbatim — that is what orq itself substitutes, checked
    against a live evaluator invoked with three retrieval chunks (2026-08-31). The
    local judge has to match it: this skill re-renders the judge prompt instead of
    invoking the stored evaluator, so a different separator here would measure the
    judge on a prompt production never showed it.
    """
    if not retrievals:
        return ''
    if isinstance(retrievals, str):
        return retrievals
    if isinstance(retrievals, list):
        return '\n'.join(r if isinstance(r, str) else str(r) for r in retrievals)
    return str(retrievals)


def stringify_tools_called(tools: Any) -> str:
    """Render an agent's tool calls for a `{{...tools_called}}` variable.

    orq does not hand the judge the value the caller passed; it formats it, and
    this skill re-renders judge prompts locally instead of invoking the stored
    evaluator (see `lib.judge`), so the formatting has to be reproduced or the
    judge is graded on a prompt production never showed it. Measured against a
    live evaluator (2026-08-31), the format is::

        1. lookup_order({"id":42})
           Status: ✅ Success
           Response: shipped

        2. send_email({"to":"a@b.c"})
           ...

    with a blank line between calls, `({})` for absent arguments, `Response: N/A`
    for a call with no result, `No tool calls were made.` for an empty list, and a
    call without a `name` dropped before numbering (so the surviving calls number
    from 1). A string passes through untouched: that is a block orq already
    rendered, recovered from a judge prompt by the trace stencil.

    **Ceiling: the status marker.** `ToolCalled.status` is
    `'' | in_progress | completed | incomplete | failed`, but the evaluator-invoke
    API drops it — every reachable probe rendered `✅ Success`, so that is the only
    marker confirmed. A non-empty, non-`completed` status prints its own value
    rather than a guessed emoji: showing the raw word is wrong in formatting where
    inventing `❌` would be wrong about the run. Replace this branch once a real
    agent trace shows what orq prints for a failed call.
    """
    if isinstance(tools, str):
        return tools
    if tools is None:
        return ''
    if not isinstance(tools, list):
        return str(tools)
    calls = [t for t in tools if isinstance(t, dict) and t.get('name')]
    if not calls:
        return 'No tool calls were made.'
    blocks = []
    for i, call in enumerate(calls, 1):
        status = str(call.get('status') or '').strip().lower()
        marker = '✅ Success' if status in ('', 'completed') else status
        blocks.append(
            f'{i}. {call["name"]}({call.get("arguments") or "{}"})\n'
            f'   Status: {marker}\n'
            f'   Response: {call.get("output") or "N/A"}'
        )
    return '\n\n'.join(blocks)


def judged_input_key(row: dict[str, Any]) -> str:
    """Stable identity of one datapoint: the content the judge actually scores.

    Keys off the fields `lib.judge.make_replacements` feeds the judge
    (query / output / reference / messages, plus retrievals / system_instructions /
    tools_called when a row carries them), NOT trace/span provenance — so the same input
    captured on two different traces is one datapoint. Serialised to a
    stable JSON string so an unhashable value (a `messages` list) still keys.

    Lives here rather than in `fetch_traces` because two callers now need it: the
    scanner's exact-match dedup, and `traces_fingerprint` below.
    """
    key: list[Any] = [row.get('query', ''), row.get('output', ''), row.get('reference', ''), row.get('messages')]
    # Appended only when present, so a row that carries neither keys exactly as it
    # did before these fields existed. Unconditional inclusion would change every
    # row's identity and so every `traces_fingerprint`, making each in-flight run
    # refuse its own stability.json for a field almost no row has.
    extra = {f: row[f] for f in ('retrievals', 'system_instructions', 'tools_called') if row.get(f)}
    if extra:
        key.append(extra)
    return json.dumps(key, sort_keys=True, ensure_ascii=False, default=str)


def traces_fingerprint(rows: list[dict[str, Any]]) -> str:
    """Content fingerprint of a `traces.jsonl` row list — `'<n>:<sha256[:16]>'`.

    `source_index` is a row's *position* in `traces.jsonl`, and that is what every
    human label is keyed by. `fetch_traces` rewrites the file wholesale (dedup
    included), so re-fetching after labelling renumbers every row underneath the
    labels: retest then pairs a label to a different datapoint and reports a clean
    agreement score for a comparison that never happened. Only a *total* miss
    raises today; partial overlap is silent.

    So the artifacts that depend on the ordering (`stability.json`, `queue.json`)
    record this fingerprint, and the consumers refuse when it no longer matches the
    file in front of them. Order-sensitive on purpose: a reordering is exactly the
    failure being guarded against.
    """
    digest = hashlib.sha256()
    for row in rows:
        digest.update(judged_input_key(row).encode('utf-8'))
        digest.update(b'\x1e')
    return f'{len(rows)}:{digest.hexdigest()[:16]}'
