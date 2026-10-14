# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "truststore>=0.9; sys_platform == 'win32'",
# ]
# ///
"""Window-wide decisions for orq-analyze-traces: one `/decisions` call per trace, over every trace in the window.

The agent reads a sample and names the failure modes; this script measures them over the whole window. It
never writes to the platform: it reads traces (`POST /v3/traces/search`, `GET /v3/traces/{id}/conversation`)
and asks the router's decision models (`POST /v3/router/decisions`), which costs money but changes nothing.

Input routing. A trace whose state fits the short model's limit goes to Jev (`typesafe/jev-latest`, 32k tokens of
state plus longest question). Anything larger goes to the long model (`openai/gpt-6-luna`, 1.05M context) whole,
never chunked or trimmed: chunking with max pooling inflates a mode's rate on long traces, and trimming deflates
it. A Jev call that still overflows (`max_tokens_exceeded`) is retried on the long model. Every row records which
model labelled it, so agreement and rates can be read per route.

Subcommands:
    probe      one tiny call per model: is `/decisions` reachable with this key?
    estimate   enumerate the window, measure a random sample of conversations, project cost per route
    label      label every trace in the window (or an id list) against a questions file; resumable JSONL
    pick       draw Phase 1 reading picks from a selection pass: flagged traces plus a random slice
    agreement  per mode, the decision model against the agent's own labels on traces it read
    rates      per mode, the share of labelled traces the decision model flags, per route

Usage (from the skill directory):
    uv run scripts/decide.py probe
    uv run scripts/decide.py estimate --search-body body.json
    uv run scripts/decide.py label --search-body body.json --questions modes.json --out labels.jsonl
    uv run scripts/decide.py pick --labels selection.jsonl --question failed --flagged 30 --random 50
    uv run scripts/decide.py agreement --labels labels.jsonl --questions modes.json --agent-labels coded.json
    uv run scripts/decide.py rates --labels labels.jsonl --questions modes.json

`--search-body` is the same `traces search` body file the skill already builds (trace-queries.md §1): RFC3339
`from`/`to`, the target in `filters`. `sort` and paging are set here. Requires `ORQ_API_KEY`; `ORQ_API_BASE_URL`
overrides the host (default https://api.orq.ai).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import ssl
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

if sys.platform == 'win32':
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

SHORT_MODEL = 'typesafe/jev-latest'
LONG_MODEL = 'openai/gpt-6-luna'
# Catalogue input prices, USD per million tokens, on 2026-10-14 (`orq models list`). Estimates only: `label`
# reports what each call actually cost from the router's own usage block.
PRICES = {SHORT_MODEL: 0.042, LONG_MODEL: 0.10, 'orq/decider-4b': 0.035}
# Jev: state plus the longest question within 32k tokens. Tokens are estimated at 4 characters each, as evaluatorq
# does. A trace the estimate routes short and Jev still rejects as too long is retried on the long model, so the
# estimate only has to avoid wasted calls, not guarantee a fit: an 85k-character trace estimated at 28k tokens by a
# 3-characters rule measured 19k on Luna's tokenizer and would have gone long for nothing.
SHORT_LIMIT_TOKENS = 28_000
LONG_LIMIT_TOKENS = 1_000_000
CHARS_PER_TOKEN = 4
MAX_ATTEMPTS = 5
SEARCH_PAGE = 100


def base_url() -> str:
    return os.environ.get('ORQ_API_BASE_URL', 'https://api.orq.ai').rstrip('/') or 'https://api.orq.ai'


def tls_verify() -> bool | ssl.SSLContext:
    """Verify TLS everywhere. On Windows use the OS trust store: OpenSSL's file-based CA load aborts some builds."""
    if sys.platform == 'win32':
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return True


def client() -> httpx.AsyncClient:
    key = os.environ.get('ORQ_API_KEY')
    if not key:
        sys.exit('✗ ORQ_API_KEY is not set')
    return httpx.AsyncClient(
        base_url=base_url(), headers={'Authorization': f'Bearer {key}'}, timeout=300, verify=tls_verify()
    )


async def request(http: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One call with retries on 429, 5xx and network errors. Any other status is returned to the caller."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            print(f'⚠ {method} {path}: {type(exc).__name__}, retrying', file=sys.stderr)
            await asyncio.sleep(2**attempt)
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == MAX_ATTEMPTS:
                return response
            try:
                wait = float(response.headers.get('retry-after') or 2**attempt)
            except ValueError:  # an HTTP-date Retry-After
                wait = 2**attempt
            await asyncio.sleep(min(wait, 60))
            continue
        return response
    raise AssertionError('unreachable')


# --- window ------------------------------------------------------------------------------------------------------


async def window_ids(http: httpx.AsyncClient, body_path: Path, max_traces: int) -> list[str]:
    """Every distinct trace id the search body selects, newest first. Search rows repeat a trace once per span."""
    body = json.loads(body_path.read_text(encoding='utf-8-sig'))
    for field in ('from', 'to'):
        if field not in body:
            sys.exit(f'✗ {body_path}: `{field}` is required (RFC3339) in a search body')
    body['sort'] = [{'field': 'end_time', 'order': 'desc'}]
    body['limit'] = SEARCH_PAGE
    ids: dict[str, None] = {}
    token = None
    seen_tokens: set[str] = set()
    while len(ids) < max_traces:
        if token:
            body['page_token'] = token
        response = await request(http, 'POST', '/v3/traces/search', json=body)
        if response.status_code != 200:
            sys.exit(f'✗ traces search: HTTP {response.status_code} {response.text[:300]}')
        payload = response.json()
        for row in payload.get('data') or []:
            if row.get('trace_id'):
                ids.setdefault(row['trace_id'], None)
        token = payload.get('next_page_token') or payload.get('page_token')
        if not token or token in seen_tokens or not payload.get('data'):
            break
        seen_tokens.add(token)
    return list(ids)[:max_traces]


def read_ids(path: Path) -> list[str]:
    return list(
        dict.fromkeys(line.strip() for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip())
    )


async def conversation(http: httpx.AsyncClient, trace_id: str) -> list[dict[str, Any]]:
    response = await request(http, 'GET', f'/v3/traces/{trace_id}/conversation')
    if response.status_code != 200:
        raise RuntimeError(f'conversation HTTP {response.status_code}: {response.text[:200]}')
    return response.json().get('items') or []


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('text'))
    return ''


def build_state(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The decision state: every message, tool call and tool result in order, whole. Reasoning is left out, as in
    evaluatorq's `jev_state`: it is the model's scratchpad, not something the agent did or said."""
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        kind = item.get('type')
        if kind == 'message':
            text = _text(item.get('content'))
            if text:
                entries.append({'index': index, 'role': item.get('role', 'unknown'), 'text': text})
        elif kind == 'function_call':
            entries.append(
                {
                    'index': index,
                    'role': 'assistant',
                    'tool_call': item.get('name'),
                    'call_id': item.get('call_id'),
                    'arguments': item.get('arguments', ''),
                }
            )
        elif kind == 'function_call_output':
            output = item.get('output')
            entries.append(
                {
                    'index': index,
                    'role': 'tool',
                    'call_id': item.get('call_id'),
                    'status': item.get('status'),
                    'output': output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
                }
            )
        elif kind and kind != 'reasoning':
            # Built-in tool calls (web or file search), MCP calls and anything newer: kept as recorded, minus ids.
            entries.append({'index': index, **{k: v for k, v in item.items() if k not in ('id', 'signature')}})
    return {'messages': entries}


def state_tokens(state: dict[str, Any], questions: dict[str, Any]) -> int:
    longest = max((len(json.dumps(q, ensure_ascii=False)) for q in questions.values()), default=0)
    return (len(json.dumps(state, ensure_ascii=False)) + longest) // CHARS_PER_TOKEN


# --- decisions ---------------------------------------------------------------------------------------------------


def load_questions(path: Path) -> tuple[dict[str, Any], dict[str, float]]:
    """`{key: {type, instructions, criteria?, threshold?}}`, type `noul` or `choice`. `threshold` is local (noul
    only) and never sent. `score` questions are refused: nothing here turns a score into a mode verdict."""
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(raw, dict) or not raw:
        sys.exit(f'✗ {path}: expected a non-empty object of questions')
    wire: dict[str, Any] = {}
    thresholds: dict[str, float] = {}
    for key, question in raw.items():
        if question.get('type') not in ('noul', 'choice'):
            sys.exit(f'✗ {path}: question {key!r} needs type noul or choice')
        if question['type'] == 'choice' and not isinstance(question.get('criteria'), dict):
            sys.exit(f'✗ {path}: choice question {key!r} needs a {{label: description}} criteria object')
        thresholds[key] = float(question.get('threshold', 0.5))
        wire[key] = {k: v for k, v in question.items() if k != 'threshold'}
    return wire, thresholds


def questions_sha(questions: dict[str, Any]) -> str:
    """Which questions a row answers. Rows from another version of the questions are never reused or counted."""
    return hashlib.sha256(json.dumps(questions, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


async def decide(http: httpx.AsyncClient, model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
    response = await request(
        http, 'POST', '/v3/router/decisions', json={'model': model, 'state': state, 'questions': questions}
    )
    if response.status_code == 200:
        body = response.json()
        return {'answers': body.get('answers') or {}, 'usage': body.get('usage') or {}}
    overflow = response.status_code == 400 and 'max_tokens_exceeded' in response.text
    return {'error': f'HTTP {response.status_code}: {response.text[:200]}', 'overflow': overflow}


async def label_one(
    http: httpx.AsyncClient, trace_id: str, questions: dict[str, Any], short: str, long: str
) -> dict[str, Any]:
    row: dict[str, Any] = {'trace_id': trace_id, 'questions_sha': questions_sha(questions)}
    try:
        state = build_state(await conversation(http, trace_id))
    except (RuntimeError, httpx.HTTPError, ValueError) as exc:
        return {**row, 'error': f'fetch: {exc}'}
    if not state['messages']:
        return {**row, 'error': 'empty conversation'}
    tokens = state_tokens(state, questions)
    row['estimated_tokens'] = tokens
    if tokens > LONG_LIMIT_TOKENS:
        return {**row, 'error': f'too long for {long} (about {tokens:,} tokens)'}
    model, reason = (short, 'fits') if tokens <= SHORT_LIMIT_TOKENS else (long, 'over_short_limit')
    try:
        result = await decide(http, model, state, questions)
        if result.get('overflow') and model == short:
            model, reason = long, 'short_overflowed'
            result = await decide(http, model, state, questions)
    except (httpx.HTTPError, ValueError) as exc:
        return {**row, 'model': model, 'error': f'decide: {type(exc).__name__}: {exc}'}
    row.update(model=model, route='short' if model == short else 'long', reason=reason)
    if 'error' in result:
        return {**row, 'error': result['error']}
    usage = result['usage']
    return {
        **row,
        'answers': result['answers'],
        'input_tokens': usage.get('input_tokens'),
        'cost': usage.get('total_cost'),
    }


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def done_ids(out: Path, sha: str) -> set[str]:
    """Traces already answered for these questions. An out file written for other questions is refused, so a
    rewritten question is never silently skipped as done: give each questions file its own out file."""
    rows = read_rows(out)
    if {row.get('questions_sha') for row in rows} - {sha}:
        sys.exit(f'✗ {out} holds answers to a different questions file; write this pass to a new --out')
    return {row['trace_id'] for row in rows if 'answers' in row}


def latest_rows(path: Path, sha: str | None = None) -> dict[str, dict[str, Any]]:
    """The last row per trace: a retried trace's success replaces its earlier error. With `sha`, rows answering
    other questions are left out, with a warning."""
    rows: dict[str, dict[str, Any]] = {}
    skipped = 0
    for row in read_rows(path):
        if sha is not None and row.get('questions_sha') != sha:
            skipped += 1
            continue
        if 'answers' in row or row['trace_id'] not in rows:
            rows[row['trace_id']] = row
    if skipped:
        print(f'⚠ {path}: {skipped} rows answer a different questions file and were left out', file=sys.stderr)
    return rows


def verdicts(row: dict[str, Any], questions: dict[str, Any], thresholds: dict[str, float]) -> set[str] | None:
    """The modes a labelled row flags. A choice question's label is the mode (`none` and `pass` flag nothing);
    a noul question flags its own key at or above its threshold. None when an answer is missing or refused."""
    flagged: set[str] = set()
    answers = row.get('answers') or {}
    for key, question in questions.items():
        answer = answers.get(key)
        if not isinstance(answer, dict) or answer.get('type') != question['type']:
            return None
        if question['type'] == 'choice':
            label = answer.get('choice')
            if label is None or label not in question['criteria']:
                return None  # missing or outside the criteria: unreadable, never a clean trace
            if label not in ('none', 'pass'):
                flagged.add(label)
        elif question['type'] == 'noul':
            if answer.get('noul') is None:
                return None
            if answer['noul'] >= thresholds[key]:
                flagged.add(key)
    return flagged


def mode_names(questions: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key, question in questions.items():
        if question['type'] == 'choice':
            names.extend(label for label in question['criteria'] if label not in ('none', 'pass'))
        elif question['type'] == 'noul':
            names.append(key)
    return names


# --- subcommands ---------------------------------------------------------------------------------------------------


PROBE_STATE = {
    'messages': [{'role': 'user', 'text': 'Where is my order?'}, {'role': 'assistant', 'text': 'It ships today.'}]
}
PROBE_QUESTION = {'probe': {'type': 'noul', 'instructions': 'Did the assistant answer the user?'}}


async def cmd_probe(args: argparse.Namespace) -> None:
    async with client() as http:
        for model in (args.short_model, args.long_model):
            result = await decide(http, model, PROBE_STATE, PROBE_QUESTION)
            status = 'available' if 'answers' in result else f'unavailable ({result["error"]})'
            print(json.dumps({'model': model, 'status': status}))


async def cmd_estimate(args: argparse.Namespace) -> None:
    questions = load_questions(args.questions)[0] if args.questions else PROBE_QUESTION
    async with client() as http:
        ids = read_ids(args.trace_ids) if args.trace_ids else await window_ids(http, args.search_body, args.max_traces)
        if not ids:
            sys.exit('✗ the window holds no traces; re-check the target filter before reading this as clean')
        sample = random.Random(args.seed).sample(ids, min(args.sample, len(ids)))
        sizes = []
        for trace_id in sample:
            try:
                sizes.append(state_tokens(build_state(await conversation(http, trace_id)), questions))
            except (RuntimeError, httpx.HTTPError, ValueError) as exc:
                print(f'⚠ {trace_id}: {exc}', file=sys.stderr)
    if not sizes:
        sys.exit('✗ no sampled conversation could be read')
    short = [s for s in sizes if s <= SHORT_LIMIT_TOKENS]
    long = [s for s in sizes if s > SHORT_LIMIT_TOKENS]
    share_long = len(long) / len(sizes)
    n = len(ids)
    cost_short = (
        n
        * (1 - share_long)
        * (sum(short) / len(short) if short else 0)
        * PRICES.get(args.short_model, PRICES[SHORT_MODEL])
        / 1e6
    )
    cost_long = (
        n * share_long * (sum(long) / len(long) if long else 0) * PRICES.get(args.long_model, PRICES[LONG_MODEL]) / 1e6
    )
    sizes.sort()
    print(
        json.dumps(
            {
                'traces_in_window': n,
                'capped_at_max_traces': n >= args.max_traces,
                'sampled': len(sizes),
                'estimated_tokens': {
                    'p50': sizes[len(sizes) // 2],
                    'p90': sizes[min(len(sizes) - 1, int(len(sizes) * 0.9))],
                    'max': sizes[-1],
                },
                'share_over_short_limit': round(share_long, 3),
                'estimated_cost_usd': {
                    'short': round(cost_short, 4),
                    'long': round(cost_long, 4),
                    'total': round(cost_short + cost_long, 4),
                },
                'note': 'Input tokens at catalogue prices, one call per trace; extra questions in the same call add '
                'almost nothing. The long model may also bill output tokens, so treat the long share as a floor.',
            },
            indent=2,
        )
    )


async def cmd_label(args: argparse.Namespace) -> None:
    questions, _ = load_questions(args.questions)
    async with client() as http:
        ids = read_ids(args.trace_ids) if args.trace_ids else await window_ids(http, args.search_body, args.max_traces)
        done = done_ids(args.out, questions_sha(questions))
        todo = [trace_id for trace_id in ids if trace_id not in done]
        print(f'✓ {len(ids)} traces, {len(done & set(ids))} already labelled, {len(todo)} to go', file=sys.stderr)
        semaphore = asyncio.Semaphore(args.concurrency)
        lock = asyncio.Lock()
        totals = Counter()
        cost = 0.0
        started = time.perf_counter()

        async def run(trace_id: str) -> None:
            nonlocal cost
            async with semaphore:
                row = await label_one(http, trace_id, questions, args.short_model, args.long_model)
            async with lock:
                with args.out.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                totals['error' if 'error' in row else row['route']] += 1
                cost += row.get('cost') or 0.0
                if sum(totals.values()) % 100 == 0:
                    print(f'… {sum(totals.values())}/{len(todo)} · ${cost:.2f}', file=sys.stderr)

        await asyncio.gather(*(run(trace_id) for trace_id in todo))
    print(
        json.dumps(
            {
                'traces_in_window': len(ids),
                'capped_at_max_traces': not args.trace_ids and len(ids) >= args.max_traces,
                'labelled_short': totals['short'],
                'labelled_long': totals['long'],
                'errors': totals['error'],
                'cost_usd': round(cost, 4),
                'seconds': round(time.perf_counter() - started),
                'out': str(args.out),
            },
            indent=2,
        )
    )


def cmd_pick(args: argparse.Namespace) -> None:
    """Flagged picks at random among the flagged traces; the random half uniformly from every other trace in the
    labels file, including those the model could not label (too long, failed), so they can still be read."""
    rows = list(latest_rows(args.labels).values())
    flagged = [
        row['trace_id']
        for row in rows
        if 'answers' in row and ((row['answers'].get(args.question) or {}).get('noul') or 0) >= args.threshold
    ]
    rng = random.Random(args.seed)
    picked_flagged = rng.sample(flagged, min(args.flagged, len(flagged)))
    taken = set(picked_flagged)
    pool = [row['trace_id'] for row in rows if row['trace_id'] not in taken]
    picked_random = rng.sample(pool, min(args.random, len(pool)))
    if args.write:
        args.write.mkdir(parents=True, exist_ok=True)
        (args.write / 'flagged_ids.txt').write_text(''.join(f'{i}\n' for i in picked_flagged), encoding='utf-8')
        (args.write / 'random_ids.txt').write_text(''.join(f'{i}\n' for i in picked_random), encoding='utf-8')
    print(
        json.dumps(
            {
                'traces': len(rows),
                'labelled': sum('answers' in row for row in rows),
                'flagged': len(flagged),
                'picks': {'flagged': picked_flagged, 'random': picked_random},
            },
            indent=2,
        )
    )


def kappa(tp: int, fp: int, fn: int, tn: int) -> float | None:
    n = tp + fp + fn + tn
    if not n:
        return None
    observed = (tp + tn) / n
    expected = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / n**2
    return None if expected == 1 else round((observed - expected) / (1 - expected), 3)


def cmd_agreement(args: argparse.Namespace) -> None:
    questions, thresholds = load_questions(args.questions)
    agent = json.loads(args.agent_labels.read_text(encoding='utf-8-sig'))
    rows = latest_rows(args.labels, questions_sha(questions))
    modes = mode_names(questions)
    random_ids = set(read_ids(args.random_ids)) if args.random_ids else set()
    report: dict[str, Any] = {'compared': 0, 'missing_or_unreadable': [], 'unknown_agent_labels': {}, 'modes': {}}
    cells: dict[tuple[str, str], Counter] = {}
    for trace_id, agent_mode in agent.items():
        if not isinstance(agent_mode, str) or (agent_mode not in modes and agent_mode != 'none'):
            # A display name instead of the slug, or a mode left out of the questions: scoring it would turn the
            # model's correct flags into false positives.
            report['unknown_agent_labels'][trace_id] = agent_mode
            continue
        row = rows.get(trace_id)
        flagged = verdicts(row, questions, thresholds) if row and 'answers' in row else None
        if flagged is None:
            report['missing_or_unreadable'].append(trace_id)
            continue
        report['compared'] += 1
        for mode in modes:
            for route in ('all', row['route'], *(('random',) if trace_id in random_ids else ())):
                truth, guess = agent_mode == mode, mode in flagged
                cells.setdefault((mode, route), Counter())[
                    'tp' if truth and guess else 'fp' if guess else 'fn' if truth else 'tn'
                ] += 1
    for mode in modes:
        report['modes'][mode] = {}
        for route in ('all', 'short', 'long', 'random'):
            c = cells.get((mode, route))
            if not c:
                continue
            tp, fp, fn, tn = c['tp'], c['fp'], c['fn'], c['tn']
            report['modes'][mode][route] = {
                'agent_positives': tp + fn,
                'tp': tp,
                'fp': fp,
                'fn': fn,
                'tn': tn,
                'precision': round(tp / (tp + fp), 3) if tp + fp else None,
                'recall': round(tp / (tp + fn), 3) if tp + fn else None,
                'kappa': kappa(tp, fp, fn, tn),
            }
    print(json.dumps(report, indent=2))


def cmd_rates(args: argparse.Namespace) -> None:
    questions, thresholds = load_questions(args.questions)
    rows = latest_rows(args.labels, questions_sha(questions))
    modes = mode_names(questions)
    counts: dict[str, Counter] = {route: Counter() for route in ('all', 'short', 'long')}
    labelled = Counter()
    multi = 0
    errors = Counter()
    for row in rows.values():
        flagged = verdicts(row, questions, thresholds) if 'answers' in row else None
        if flagged is None:
            errors[(row.get('error') or 'unreadable answer').split(':')[0]] += 1
            continue
        multi += len(flagged) > 1
        for route in ('all', row['route']):
            labelled[route] += 1
            counts[route].update(flagged)
    print(
        json.dumps(
            {
                'traces': len(rows),
                'labelled': dict(labelled),
                'not_labelled': dict(errors),
                'flagged_more_than_one_mode': multi,
                'modes': {
                    mode: {
                        route: {'count': counts[route][mode], 'rate': round(counts[route][mode] / labelled[route], 4)}
                        for route in ('all', 'short', 'long')
                        if labelled[route]
                    }
                    for mode in modes
                },
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)

    def models(p: argparse.ArgumentParser) -> None:
        p.add_argument('--short-model', default=SHORT_MODEL)
        p.add_argument('--long-model', default=LONG_MODEL)

    def window(p: argparse.ArgumentParser) -> None:
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument('--search-body', type=Path, help='traces search body file: from, to, filters')
        source.add_argument('--trace-ids', type=Path, help='one trace id per line, instead of a search')
        p.add_argument('--max-traces', type=int, default=5000)

    p = sub.add_parser('probe')
    models(p)
    p = sub.add_parser('estimate')
    models(p)
    window(p)
    p.add_argument('--sample', type=int, default=30)
    p.add_argument('--questions', type=Path, help='size the sample with these questions (default: one short one)')
    p.add_argument('--seed', type=int, default=0)
    p = sub.add_parser('label')
    models(p)
    window(p)
    p.add_argument('--questions', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--concurrency', type=int, default=16)
    p = sub.add_parser('pick')
    p.add_argument('--labels', type=Path, required=True)
    p.add_argument('--question', required=True, help='the noul question key of the selection pass')
    p.add_argument('--threshold', type=float, default=0.5)
    p.add_argument('--flagged', type=int, default=30)
    p.add_argument('--random', type=int, default=50)
    p.add_argument('--write', type=Path, help='also write flagged_ids.txt and random_ids.txt into this directory')
    p.add_argument('--seed', type=int, default=0)
    p = sub.add_parser('agreement')
    p.add_argument('--labels', type=Path, required=True)
    p.add_argument('--questions', type=Path, required=True)
    p.add_argument('--agent-labels', type=Path, required=True, help='{trace_id: mode slug or "none"}, traces read')
    p.add_argument('--random-ids', type=Path, help="pick's random_ids.txt: also report the random stratum alone")
    p = sub.add_parser('rates')
    p.add_argument('--labels', type=Path, required=True)
    p.add_argument('--questions', type=Path, required=True)

    args = parser.parse_args()
    handlers = {'probe': cmd_probe, 'estimate': cmd_estimate, 'label': cmd_label}
    if args.command in handlers:
        asyncio.run(handlers[args.command](args))
    else:
        {'pick': cmd_pick, 'agreement': cmd_agreement, 'rates': cmd_rates}[args.command](args)


if __name__ == '__main__':
    main()
