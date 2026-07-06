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


def _judge_io(spans: list[dict[str, Any]], eval_span: dict[str, Any]) -> tuple[str, Any]:
    """Return (rendered_input, messages) the judge actually saw.

    The content under evaluation is rendered into the judge's own
    ``span.chat_completion`` call (its ``gen_ai.input.messages``). We keep those
    messages verbatim and do NOT parse delimiters out of the prompt: evaluators
    wrap their template variables differently (some use ``<output>`` tags, some
    don't), so tag-stripping is not portable. The judge chat span is the
    evaluator span's child (``parent_span_id``); we fall back to any chat span in
    the trace.
    """
    esid = eval_span.get('span_id') or eval_span.get('_id')
    chats = [s for s in spans if isinstance(s, dict) and s.get('type') == 'span.chat_completion']
    chosen = [s for s in chats if s.get('parent_span_id') == esid] or chats
    for s in chosen:
        msgs = (((s.get('attributes') or {}).get('gen_ai') or {}).get('input') or {}).get('messages')
        if msgs:
            rendered = '\n\n'.join(str(m.get('content', '')) for m in msgs)
            return rendered, msgs
    return '', None


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
    chat-span selection (children of the eval span, else any chat in the trace).
    """
    esid = eval_span.get('span_id') or eval_span.get('_id')
    chats = [s for s in spans if isinstance(s, dict) and s.get('type') == 'span.chat_completion']
    chosen = [s for s in chats if s.get('parent_span_id') == esid] or chats
    for s in chosen:
        req = (((s.get('attributes') or {}).get('gen_ai') or {}).get('request') or {})
        model = req.get('model')
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
            return [], filter_echo

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
                rows.append(
                    {
                        'trace_id': trace_id,
                        'span_id': span_id,
                        'evaluator_id': ev['evaluator_id'],
                        'evaluator_key': ev['evaluator_key'],
                        'query': query,
                        'output': output_val,
                        'messages': msgs,
                        'judge_value': ev['value'],
                        'judge_explanation': ev['explanation'],
                        'judge_model': _judge_model(full, span),
                        # True when this row's judge span lost its detail fetch and
                        # fell back to the light span (empty query/output/model).
                        'degraded': span_id in downgraded_spans,
                    }
                )

        await asyncio.gather(*(_scan(t) for t in traces))

    n_degraded = sum(1 for r in rows if r.get('degraded'))
    filter_echo['n_rows'] = len(rows)
    filter_echo['n_degraded'] = n_degraded
    _guard_hollow(n_degraded, len(rows), float(cfg.get('hollow_abort_ratio', 0.2)), force)
    return rows, filter_echo


def _guard_hollow(n_degraded: int, n_rows: int, abort_ratio: float, force: bool) -> None:
    """Abort when too many datapoints lost their judge-span detail.

    A run-wide 401/403/429 on the span-detail endpoint degrades every row to an
    empty query/output/judge_model. Logging alone is too easy to miss in a green
    pipeline, so cross a ratio → hard stop (unless --force).
    """
    if not n_rows or not n_degraded:
        return
    ratio = n_degraded / n_rows
    if ratio > abort_ratio and not force:
        raise SystemExit(
            f'✗ {n_degraded}/{n_rows} datapoints ({ratio:.0%}) lost their judge-span detail and are '
            f'hollow (empty query/output/judge_model). This usually means a run-wide auth (401/403) '
            f'or rate-limit (429) failure on the span-detail endpoint — check ORQ_API_KEY scope and '
            f'retry. Pass --force to persist the partial set anyway.'
        )
    logger.warning(f'⚠ {n_degraded}/{n_rows} datapoints degraded (used light span, no judge detail)')


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

    rows, filter_echo = asyncio.run(
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
        logger.warning(
            f'⚠ No judge model found on any trace span; keeping config id '
            f'{evaluator.get("judge_model_id") or evaluator.get("judge_model")!r} '
            'as judge_model. The stability run cannot route an opaque id — set '
            'evaluator.json["judge_model"] to a real model slug before step 4.'
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
