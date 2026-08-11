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


def _message_text(m: Any) -> str:
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


def _judge_io(spans: list[dict[str, Any]], eval_span: dict[str, Any]) -> tuple[str, Any]:
    """Return (rendered_input, messages) the judge actually saw.

    The content under evaluation is rendered into the judge's own LLM call (a
    ``span.chat_completion`` or ``span.responses`` span, its ``gen_ai.input.
    messages``). We keep those messages verbatim and do NOT parse delimiters out
    of the prompt: evaluators wrap their template variables differently (some use
    ``<output>`` tags, some don't), so tag-stripping is not portable. The judge
    span is the evaluator span's child (``parent_span_id``); we fall back to any
    judge span in the trace, then to the eval span's own gen_ai input.
    """
    esid = eval_span.get('span_id') or eval_span.get('_id')
    chats = [s for s in spans if isinstance(s, dict) and s.get('type') in _JUDGE_SPAN_TYPES]
    chosen = [s for s in chats if s.get('parent_span_id') == esid] or chats
    # Newer orq schema records the judge's LLM call ON the evaluator span itself
    # (no separate child span), so fall back to the eval span's own gen_ai input.
    # Without this, evaluators that don't emit a child judge span yield empty
    # query/output — hollow datapoints behind a green pipeline.
    for s in [*chosen, eval_span]:
        msgs = (((s.get('attributes') or {}).get('gen_ai') or {}).get('input') or {}).get('messages')
        if msgs:
            rendered = '\n\n'.join(_message_text(m) for m in msgs)
            if rendered.strip():
                return rendered, msgs
    return '', None


def _content_source_span_ids(spans: list[dict[str, Any]], eval_span: dict[str, Any]) -> set[str]:
    """Span ids whose detail carries a row's query/output — the spans the two
    extractors actually read from: the root/trace span (``_structured_io``), every
    judge span and the eval span itself (``_judge_io``'s child-or-any + eval-span
    fallback).

    Used to tell a hollow row caused by a *failed detail fetch on one of these
    spans* apart from a genuine shape gap. A 429 on the root span's ``get_span``
    hollows the row (``_structured_io`` sees only the light span, falls through)
    while the eval span's own fetch succeeded — so keying the classification off
    the eval span id alone misfiles it as ``empty_extraction`` and sends the
    operator chasing an extractor bug that is not there. Intersecting this set
    with ``downgraded_spans`` closes that gap.
    """
    ids: set[str] = set()
    esid = eval_span.get('span_id') or eval_span.get('_id')
    if esid:
        ids.add(esid)
    for s in spans:
        if not isinstance(s, dict):
            continue
        sid = s.get('span_id') or s.get('_id')
        if not sid:
            continue
        is_root = s.get('type') == 'trace' or ((s.get('attributes') or {}).get('type')) == 'workflow_run'
        if is_root or s.get('type') in _JUDGE_SPAN_TYPES:
            ids.add(sid)
    return ids


def _structured_io(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer the clean, structured content orq records on the trace-level span.

    An orq evaluator run records the content under evaluation on the root span
    (``type == 'trace'`` / ``attributes.type == 'workflow_run'``) as
    ``attributes.gen_ai.input = {input, output, query, reference}`` — ``output``
    is exactly the content the judge scored. This is robust across judge API
    shapes: the Responses API nests text under ``messages[].parts[].content``,
    which the per-message ``gen_ai.input.messages`` reader in ``_judge_io`` cannot
    see (it yields empty rows → the hollow guard aborts). ``query`` falls back to
    ``input`` since ``make_replacements`` collapses both leaves onto the row's
    ``query``. Returns None when no root span carries a non-empty structured input
    so the caller falls back to the template-stencil recovery.
    """
    for s in spans:
        if not isinstance(s, dict):
            continue
        is_root = s.get('type') == 'trace' or ((s.get('attributes') or {}).get('type')) == 'workflow_run'
        if not is_root:
            continue
        gi = (((s.get('attributes') or {}).get('gen_ai') or {}).get('input'))
        if not isinstance(gi, dict):
            continue
        output = str(gi.get('output') or '')
        query = str(gi.get('query') or gi.get('input') or '')
        reference = str(gi.get('reference') or '')
        # Gate on the content-under-evaluation only. `reference` is ground-truth
        # metadata (and a boolean coerces to a truthy 'True'), so a root carrying
        # only `reference` is effectively hollow — returning it here would win over
        # the judge-span fallback and yield an empty-output row.
        if output or query:
            return {'query': query, 'output': output, 'reference': reference, 'messages': None}
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
    """Map recovered ``{{var}}`` values onto the row's query/output/messages fields
    using the same suffix rules as ``lib.judge.make_replacements``."""
    fields: dict[str, Any] = {'query': '', 'output': '', 'messages': None}
    for var, val in recovered.items():
        leaf = var.split('.')[-1].strip().lower()
        if leaf in {'input', 'query', 'prompt'}:
            fields['query'] = val
        elif leaf in {'output', 'response', 'completion', 'answer'}:
            fields['output'] = val
        elif leaf in {'messages', 'history', 'conversation'}:
            fields['messages'] = val
    return fields


def _judge_model(spans: list[dict[str, Any]], eval_span: dict[str, Any]) -> str:
    """Return the model slug the judge actually ran on for this datapoint.

    The evaluator config only stores an opaque model id (a workspace registry
    UUID), which neither names the model nor survives in the active /v2/models
    catalog once that model is deprecated. The one ground-truth source is the
    judge's own LLM call: a ``span.chat_completion`` descendant of the evaluator
    span whose ``attributes.gen_ai.request.model`` carries the real id (e.g.
    ``anthropic.claude-3-5-sonnet-20241022-v2:0``). Because it is read per
    datapoint, an evaluator whose judge model changed over time is reported
    honestly rather than collapsed to one config value. Mirrors ``_judge_io``'s
    judge-span selection (children of the eval span, else any judge span in the
    trace), covering both Chat Completions and Responses API shapes.
    """
    esid = eval_span.get('span_id') or eval_span.get('_id')
    chats = [s for s in spans if isinstance(s, dict) and s.get('type') in _JUDGE_SPAN_TYPES]
    chosen = [s for s in chats if s.get('parent_span_id') == esid] or chats
    # As in _judge_io, the newer schema keeps the LLM call on the eval span
    # itself; also accept gen_ai.response.model as a fallback to request.model.
    for s in [*chosen, eval_span]:
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
                    sid = s.get('span_id') or s.get('_id')
                    detail = await client.get_span(trace_id, sid) if sid else None
                    if sid and detail is None:
                        downgraded_spans.add(sid)
                    full.append(detail or s)

            for span in full:
                matches = _evaluation_matches(span, evaluator_id, evaluator_key)
                if not matches:
                    continue
                ev = matches[0]
                span_id = span.get('span_id') or span.get('_id')
                # Prefer the clean, structured content orq records on the
                # trace-level span (gen_ai.input = {input, output, query,
                # reference}) — robust across judge API shapes. Fall back to the
                # template-stencil recovery from the (post-substitution) judge span
                # when the structured input is absent.
                structured = _structured_io(full)
                if structured is not None:
                    query, output_val, msgs = structured['query'], structured['output'], structured['messages']
                    reference = structured['reference']
                else:
                    rendered, messages = _judge_io(full, span)
                    # The judge span stores the prompt post-substitution. Recover the
                    # original variable values via the template stencil so the row
                    # holds the *content under evaluation*, not the whole rendered
                    # judge prompt (which step 4 would otherwise re-nest inside itself
                    # and the annotation UI would show verbatim).
                    recovered = _recover_variables(template, rendered)
                    if recovered:
                        io = _assign_io(recovered)
                        query, output_val, msgs = io['query'], io['output'], io['messages']
                    else:
                        logger.warning(
                            f'⚠ could not recover template variables for span '
                            f'{span.get("span_id") or span.get("_id")}; storing raw rendered judge input'
                        )
                        query, output_val, msgs = '', rendered, messages
                    reference = ''
                # Two distinct hollow modes, tracked separately so the guard can
                # tell a span-detail fetch failure (auth/rate-limit) apart from an
                # unrecognised span shape (the span was fetched fine but extraction
                # found no content). The remedies differ, so don't collapse them.
                if span_id in downgraded_spans:
                    degrade_reason: str | None = 'detail_fetch'
                elif not query and not output_val:
                    # Hollow. The content is read from the root/judge spans, not
                    # just the eval span, so a failed detail fetch on ONE of those
                    # (e.g. a 429 on the root span while the eval span's own fetch
                    # succeeded) is a fetch failure, not a shape gap. Check the
                    # content-source set against downgraded_spans before blaming
                    # the extractor.
                    lost_source = _content_source_span_ids(full, span) & downgraded_spans
                    degrade_reason = 'detail_fetch' if lost_source else 'empty_extraction'
                    if degrade_reason == 'empty_extraction' and not debug_sample:
                        # Capture the trace that genuinely hollowed on shape (not
                        # the first matching trace, which may parse cleanly).
                        debug_sample.append({'trace_id': trace_id, 'spans': full})
                else:
                    degrade_reason = None
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

    def _sid(s: dict[str, Any]) -> Any:
        return s.get('span_id') or s.get('_id')

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
        'span_inventory': [
            {'type': s.get('type'), 'span_id': _sid(s), 'parent_span_id': s.get('parent_span_id')}
            for s in spans
        ],
        'spans': [
            {
                'type': s.get('type'),
                'span_id': _sid(s),
                'gen_ai.input': _shape(_gen_ai(s, 'input')),
                'gen_ai.output': _shape(_gen_ai(s, 'output')),
            }
            for s in spans
        ],
    }


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
    - ``detail_fetch``: the span-detail GET failed (a run-wide 401/403/429), so
      the row fell back to the light span. Remedy: fix auth/rate-limit, retry.
    - ``empty_extraction``: the span was fetched fine but neither extraction path
      found content — the scanner doesn't understand this evaluator's span shape.
      Remedy: fix the extractor; ``--force`` would only persist empty rows.

    Reporting the breakdown (and pointing at ``hollow_debug.json`` for the shape
    case) is what turns a green-pipeline mystery into an obvious fix.
    """
    n_degraded = n_detail + n_empty
    if not n_rows or not n_degraded:
        return
    ratio = n_degraded / n_rows
    if ratio <= abort_ratio or force:
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
            f'{dump} Fix the extractor — --force would only persist empty rows.'
        )
    if n_empty == 0:
        raise SystemExit(
            f'✗ {n_detail}/{n_rows} datapoints ({ratio:.0%}) lost their span detail to span-detail '
            f'endpoint failures (a run-wide 401/403/429). Check ORQ_API_KEY scope and rate limits, '
            f'then retry. Pass --force to persist the light-span rows anyway.'
        )
    raise SystemExit(
        f'✗ {n_degraded}/{n_rows} datapoints ({ratio:.0%}) are hollow: {n_detail} from span-detail '
        f'failures (auth/rate-limit — check ORQ_API_KEY scope) and {n_empty} from empty extraction '
        f'(a span-shape gap the scanner does not parse).{dump} Address the dominant cause; --force '
        f'persists them as-is.'
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
            auth/rate-limit failure aborts instead of writing garbage.
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
    if not force and (n_detail + n_empty) / len(rows) > abort_ratio and debug_sample:
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
