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

from typing import Any

# Suffix → row field. Evaluators name their variables differently
# (`log.input`/`log.output`, `query`/`output`, `input`/`response`, ...), so both
# directions match on the last dotted segment rather than the full name.
_FIELD_BY_LEAF: dict[str, str] = {
    'input': 'query',
    'query': 'query',
    'prompt': 'query',
    'output': 'output',
    'response': 'output',
    'completion': 'output',
    'answer': 'output',
    'messages': 'messages',
    'history': 'messages',
    'conversation': 'messages',
    'reference': 'reference',
    'expected': 'reference',
    'expected_output': 'reference',
}


def field_for_variable(var: str) -> str | None:
    """Row field a judge template variable reads from, or None if unmappable.

    None is meaningful in both directions: the renderer leaves an unmapped
    `{{var}}` literal (evaluatorq's `render_template` keeps it rather than
    blanking it, so nothing silently vanishes) and the scanner drops an
    unmappable recovered value instead of guessing a field for it.
    """
    return _FIELD_BY_LEAF.get(var.split('.')[-1].strip().lower())


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
