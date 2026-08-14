# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "evaluatorq>=1.4.0",
#     "fire>=0.7.0",
#     "httpx>=0.27",
#     "loguru>=0.7.3",
#     "python-dotenv>=1.2.1",
#     "tenacity>=8.0",
# ]
# ///
"""Step 2 — pull production traces carrying the evaluator's results.

v3oql has no server-side "filter by evaluator" operator, so we scan recent
traces and match the evaluator client-side on each trace's spans:
  1. POST /v2/traces/v3oql        page recent traces (empty filter + fields:[])
  2. GET  /v2/traces/{id}/v3spans keep `span.evaluator` spans whose
     `attributes.orq.evaluator.id` is ours, then GET each span's full content

From each kept evaluator span we extract `(output, messages, judge_value,
judge_explanation, judge_model)` into `traces.jsonl` — the datapoint set the
stability run re-judges. `output` is the judge's rendered input kept verbatim
(no delimiter parsing — evaluators wrap their variables differently).
`judge_model` is the model the judge's LLM call actually ran on, read off the
child `span.chat_completion` (the config only stores an opaque model id). After
the scan we pin the most common observed model onto `evaluator.json` as
`judge_model` so step 4 reconstructs the real judge. The spans calls are
concurrency-bounded.

Because matching is client-side, the lever for an empty result is usually
scan depth (`--trace_limit`, default 200): a sparse or aged evaluator can sit
beyond the default window. On empty we echo the match + window used (never a
silent empty run) so the operator can raise `--trace_limit` or the date window
in config.toml.

Usage:
    uv run scripts/fetch_traces.py --run_dir runs/<key>_<ts>
    uv run scripts/fetch_traces.py --run_dir runs/<key>_<ts> --trace_limit 2000
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from datetime import datetime
from typing import Any

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner
from lib.content import field_for_variable, message_text
from lib.orq_client import OrqClient

load_dotenv()


def _evaluation_matches(span: dict[str, Any], evaluator_id: str, evaluator_key: str) -> list[dict[str, Any]]:
    """Return a normalised verdict if this span is *our* evaluator's result.

    Evaluator results live in spans of ``type == 'span.evaluator'``. The evaluator
    is identified by ``attributes.orq.evaluator.id`` (exact id, preferred) or
    ``.key`` (the display name). The boolean verdict and explanation live under
    ``attributes.gen_ai.evaluation`` (``score.value`` is 1/0; ``passed`` is the
    bool fallback). Returns ``[]`` for any other span.
    """
    if span.get('type') != 'span.evaluator':
        return []
    attrs = span.get('attributes') or {}
    ev = ((attrs.get('orq') or {}).get('evaluator')) or {}
    matched = (evaluator_id and ev.get('id') == evaluator_id) or (
        evaluator_key and ev.get('key') == evaluator_key
    )
    if not matched:
        return []
    evaluation = (attrs.get('gen_ai') or {}).get('evaluation') or {}
    score = evaluation.get('score') or {}
    if isinstance(score, dict) and score.get('value') is not None:
        value: Any = score.get('value')
    else:
        value = evaluation.get('passed')
    return [
        {
            'value': value,
            'explanation': evaluation.get('explanation'),
            'evaluator_id': ev.get('id'),
            'evaluator_key': ev.get('key'),
        }
    ]


# Span types that carry the judge's own LLM call. orq emits Chat Completions
# (``span.chat_completion``) and Responses API (``span.responses``) shapes; both
# store the rendered judge prompt under ``gen_ai.input.messages``.
_JUDGE_SPAN_TYPES = {'span.chat_completion', 'span.responses', 'span.llm'}


def _span_id(span: dict[str, Any]) -> Any:
    """This span's id under either of the two keys orq's two views use."""
    return span.get('span_id') or span.get('_id')


def _is_root_span(span: dict[str, Any]) -> bool:
    """True for the trace-level span carrying the structured content under
    evaluation (``_structured_io``'s source). One predicate, because
    ``_content_source_span_ids`` must blame exactly the spans that are read."""
    return span.get('type') == 'trace' or ((span.get('attributes') or {}).get('type')) == 'workflow_run'


def _parent_of(span: dict[str, Any]) -> Any:
    """The span's parent id, normalised across the fields orq actually populates.

    ``parent_span_id`` alone is NOT enough, and the difference is not cosmetic:
    on a Responses-API trace the judge span (``span.responses``) arrives with
    ``parent_span_id`` absent and the real link on ``parent_id`` (OTel-bridged
    spans also carry it at ``attributes.orq.bridge.parent_span_id``) — see
    ``tests/fixtures/responses_api_trace.json``. Scoping on ``parent_span_id``
    alone therefore finds *no* child on exactly the span shape this scanner was
    fixed to support, silently falling back to "any judge span in the trace" and
    reading another evaluator call's prompt, model and content.
    """
    return (
        span.get('parent_span_id')
        or span.get('parent_id')
        or (((span.get('attributes') or {}).get('orq') or {}).get('bridge') or {}).get('parent_span_id')
    )


def _judge_spans(spans: list[dict[str, Any]], eval_span: dict[str, Any]) -> list[dict[str, Any]]:
    """The judge LLM-call spans belonging to THIS eval span, best-effort.

    One scoper for all three readers (`_judge_io`, `_judge_model`,
    `_content_source_span_ids`) so they cannot disagree about which span a row's
    content came from.

    Children first (via `_parent_of`). With no child we fall back to the trace's
    judge spans **only when there is exactly one** — then "unscoped" and "this
    eval span's" are the same span. With several unparented candidates we return
    none: a trace with two evaluator calls would otherwise hand every row the
    first judge span's prompt, model and content, and the row is non-empty so
    nothing downstream catches it. Ambiguous is not the same as unscoped, and
    `_judge_io`/`_judge_model` still fall back to the eval span's own gen_ai.
    """
    esid = _span_id(eval_span)
    chats = [s for s in spans if isinstance(s, dict) and s.get('type') in _JUDGE_SPAN_TYPES]
    children = [s for s in chats if esid is not None and _parent_of(s) == esid]
    if children:
        return children
    return chats if len(chats) == 1 else []


def _judge_io(spans: list[dict[str, Any]], eval_span: dict[str, Any]) -> tuple[str, Any]:
    """Return (rendered_input, messages) the judge actually saw.

    The content under evaluation is rendered into the judge's own LLM call (a
    ``span.chat_completion`` or ``span.responses`` span, its ``gen_ai.input.
    messages``). We keep those messages verbatim and do NOT parse delimiters out
    of the prompt: evaluators wrap their template variables differently (some use
    ``<output>`` tags, some don't), so tag-stripping is not portable. The judge
    span is scoped by ``_judge_spans``; we then fall back to the eval span's own
    gen_ai input.
    """
    # Newer orq schema records the judge's LLM call ON the evaluator span itself
    # (no separate child span), so fall back to the eval span's own gen_ai input.
    # Without this, evaluators that don't emit a child judge span yield empty
    # query/output — hollow datapoints behind a green pipeline.
    for s in [*_judge_spans(spans, eval_span), eval_span]:
        msgs = (((s.get('attributes') or {}).get('gen_ai') or {}).get('input') or {}).get('messages')
        if msgs:
            rendered = '\n\n'.join(message_text(m) for m in msgs)
            if rendered.strip():
                return rendered, msgs
    return '', None


def _content_source_span_ids(spans: list[dict[str, Any]], eval_span: dict[str, Any]) -> set[str]:
    """Span ids whose detail carries THIS row's query/output — exactly the spans
    the two extractors read from for this eval span: the root/trace span
    (``_structured_io``), the eval span itself, and its judge span(s) scoped the
    same way ``_judge_io`` scopes them (this eval span's children, or all judge
    spans only as a fallback when it has none).

    Used to tell a hollow row caused by a *failed detail fetch on one of these
    spans* apart from a genuine shape gap. A 429 on the root span's ``get_span``
    hollows the row (``_structured_io`` sees only the light span, falls through)
    while the eval span's own fetch succeeded — so keying the classification off
    the eval span id alone misfiles it as ``empty_extraction`` and sends the
    operator chasing an extractor bug that is not there. Intersecting this set
    with ``downgraded_spans`` closes that gap.

    The judge scoping goes through ``_judge_spans``, the same helper ``_judge_io``
    reads with: a trace with two evaluator calls has two judge spans, and
    borrowing the *other* eval span's 429'd judge span into this set would misfile
    this row as ``detail_fetch`` (and, because the debug sample is only captured
    on ``empty_extraction``, leave the real shape gap invisible). Only spans
    ``_judge_io`` would actually read for this eval span count.
    """
    ids: set[str] = set()
    esid = _span_id(eval_span)
    if esid:
        ids.add(esid)

    for s in _judge_spans(spans, eval_span):
        sid = _span_id(s)
        if sid:
            ids.add(sid)

    # Root/trace span — where _structured_io reads the row's structured content.
    for s in spans:
        if not isinstance(s, dict):
            continue
        is_root = _is_root_span(s)
        if is_root:
            sid = _span_id(s)
            if sid:
                ids.add(sid)
    return ids


def _classify_degrade(
    span_id: str | None,
    query: str,
    output_val: str,
    spans: list[dict[str, Any]],
    eval_span: dict[str, Any],
    downgraded_spans: set[str],
    messages: Any = None,
) -> str | None:
    """Classify why a row is degraded, or None if it is a clean datapoint.

    Two distinct hollow modes, kept apart so the guard points the operator at the
    right remedy (they don't collapse):

    - ``detail_fetch`` — a span whose detail carries this row's content failed to
      fetch (auth / rate-limit). True when the eval span itself downgraded, or
      when any content-source span (root + this eval span's judge spans, per
      ``_content_source_span_ids``) is in ``downgraded_spans``.
    - ``empty_extraction`` — every source span fetched fine but no content
      (query / output / messages) came out: a genuine unrecognised-shape gap.

    Extracted from the fetch loop so the decision is unit-testable at the call
    site, not just via the helper set.
    """
    # A row that actually carries content is a clean datapoint no matter which
    # spans' detail fetches failed: the eval span may have 429'd while query/
    # output came through fine from the root span. Gate degradation on genuinely
    # empty content so a usable row is never dropped — stability.py skips degraded
    # rows with an "empty output" reason that would otherwise be false. `messages`
    # counts as content: a conversation-only evaluator (judging on {{messages}})
    # has empty query/output but a populated conversation, and dropping it would be
    # the same silent data loss the query/output gate prevents. `reference` does
    # NOT count — it is ground-truth metadata, not content under evaluation.
    if query or output_val or messages:
        return None
    if span_id in downgraded_spans:
        return 'detail_fetch'
    lost_source = _content_source_span_ids(spans, eval_span) & downgraded_spans
    return 'detail_fetch' if lost_source else 'empty_extraction'


def _structured_io(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer the clean, structured content orq records on the trace-level span.

    An orq evaluator run records the content under evaluation on the root span
    (``type == 'trace'`` / ``attributes.type == 'workflow_run'``) as
    ``attributes.gen_ai.input = {input, output, query, reference, messages}`` —
    ``output`` is exactly the content the judge scored and ``messages`` the prior
    conversation turns (kept for {{messages}} templates). This is robust across judge API
    shapes: the Responses API nests text under ``messages[].parts[].content``,
    which the per-message ``gen_ai.input.messages`` reader in ``_judge_io`` cannot
    see (it yields empty rows → the hollow guard aborts). ``query`` falls back to
    ``input`` since ``make_replacements`` collapses both leaves onto the row's
    ``query``. Returns None when no root span carries a non-empty structured input
    so the caller falls back to the template-stencil recovery.
    """
    for s in spans:
        if not isinstance(s, dict) or not _is_root_span(s):
            continue
        gi = (((s.get('attributes') or {}).get('gen_ai') or {}).get('input'))
        if not isinstance(gi, dict):
            continue
        output = str(gi.get('output') or '')
        query = str(gi.get('query') or gi.get('input') or '')
        reference = str(gi.get('reference') or '')
        # The same root span also carries the conversation history under
        # `messages` (list of {role, content}); an evaluator whose template has a
        # {{messages}}/{{history}} variable needs it, or make_replacements renders
        # it blank and the stability run re-judges with no conversation.
        messages = gi.get('messages')
        # Gate on the content-under-evaluation. `reference` is ground-truth
        # metadata (and a boolean coerces to a truthy 'True'), so a root carrying
        # only `reference` is effectively hollow — returning it here would win over
        # the judge-span fallback and yield an empty-output row. `messages` DOES
        # count: a conversation-only evaluator has no query/output at all, and
        # requiring one would leave that row to the stencil path (which cannot
        # recover a conversation) and then to the hollow guard. The caller still
        # runs the judge-span path when query/output are both empty, so a normal
        # evaluator whose root records only the conversation keeps its output.
        if output or query or messages:
            return {'query': query, 'output': output, 'reference': reference, 'messages': messages}
    return None


_VAR_TOKEN = re.compile(r'{{\s*([\w.]+)\s*}}')


def _recover_variables(template: str, rendered: str) -> dict[str, str]:
    """Recover each ``{{var}}`` value from a fully-rendered judge prompt.

    The production judge span stores the prompt *after* substitution, so the raw
    text embeds the content under evaluation. Storing that raw text as the
    datapoint and then re-rendering the template around it (step 4) double-nests
    the prompt inside itself. We reverse the substitution using the evaluator
    template as a stencil: this is portable across tag conventions (``<output>``,
    none, etc.) because it keys off the template's *own* literal framing, not a
    hard-coded delimiter. Exact for a single variable; for several we split on the
    literal inter-token segments. Returns ``{}`` when the framing does not line up
    so the caller can fall back to the raw rendered text.
    """
    m_single = _VAR_TOKEN.search(template)
    if m_single is None:
        return {}
    tokens = _VAR_TOKEN.findall(template)
    if len(tokens) == 1:
        prefix, suffix = template[: m_single.start()], template[m_single.end() :]
        if not (rendered.startswith(prefix) and rendered.endswith(suffix)):
            return {}
        return {tokens[0]: rendered[len(prefix) : len(rendered) - len(suffix)]}
    # Multiple variables: build a stencil regex (literals escaped, tokens capture).
    parts = _VAR_TOKEN.split(template)  # [lit, name, lit, name, ..., lit]
    pattern = ''.join(
        re.escape(part) if i % 2 == 0 else '(.*?)' for i, part in enumerate(parts)
    )
    match = re.fullmatch(pattern, rendered, re.DOTALL)
    if match is None:
        return {}
    return dict(zip(tokens, match.groups()))


def _assign_io(recovered: dict[str, str]) -> dict[str, Any]:
    """Map recovered ``{{var}}`` values onto the row's fields.

    Uses ``lib.content.field_for_variable`` — literally the table
    ``lib.judge.make_replacements`` renders back with, so recovery and rendering
    cannot disagree. That matters for ``reference``: a template like
    ``Compare {{log.output}} with {{log.expected_output}}`` recovers both, and a
    mapper that knew only query/output/messages would drop the expected value on
    the floor and re-judge against a blank reference.
    """
    fields: dict[str, Any] = {'query': '', 'output': '', 'reference': '', 'messages': None}
    for var, val in recovered.items():
        field = field_for_variable(var)
        if field is not None:
            fields[field] = val
    return fields


def _extract_io(
    spans: list[dict[str, Any]], eval_span: dict[str, Any], template: str
) -> dict[str, Any]:
    """The row's content under evaluation: ``{query, output, reference, messages}``.

    THE extraction precedence, in one place so the scan loop and the tests
    exercise the same ordering (a test that re-implements it passes even when
    ``_fetch`` regresses to writing empty rows):

    1. **Structured root span** — the clean ``gen_ai.input`` orq records on the
       trace-level span. Robust across judge API shapes, so it wins whenever it
       carries query/output.
    2. **Template-stencil recovery off the judge span** — the judge span stores
       its prompt *post*-substitution, so we reverse the substitution using the
       evaluator template as a stencil. Storing the rendered prompt instead would
       make step 4 re-nest the whole judge prompt inside itself.
    3. **Raw rendered prompt** — only when the stencil doesn't line up; logged.

    A root that carries *only* a conversation (no query/output) does not short
    circuit 2: its `messages`/`reference` ride along with whatever the judge span
    yields, so a conversation-only evaluator keeps its conversation and a normal
    one keeps its output.
    """
    structured = _structured_io(spans) or {}
    if structured.get('query') or structured.get('output'):
        return {
            'query': structured['query'],
            'output': structured['output'],
            'reference': structured['reference'],
            'messages': structured['messages'],
        }

    rendered, judge_messages = _judge_io(spans, eval_span)
    # An empty rendered prompt has nothing to reverse: a single-variable stencil
    # matches '' with empty prefix/suffix and would "recover" the variable as ''.
    recovered = _recover_variables(template, rendered) if rendered else {}
    if recovered:
        io = _assign_io(recovered)
    else:
        if rendered:
            logger.warning(
                f'⚠ could not recover template variables for span {_span_id(eval_span)}; '
                'storing raw rendered judge input'
            )
        io = {'query': '', 'output': rendered, 'reference': '', 'messages': judge_messages}
    # The structured root is the better source for the fields the stencil can't
    # produce: a conversation is not recoverable from a rendered prompt, and
    # `reference` is ground truth the judge span never echoes back.
    return {
        'query': io['query'],
        'output': io['output'],
        'reference': io['reference'] or structured.get('reference', ''),
        'messages': io['messages'] or structured.get('messages'),
    }


def _judge_model(spans: list[dict[str, Any]], eval_span: dict[str, Any]) -> str:
    """Return the model slug the judge actually ran on for this datapoint.

    The evaluator config only stores an opaque model id (a workspace registry
    UUID), which neither names the model nor survives in the active /v2/models
    catalog once that model is deprecated. The one ground-truth source is the
    judge's own LLM call: a ``span.chat_completion`` descendant of the evaluator
    span whose ``attributes.gen_ai.request.model`` carries the real id (e.g.
    ``anthropic.claude-3-5-sonnet-20241022-v2:0``). Because it is read per
    datapoint, an evaluator whose judge model changed over time is reported
    honestly rather than collapsed to one config value. Shares ``_judge_spans``
    with ``_judge_io`` so the model is read off the same span as the prompt,
    covering both Chat Completions and Responses API shapes.
    """
    # As in _judge_io, the newer schema keeps the LLM call on the eval span
    # itself; also accept gen_ai.response.model as a fallback to request.model.
    for s in [*_judge_spans(spans, eval_span), eval_span]:
        gen_ai = ((s.get('attributes') or {}).get('gen_ai') or {})
        model = (gen_ai.get('request') or {}).get('model') or (gen_ai.get('response') or {}).get('model')
        if model:
            return str(model)
    return ''


def _epoch_ms(iso: str | None) -> int | None:
    """Parse an ISO-8601 span timestamp to epoch-ms (None if unparseable)."""
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _in_window(iso: str | None, start: int | None, end: int | None) -> bool:
    """Keep a trace whose start_time falls inside the configured epoch-ms window."""
    ms = _epoch_ms(iso)
    if ms is None:
        return True
    if start and ms < start:
        return False
    return not (end and ms > end)


async def _fetch(
    evaluator_id: str, evaluator_key: str, cfg: dict[str, Any], template: str, force: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    limit = int(cfg.get('trace_limit', 200))
    start = int(cfg.get('trace_start_date', 0)) or None
    end = int(cfg.get('trace_end_date', 0)) or None
    filter_echo = {
        'match': 'attributes.orq.evaluator.id (client-side)',
        'evaluator_id': evaluator_id,
        'evaluator_key': evaluator_key,
        'limit': limit,
        'start_date': start,
        'end_date': end,
    }

    rows: list[dict[str, Any]] = []
    # Span ids whose per-span detail fetch failed (get_span -> None): the row
    # falls back to the light list-view span, which lacks the judge prompt and
    # model. Tracked so a run-wide auth/rate-limit failure can't hollow every
    # datapoint behind a green pipeline (logging alone is too easy to miss).
    downgraded_spans: set[str] = set()
    # The raw spans of the first trace that actually produces a hollow
    # (empty_extraction) row, kept for the hollow-abort diagnostic dump so the
    # *offending* shape is visible at a glance instead of needing a manual probe.
    # Captured at classification time, not on the first matching trace: on a run
    # where most traces parse cleanly and a minority hit an unrecognised shape,
    # the first matching trace is a healthy one and would misrepresent the dump.
    debug_sample: list[dict[str, Any]] = []
    async with OrqClient() as client:
        raw_traces = await client.query_traces(limit=limit)
        traces = raw_traces
        if start or end:
            traces = [t for t in raw_traces if _in_window(t.get('start_time'), start, end)]
            # The window is filtered client-side over the newest `limit` traces,
            # NOT pushed to the server. If we hit the cap and the oldest trace we
            # saw is still newer than the window start, older in-window traces
            # exist beyond the scan depth and were never fetched — say so loudly
            # rather than silently returning a partial window.
            if start and len(raw_traces) >= limit:
                oldest = min(
                    (ms for t in raw_traces if (ms := _epoch_ms(t.get('start_time'))) is not None),
                    default=None,
                )
                if oldest is not None and oldest > start:
                    logger.warning(
                        f'⚠ Scan hit the {limit}-trace cap without reaching the window start; '
                        f'traces older than epoch-ms {oldest} were not fetched. The date window '
                        f'may be truncated — raise --trace_limit to cover the full window.'
                    )
        logger.info(f'v3oql returned {len(traces)} traces to scan')
        if not traces:
            return [], filter_echo, None

        sem = asyncio.Semaphore(int(cfg.get('max_concurrency', 8)))

        async def _scan(trace: dict[str, Any]) -> None:
            trace_id = trace.get('trace_id') or trace.get('id') or trace.get('_id')
            if not trace_id:
                return
            async with sem:
                try:
                    spans = await client.get_trace_spans(trace_id)
                except Exception:  # noqa: BLE001
                    logger.exception(f'✗ v3spans failed for trace {trace_id}')
                    return
                # Cheap gate on the light list view before paying for full spans.
                if not any(_evaluation_matches(s, evaluator_id, evaluator_key) for s in spans):
                    return
                full: list[dict[str, Any]] = []
                for s in spans:
                    sid = _span_id(s)
                    detail = await client.get_span(trace_id, sid) if sid else None
                    if sid and detail is None:
                        downgraded_spans.add(sid)
                    full.append(detail or s)

            for span in full:
                matches = _evaluation_matches(span, evaluator_id, evaluator_key)
                if not matches:
                    continue
                ev = matches[0]
                span_id = _span_id(span)
                io = _extract_io(full, span, template)
                query, output_val = io['query'], io['output']
                reference, msgs = io['reference'], io['messages']
                # Two distinct hollow modes, tracked separately (see
                # _classify_degrade) so the guard can tell a span-detail fetch
                # failure (auth/rate-limit) apart from an unrecognised span shape.
                degrade_reason = _classify_degrade(span_id, query, output_val, full, span, downgraded_spans, msgs)
                if degrade_reason == 'empty_extraction' and not debug_sample:
                    # Capture the trace that genuinely hollowed on shape (not the
                    # first matching trace, which may parse cleanly).
                    debug_sample.append({'trace_id': trace_id, 'spans': full})
                rows.append(
                    {
                        'trace_id': trace_id,
                        'span_id': span_id,
                        'evaluator_id': ev['evaluator_id'],
                        'evaluator_key': ev['evaluator_key'],
                        'query': query,
                        'output': output_val,
                        'reference': reference,
                        'messages': msgs,
                        'judge_value': ev['value'],
                        'judge_explanation': ev['explanation'],
                        'judge_model': _judge_model(full, span),
                        # A hollow row can't be re-judged faithfully, so it must not
                        # pass as a clean datapoint behind a green pipeline.
                        'degraded': degrade_reason is not None,
                        'degrade_reason': degrade_reason,
                    }
                )

        await asyncio.gather(*(_scan(t) for t in traces))

    n_detail = sum(1 for r in rows if r.get('degrade_reason') == 'detail_fetch')
    n_empty = sum(1 for r in rows if r.get('degrade_reason') == 'empty_extraction')
    filter_echo['n_rows'] = len(rows)
    filter_echo['n_degraded'] = n_detail + n_empty
    filter_echo['n_detail_fetch'] = n_detail
    filter_echo['n_empty_extraction'] = n_empty
    return rows, filter_echo, (debug_sample[0] if debug_sample else None)


def _shape(obj: Any, depth: int = 0, maxdepth: int = 6) -> Any:
    """A structural view of a JSON value: dict keys kept, lists shown as length +
    first-element shape, strings truncated. Keeps a hollow_debug.json small while
    still revealing WHERE the content lives."""
    if depth > maxdepth:
        return '…'
    if isinstance(obj, dict):
        return {k: _shape(v, depth + 1, maxdepth) for k, v in obj.items()}
    if isinstance(obj, list):
        if not obj:
            return []
        return [f'<list len={len(obj)}>', _shape(obj[0], depth + 1, maxdepth)]
    if isinstance(obj, str):
        return obj if len(obj) <= 160 else obj[:160] + f'…(+{len(obj) - 160})'
    return obj


def _hollow_debug(sample: dict[str, Any]) -> dict[str, Any]:
    """Build the hollow-abort diagnostic: one matching trace's span inventory and
    the shape of each span's gen_ai.input/output, so a shape gap is obvious."""
    spans = sample.get('spans') or []

    def _gen_ai(s: dict[str, Any], field: str) -> Any:
        return ((s.get('attributes') or {}).get('gen_ai') or {}).get(field)

    return {
        'trace_id': sample.get('trace_id'),
        'note': (
            'Extraction produced empty rows for this evaluator. The scanner reads the '
            'content-under-evaluation from gen_ai.input.messages on the judge span '
            f'(one of {sorted(_JUDGE_SPAN_TYPES)}) and from the root trace span '
            "gen_ai.input.{output,query}. Compare against where the text actually sits below."
        ),
        # `parent` is the NORMALISED link (_parent_of), with the raw fields beside
        # it: on a Responses-API trace `parent_span_id` is absent and the real
        # link sits on `parent_id`, so printing only the raw field makes the tree
        # look unparented in the very dump meant to explain the shape.
        'span_inventory': [
            {
                'type': s.get('type'),
                'span_id': _span_id(s),
                'parent': _parent_of(s),
                'parent_span_id': s.get('parent_span_id'),
                'parent_id': s.get('parent_id'),
            }
            for s in spans
        ],
        'spans': [
            {
                'type': s.get('type'),
                'span_id': _span_id(s),
                'gen_ai.input': _shape(_gen_ai(s, 'input')),
                'gen_ai.output': _shape(_gen_ai(s, 'output')),
            }
            for s in spans
        ],
    }


def _should_abort_hollow(
    n_detail: int, n_empty: int, n_rows: int, abort_ratio: float, force: bool
) -> bool:
    """Whether the hollow ratio is high enough to abort the run.

    Single source of truth for the abort threshold, shared by ``main`` (which
    dumps the offending span shape first) and ``_guard_hollow`` (which raises).
    They previously computed this separately and were free to drift.

    ``force`` discounts only the ``detail_fetch`` rows. It is documented as the
    auth/rate-limit override — persisting light-span rows the operator knows are
    a transient blip — and the ``empty_extraction`` message explicitly tells them
    NOT to force a shape gap, since forcing writes empty rows that then read as
    perfectly stable datapoints. Letting it suppress that abort too made the flag
    contradict its own error text.
    """
    n_degraded = n_empty if force else n_detail + n_empty
    if not n_rows or not n_degraded:
        return False
    return n_degraded / n_rows > abort_ratio


def _guard_hollow(
    n_detail: int,
    n_empty: int,
    n_rows: int,
    abort_ratio: float,
    force: bool,
    debug_path: str | None = None,
) -> None:
    """Abort when too many datapoints are hollow — with a diagnosis, not a guess.

    Two failure modes, told apart by ``degrade_reason``:
    - ``detail_fetch``: the span-detail GET did not return usable content, so the
      row fell back to the light span. ``orq_client.get_span`` returns None for
      any HTTP error, a non-JSON body or a non-dict payload, so this covers a 5xx
      or a malformed response as well as the common 401/403/429 — the message
      names auth and rate limits first because they dominate, not exclusively.
      Remedy: check the status, fix, retry.
    - ``empty_extraction``: the span was fetched fine but no extraction path
      found content — the scanner doesn't understand this evaluator's span shape.
      Remedy: fix the extractor; ``--force`` does not override this one, because
      forcing it writes empty rows that then read as perfectly stable datapoints.

    Reporting the breakdown (and pointing at ``hollow_debug.json`` for the shape
    case) is what turns a green-pipeline mystery into an obvious fix.
    """
    n_degraded = n_detail + n_empty
    if not n_rows or not n_degraded:
        return
    ratio = n_degraded / n_rows
    if not _should_abort_hollow(n_detail, n_empty, n_rows, abort_ratio, force):
        logger.warning(
            f'⚠ {n_degraded}/{n_rows} datapoints degraded '
            f'(span-detail failures: {n_detail}, empty extraction: {n_empty})'
        )
        return
    dump = f' One trace\'s span shape was dumped to {debug_path}.' if debug_path else ''
    if n_detail == 0:
        # Fetch succeeded for every row; extraction still found nothing → shape gap.
        raise SystemExit(
            f'✗ {n_empty}/{n_rows} datapoints ({ratio:.0%}) are hollow, but the span-detail fetch '
            f'SUCCEEDED for all of them (0 auth/rate-limit failures). This is an extraction/shape '
            f'gap, not an auth problem: the judge runs as a span type or content shape this scanner '
            f'does not parse (e.g. a Responses-API span.responses with text under parts[].content).'
            f'{dump} Fix the extractor — --force does NOT override this abort, because forcing it '
            f'writes empty rows that then read as perfectly stable datapoints.'
        )
    if n_empty == 0:
        raise SystemExit(
            f'✗ {n_detail}/{n_rows} datapoints ({ratio:.0%}) lost their span detail to span-detail '
            f'endpoint failures (usually a run-wide 401/403/429; any HTTP error or unparseable body '
            f'lands here). Check the endpoint status and ORQ_API_KEY scope and rate limits, then '
            f'retry. Pass --force to persist the light-span rows anyway.'
        )
    raise SystemExit(
        f'✗ {n_degraded}/{n_rows} datapoints ({ratio:.0%}) are hollow: {n_detail} from span-detail '
        f'failures (usually auth/rate-limit — check ORQ_API_KEY scope) and {n_empty} from empty '
        f'extraction (a span-shape gap the scanner does not parse).{dump} Address the dominant '
        f'cause; --force discounts only the span-detail half, never the shape gap.'
    )


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    trace_limit: int | None = 200,
    force: bool = False,
) -> str:
    """Fetch traces for the evaluator recorded in the run directory.

    Args:
        run_dir: Run directory from step 1. Defaults to the most recent one.
        config: TOML config path.
        trace_limit: Scan depth (most-recent traces to scan client-side).
            Defaults to 200 and overrides ``trace_limit`` in config.toml so the
            scan window can be widened per-run without editing config. Pass a
            larger value when the evaluator is sparse or its traffic is aged
            (e.g. ``--trace_limit 2000``).
        force: Persist the datapoints even when a large fraction lost their
            judge-span detail (hollow rows). Off by default so a run-wide
            auth/rate-limit failure aborts instead of writing garbage. It covers
            the span-detail half ONLY — an extraction/shape gap still aborts,
            since forcing that writes empty rows that look perfectly stable.
    """
    cfg = runner.load_config(config)
    if trace_limit is not None:
        cfg['trace_limit'] = int(trace_limit)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run fetch_evaluator.py first.')

    evaluator = runner.read_json(out_dir / 'evaluator.json')
    evaluator_id = evaluator['id']
    evaluator_key = evaluator.get('key', '')

    rows, filter_echo, debug_sample = asyncio.run(
        _fetch(evaluator_id, evaluator_key, cfg, evaluator.get('prompt', ''), force=force)
    )

    if not rows:
        raise SystemExit(
            'No candidate datapoints found.\n'
            f'  scan: {filter_echo}\n'
            'Matching is client-side (v3oql has no evaluator filter): raise the '
            'scan depth with `--trace_limit <N>` (default 300) — the evaluator '
            'may be sparse or its traffic older than the scanned window — and/or '
            'widen trace_start_date / trace_end_date (epoch-ms) in config.toml. '
            'Confirm the evaluator actually has traces in the window.'
        )

    # Hollow guard: abort (with a diagnosis, not a guess) when too many rows are
    # unusable. On a shape-gap abort, dump one trace's span shape to the run dir
    # first so the message can point at it and the fix is obvious.
    n_detail = int(filter_echo.get('n_detail_fetch', 0))
    n_empty = int(filter_echo.get('n_empty_extraction', 0))
    abort_ratio = float(cfg.get('hollow_abort_ratio', 0.2))
    debug_path: str | None = None
    if _should_abort_hollow(n_detail, n_empty, len(rows), abort_ratio, force) and debug_sample:
        debug_path = str(out_dir / 'hollow_debug.json')
        runner.write_json(out_dir / 'hollow_debug.json', _hollow_debug(debug_sample))
    _guard_hollow(n_detail, n_empty, len(rows), abort_ratio, force, debug_path)

    runner.write_jsonl(out_dir / 'traces.jsonl', rows)
    logger.info(f'✓ Wrote {len(rows)} datapoints to {out_dir / "traces.jsonl"}')

    model = _resolve_judge_model(out_dir, evaluator, rows)

    # Now that the judge model and datapoint count are known, embed them in the
    # run dir name so the folder is self-describing (`<key>_<ts>_<model>_<N>dp`).
    out_dir = runner.apply_run_meta(out_dir, model or 'model-unknown', len(rows))
    logger.info(f'✓ Run dir: {out_dir}')

    print(out_dir)
    return str(out_dir)


def _resolve_judge_model(out_dir: Any, evaluator: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
    """Resolve the evaluator's judge model from the traces and pin it.

    `evaluator.json` arrives from step 1 with only the opaque config model id
    (`judge_model_id`). Each row now carries the model its judge actually ran on
    (``_judge_model``); the most common one is the canonical judge model the
    stability run reconstructs with. The full distribution is written too, so a
    judge whose model changed across the scanned window is visible rather than
    silently collapsed.
    """
    observed = Counter(r['judge_model'] for r in rows if r.get('judge_model'))
    if not observed:
        # Traces didn't record a model. That's fine IF step 1 already resolved a
        # routable slug (from the config id or --judge_model) — keep it. Only the
        # opaque config UUID (== judge_model_id) is unroutable.
        pinned = evaluator.get('judge_model')
        if pinned and pinned != evaluator.get('judge_model_id'):
            logger.info(f'✓ No model on trace spans; using the slug resolved in step 1: {pinned}')
            return pinned
        logger.warning(
            f'⚠ No judge model on any trace span and none resolved in step 1 (config id '
            f'{evaluator.get("judge_model_id") or evaluator.get("judge_model")!r}). The stability '
            'run cannot route an opaque id — rerun fetch_evaluator.py with --judge_model <slug>.'
        )
        return None

    resolved, _ = observed.most_common(1)[0]
    evaluator['judge_model'] = resolved
    evaluator['judge_models_observed'] = dict(observed)
    runner.write_json(out_dir / 'evaluator.json', evaluator)
    logger.info(f'✓ Resolved judge model from traces: {resolved}')
    if len(observed) > 1:
        logger.warning(
            f'⚠ Datapoints were judged by >1 model: {dict(observed)}. Using the '
            f'most common ({resolved}) as the judge model; a mixed-model history '
            'can inflate the apparent flip-rate.'
        )
    return resolved


if __name__ == '__main__':
    fire.Fire(main)
