# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "truststore>=0.9; sys_platform == 'win32'",
# ]
# ///
"""Window-wide decisions for orq-analyze-traces: one `/decisions` call per trace, over every trace in the window.

The agent reads a sample and names the failure modes; this script finds them across the whole window. It never
writes to the platform: it reads traces (`POST /v3/traces/search`, `GET /v3/traces/{id}/conversation`) and asks the
router's decision models (`POST /v3/router/decisions`), which costs money but changes nothing.

A trace goes whole to the short model when its estimated size fits that model's budget, and whole to the long model
otherwise; a short call the router still rejects as too long is retried long. Traces are never chunked or trimmed:
max-pooled chunks flag long traces more often, trimming flags them less, and either makes what the model finds
depend on trace length. This is also why the script does not build on evaluatorq's labelling pass, which chunks;
its state shape differs from evaluatorq's `jev_state` too, and calibration measures the instrument as built here.

Subcommands and the files they share are described in the skill's "Window decisions" section. Requires
`ORQ_API_KEY`; `ORQ_API_BASE_URL` overrides the host (default https://api.orq.ai).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import ssl
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

if sys.platform == 'win32':
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


@dataclass(frozen=True)
class Model:
    budget_tokens: int  # estimated state + longest question routed to this model
    price_per_m: float  # USD per million input tokens, catalogue price on 2026-10-14; `estimate` only


# Jev and decider-4b reject state plus longest question above 32k tokens; the 4k margin absorbs the 4-characters
# estimate, and a trace it misjudges is retried on the long model.
MODELS = {
    'typesafe/jev-latest': Model(28_000, 0.042),
    'orq/decider-4b': Model(28_000, 0.035),
    'openai/gpt-6-luna': Model(1_000_000, 0.10),
}
SHORT_MODEL = 'typesafe/jev-latest'
LONG_MODEL = 'openai/gpt-6-luna'
OVERFLOW = 'max_tokens_exceeded'
# Undercounts CJK and other dense scripts by up to 4x. The overflow retry catches that on the short model; the long
# model's pre-check can still pass a trace the router then rejects, which is recorded as an error row.
CHARS_PER_TOKEN = 4
DEFAULT_MAX_TRACES = 5000
SEARCH_PAGE = 100
MAX_ATTEMPTS = 5
# A mode clears for window-wide labelling only on enough evidence that the model finds what the agent found.
MIN_POSITIVES = 10
MIN_RECALL = 0.7
FLAGGED_LIST = 50


def fail(message: str) -> None:
    sys.exit(f'✗ {message}')


def model(name: str) -> Model:
    if name not in MODELS:
        fail(f'unknown decision model {name!r}; known: {", ".join(MODELS)}')
    return MODELS[name]


def base_url() -> str:
    return os.environ.get('ORQ_API_BASE_URL', 'https://api.orq.ai').rstrip('/') or 'https://api.orq.ai'


def tls_verify() -> bool | ssl.SSLContext:
    """Same policy as orq-evaluator-alignment's `lib/orq_client.tls_verify` (RES-1387): verify always, and on
    Windows through the OS trust store. Never fall back to unverified TLS: the API key rides these connections."""
    if sys.platform == 'win32':
        try:
            import truststore
        except ImportError:
            fail('truststore is required on Windows; run this script with `uv run`, which installs it')
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return True


def client() -> httpx.AsyncClient:
    key = os.environ.get('ORQ_API_KEY')
    if not key:
        fail('ORQ_API_KEY is not set')
    return httpx.AsyncClient(
        base_url=base_url(), headers={'Authorization': f'Bearer {key}'}, timeout=300, verify=tls_verify()
    )


async def request(http: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Retries 429, 5xx, the router's 499 `internal_error` and network errors; returns any other status."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            print(f'⚠ {method} {path}: {type(exc).__name__}, retrying', file=sys.stderr)
            await asyncio.sleep(2**attempt)
            continue
        if response.status_code in (429, 499) or response.status_code >= 500:
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


@dataclass
class Window:
    ids: list[str]  # newest first
    capped: bool
    source: str


async def load_window(http: httpx.AsyncClient, args: argparse.Namespace) -> Window:
    """The traces to label. Over the cap, refuse unless `--max-traces` was given: a cap silently turns the date
    range into its newest N traces."""
    if args.trace_ids:
        return Window(read_ids(args.trace_ids), False, str(args.trace_ids))
    limit = args.max_traces or DEFAULT_MAX_TRACES
    body = json.loads(args.search_body.read_text(encoding='utf-8-sig'))
    for field in ('from', 'to'):
        if field not in body:
            fail(f'{args.search_body}: `{field}` is required (RFC3339) in a search body')
    body['sort'] = [{'field': 'end_time', 'order': 'desc'}]
    body['limit'] = SEARCH_PAGE
    ids: dict[str, None] = {}  # search rows repeat a trace once per span
    seen_tokens: set[str] = set()
    while len(ids) <= limit:
        response = await request(http, 'POST', '/v3/traces/search', json=body)
        if response.status_code != 200:
            fail(f'traces search: HTTP {response.status_code} {response.text[:300]}')
        payload = response.json()
        for row in payload.get('data') or []:
            if row.get('trace_id'):
                ids.setdefault(row['trace_id'], None)
        token = payload.get('next_page_token')
        if not payload.get('has_more', bool(token)) or not token or token in seen_tokens:
            break
        seen_tokens.add(token)
        body['page_token'] = token
    capped = len(ids) > limit
    if capped and args.max_traces is None:
        fail(
            f'the window holds more than {limit:,} traces; pass --max-traces to label only the newest N '
            '(and report the window as those traces), or narrow the search body'
        )
    window = f'{body["from"]} to {body["to"]}'
    return Window(list(ids)[:limit], capped, window)


def read_ids(path: Path) -> list[str]:
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    return list(dict.fromkeys(line.strip() for line in lines if line.strip()))


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


async def conversation(http: httpx.AsyncClient, trace_id: str) -> list[dict[str, Any]]:
    response = await request(http, 'GET', f'/v3/traces/{trace_id}/conversation')
    if response.status_code != 200:
        raise FetchError(f'conversation HTTP {response.status_code}: {response.text[:200]}', response.status_code)
    items = response.json().get('items') or []
    return [item for item in items if isinstance(item, dict)]


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('text'))
    return ''


def build_state(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Every message, tool call and tool result in order, whole. Reasoning is left out: it is the model's
    scratchpad, not something the agent did or said."""
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
            # Built-in tool calls (web or file search), MCP calls and anything newer, as recorded.
            entries.append({'index': index, **{k: v for k, v in item.items() if k not in ('id', 'signature')}})
    return {'messages': entries}


def state_tokens(state: dict[str, Any], questions: dict[str, Any]) -> int:
    longest = max((len(json.dumps(q, ensure_ascii=False)) for q in questions.values()), default=0)
    return (len(json.dumps(state, ensure_ascii=False)) + longest) // CHARS_PER_TOKEN


def estimated_cost(name: str, tokens: int) -> float:
    return tokens * model(name).price_per_m / 1e6


class Budget:
    """Each call reserves its estimated cost before it starts and settles to the billed cost when it ends, so calls
    already in flight cannot carry a pass past `--budget-usd`. No await sits between check and reserve, so the event
    loop needs no lock here."""

    def __init__(self, limit: float) -> None:
        self.limit, self.spent, self.held = limit, 0.0, 0.0

    def reserve(self, amount: float) -> bool:
        if self.spent + self.held + amount > self.limit:
            return False
        self.held += amount
        return True

    def settle(self, held: float, cost: float) -> None:
        self.held -= held
        self.spent += cost


# --- questions and runs ------------------------------------------------------------------------------------------


@dataclass
class Questions:
    wire: dict[str, Any]  # exactly what is sent
    thresholds: dict[str, float]
    path: Path


def load_questions(path: Path) -> Questions:
    """`{key: {type: noul | choice, instructions, criteria?, threshold?}}`. `threshold` (noul) is never sent."""
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(raw, dict) or not raw:
        fail(f'{path}: expected a non-empty object of questions')
    wire: dict[str, Any] = {}
    thresholds: dict[str, float] = {}
    for key, question in raw.items():
        if question.get('type') not in ('noul', 'choice'):
            fail(f'{path}: question {key!r} needs type noul or choice')
        if question['type'] == 'choice' and not isinstance(question.get('criteria'), dict):
            fail(f'{path}: choice question {key!r} needs a {{label: description}} criteria object')
        thresholds[key] = float(question.get('threshold', 0.5))
        wire[key] = {k: v for k, v in question.items() if k != 'threshold'}
    return Questions(wire, thresholds, path)


def run_file(args: argparse.Namespace, questions: Questions) -> Path:
    """One labels file per questions file and model pair, so a rewritten question or a switched model can never be
    answered by, or pooled with, an earlier pass."""
    key = json.dumps([questions.wire, args.short_model, args.long_model], sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return args.run_dir / f'{questions.path.stem}-{digest}.jsonl'


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Rows of a labels file. A torn last line (a run killed mid-write) is skipped, so the pass stays resumable."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def latest_rows(path: Path) -> dict[str, dict[str, Any]]:
    """The last row per trace: a retried trace's success replaces its earlier error."""
    rows: dict[str, dict[str, Any]] = {}
    for row in read_rows(path):
        if 'answers' in row or row['trace_id'] not in rows:
            rows[row['trace_id']] = row
    return rows


def existing_rows(args: argparse.Namespace, questions: Questions) -> tuple[Path, dict[str, dict[str, Any]]]:
    path = run_file(args, questions)
    if not path.exists():
        fail(f'no labels for {questions.path} with these models in {args.run_dir}; run `label` first')
    return path, latest_rows(path)


def meta_path(labels: Path) -> Path:
    return labels.with_suffix('.meta.json')


def load_meta(labels: Path) -> dict[str, Any]:
    """The last window pass over this labels file: its source, size and trace ids (newest first)."""
    path = meta_path(labels)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def verdicts(row: dict[str, Any], questions: Questions) -> set[str] | None:
    """The modes a row flags, or None when any answer is missing, refused, or a label outside the criteria."""
    flagged: set[str] = set()
    answers = row.get('answers') or {}
    for key, question in questions.wire.items():
        answer = answers.get(key)
        if not isinstance(answer, dict) or answer.get('type') != question['type']:
            return None
        if question['type'] == 'choice':
            label = answer.get('choice')
            if label not in question['criteria']:
                return None
            if label not in ('none', 'pass'):
                flagged.add(label)
        else:
            if answer.get('noul') is None:
                return None
            if answer['noul'] >= questions.thresholds[key]:
                flagged.add(key)
    return flagged


def mode_names(questions: Questions) -> list[str]:
    names: list[str] = []
    for key, question in questions.wire.items():
        if question['type'] == 'choice':
            names.extend(label for label in question['criteria'] if label not in ('none', 'pass'))
        else:
            names.append(key)
    return names


# --- decisions ---------------------------------------------------------------------------------------------------


async def decide(http: httpx.AsyncClient, name: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
    response = await request(
        http, 'POST', '/v3/router/decisions', json={'model': name, 'state': state, 'questions': questions}
    )
    if response.status_code == 200:
        body = response.json()
        usage = body.get('usage') if isinstance(body.get('usage'), dict) else {}
        return {'answers': body.get('answers') or {}, 'usage': usage}
    return {
        'error': f'HTTP {response.status_code}: {response.text[:200]}',
        'status': response.status_code,
        'overflow': response.status_code == 400 and OVERFLOW in response.text,
    }


async def label_one(
    http: httpx.AsyncClient, trace_id: str, order: int, args: argparse.Namespace, wire: dict, budget: Budget
) -> dict | None:
    """One trace's row, or None when the budget cannot cover its call (the trace stays unstarted)."""
    row: dict[str, Any] = {'trace_id': trace_id, 'order': order}
    try:
        state = build_state(await conversation(http, trace_id))
    except (RuntimeError, httpx.HTTPError, ValueError) as exc:
        return {**row, 'error': f'fetch: {exc}', 'status': getattr(exc, 'status', None)}
    if not state['messages']:
        return {**row, 'error': 'empty conversation'}
    tokens = state_tokens(state, wire)
    row['estimated_tokens'] = tokens
    if tokens > model(args.long_model).budget_tokens:
        return {**row, 'error': f'too long for {args.long_model} (about {tokens:,} tokens)'}
    name, reason = (
        (args.short_model, 'fits') if tokens <= model(args.short_model).budget_tokens else (args.long_model, 'over')
    )
    held, cost = 0.0, 0.0
    try:
        if not budget.reserve(estimated_cost(name, tokens)):
            return None
        held = estimated_cost(name, tokens)
        result = await decide(http, name, state, wire)
        if result.get('overflow') and name == args.short_model:
            name, reason = args.long_model, 'short_overflowed'
            if not budget.reserve(estimated_cost(name, tokens)):
                return None
            held += estimated_cost(name, tokens)
            result = await decide(http, name, state, wire)
        row.update(model=name, route='short' if name == args.short_model else 'long', reason=reason)
        if 'error' in result:
            return {**row, 'error': result['error'], 'status': result['status']}
        billed = result['usage'].get('total_cost')
        if isinstance(billed, (int, float)):
            cost = float(billed)
            return {**row, 'answers': result['answers'], 'cost': cost}
        # No billed cost on the response: count the estimate, so the budget cap still holds.
        cost = estimated_cost(name, tokens)
        return {**row, 'answers': result['answers'], 'cost': cost, 'cost_estimated': True}
    except (httpx.HTTPError, ValueError) as exc:
        return {**row, 'model': name, 'error': f'decide: {type(exc).__name__}: {exc}'}
    finally:
        budget.settle(held, cost)


# --- subcommands -------------------------------------------------------------------------------------------------


PROBE_STATE = {'messages': [{'role': 'user', 'text': 'Where is my order?'}, {'role': 'assistant', 'text': 'Today.'}]}
PROBE_QUESTION = {'probe': {'type': 'noul', 'instructions': 'Did the assistant answer the user?'}}


async def cmd_probe(args: argparse.Namespace) -> None:
    ok = True
    async with client() as http:
        for name in (args.short_model, args.long_model):
            model(name)
            try:
                result = await decide(http, name, PROBE_STATE, PROBE_QUESTION)
            except httpx.HTTPError as exc:
                result = {'error': f'{type(exc).__name__}: {exc}', 'status': None}
            if 'answers' in result:
                status = 'available'
            elif result.get('status') == 429:
                status = 'rate limited: retry in a minute, this is not unavailability'
            else:
                status, ok = f'unavailable ({result["error"]})', False
            print(json.dumps({'model': name, 'status': status}))
    if not ok:
        sys.exit(1)


async def cmd_estimate(args: argparse.Namespace) -> None:
    wire = load_questions(args.questions).wire if args.questions else PROBE_QUESTION
    short, long = model(args.short_model), model(args.long_model)
    async with client() as http:
        window = await load_window(http, args)
        if not window.ids:
            fail('the window holds no traces; re-check the target filter before reading this as clean')
        sample = random.Random(args.seed).sample(window.ids, min(args.sample, len(window.ids)))
        sizes = []
        for trace_id in sample:
            try:
                sizes.append(state_tokens(build_state(await conversation(http, trace_id)), wire))
            except (RuntimeError, httpx.HTTPError, ValueError) as exc:
                print(f'⚠ {trace_id}: {exc}', file=sys.stderr)
    if not sizes:
        fail('no sampled conversation could be read')
    n = len(window.ids)
    over = [s for s in sizes if s > short.budget_tokens]
    under = [s for s in sizes if s <= short.budget_tokens]
    cost_short = n * len(under) / len(sizes) * (sum(under) / len(under) if under else 0) * short.price_per_m / 1e6
    cost_long = n * len(over) / len(sizes) * (sum(over) / len(over) if over else 0) * long.price_per_m / 1e6
    sizes.sort()
    report = {
        'window': window.source,
        'traces': n,
        'capped_at_max_traces': window.capped,
        'sampled': len(sizes),
        'estimated_tokens': {'p50': sizes[len(sizes) // 2], 'max': sizes[-1]},
        'sampled_over_short_budget': len(over),
        'estimated_cost_usd': round(cost_short + cost_long, 4),
        'note': 'input tokens at catalogue prices, one call per trace whatever the number of questions. '
        + (
            'No sampled trace went long; one that does costs up to '
            f'${long.budget_tokens * long.price_per_m / 1e6:.2f}, so budget above this.'
            if not over
            else 'The long model may also bill output tokens; budget above this.'
        ),
    }
    print(json.dumps(report, indent=2))


async def cmd_label(args: argparse.Namespace) -> None:
    questions = load_questions(args.questions)
    model(args.short_model)
    model(args.long_model)
    out = run_file(args, questions)
    out.parent.mkdir(parents=True, exist_ok=True)
    async with client() as http:
        window = await load_window(http, args)
        done = {row['trace_id'] for row in read_rows(out) if 'answers' in row}
        todo = [(order, trace_id) for order, trace_id in enumerate(window.ids) if trace_id not in done]
        print(f'✓ {len(window.ids)} traces, {len(window.ids) - len(todo)} already labelled → {out}', file=sys.stderr)
        semaphore = asyncio.Semaphore(args.concurrency)
        lock = asyncio.Lock()
        totals: Counter = Counter()
        budget = Budget(args.budget_usd)
        abort: str | None = None
        warned_estimate = False
        started = time.perf_counter()

        async def run(order: int, trace_id: str) -> None:
            nonlocal abort, warned_estimate
            async with semaphore:
                if abort:
                    totals['not_started'] += 1
                    return
                try:
                    row = await label_one(http, trace_id, order, args, questions.wire, budget)
                except Exception as exc:  # one malformed trace must not end a paid pass
                    row = {'trace_id': trace_id, 'order': order, 'error': f'{type(exc).__name__}: {exc}'}
            if row is None:
                totals['not_started'] += 1
                return
            async with lock:
                with out.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                totals['error' if 'error' in row else row['route']] += 1
                if row.get('cost_estimated') and not warned_estimate:
                    warned_estimate = True
                    print('⚠ the router reported no cost; the budget counts estimated input cost', file=sys.stderr)
                if row.get('status') in (401, 403):
                    abort = row['error']
                if sum(totals.values()) % 100 == 0:
                    print(f'… {sum(totals.values())}/{len(todo)} · ${budget.spent:.2f}', file=sys.stderr)

        await asyncio.gather(*(run(order, trace_id) for order, trace_id in todo))
    meta = {'window': window.source, 'traces': len(window.ids), 'capped_at_max_traces': window.capped}
    if args.search_body:
        # Only a window pass describes the window; a `--trace-ids` pass (calibration) shares the labels file.
        meta_path(out).write_text(json.dumps({**meta, 'ids': window.ids}, indent=2), encoding='utf-8')
    rows = latest_rows(out)
    report = {
        **meta,
        'labelled': sum('answers' in row for row in rows.values()),
        'this_run': {
            **dict(totals),
            'cost_usd': round(budget.spent, 4),
            'cost_estimated': warned_estimate,
            'seconds': round(time.perf_counter() - started),
        },
        'stopped': abort
        or (
            f'budget of ${args.budget_usd} reached; re-run with a higher --budget-usd to finish'
            if totals['not_started']
            else None
        ),
        'labels': str(out),
    }
    print(json.dumps(report, indent=2))
    if abort:
        fail(f'stopped on an authorisation error: {abort}')


def cmd_pick(args: argparse.Namespace) -> None:
    """Flagged picks at random among the flagged traces; the random half uniformly from every other trace in the
    window, including those the model could not label or never reached, so they can still be read."""
    questions = load_questions(args.questions)
    path, rows_by_id = existing_rows(args, questions)
    key = args.question or next(iter(questions.wire))
    if key not in questions.wire or questions.wire[key]['type'] != 'noul':
        fail(f'{args.questions}: pick needs a noul question; {key!r} is not one')
    rows = list(rows_by_id.values())
    window_ids = load_meta(path).get('ids') or list(rows_by_id)
    flagged = [
        row['trace_id']
        for row in rows
        if 'answers' in row and ((row['answers'].get(key) or {}).get('noul') or 0) >= questions.thresholds[key]
    ]
    rng = random.Random(args.seed)
    picked_flagged = rng.sample(flagged, min(args.flagged, len(flagged)))
    taken = set(picked_flagged)
    pool = [trace_id for trace_id in window_ids if trace_id not in taken]
    picked_random = rng.sample(pool, min(args.random, len(pool)))
    picks = args.run_dir / 'picks'
    picks.mkdir(parents=True, exist_ok=True)
    (picks / 'flagged_ids.txt').write_text(''.join(f'{i}\n' for i in picked_flagged), encoding='utf-8')
    (picks / 'random_ids.txt').write_text(''.join(f'{i}\n' for i in picked_random), encoding='utf-8')
    report = {
        'traces': len(window_ids),
        'labelled': sum('answers' in row for row in rows),
        'flagged': len(flagged),
        'picks': {'flagged': picked_flagged, 'random': picked_random},
        'written': str(picks),
    }
    print(json.dumps(report, indent=2))


def kappa(tp: int, fp: int, fn: int, tn: int) -> float | None:
    n = tp + fp + fn + tn
    expected = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / n**2 if n else 1
    return None if expected == 1 else round(((tp + tn) / n - expected) / (1 - expected), 3)


def cmd_agreement(args: argparse.Namespace) -> None:
    questions = load_questions(args.questions)
    _, rows = existing_rows(args, questions)
    agent = json.loads(args.agent_labels.read_text(encoding='utf-8-sig'))
    modes = mode_names(questions)
    random_path = args.run_dir / 'picks' / 'random_ids.txt'
    random_ids = set(read_ids(random_path)) if random_path.exists() else set()
    report: dict[str, Any] = {'compared': 0, 'missing_or_unreadable': [], 'unknown_agent_labels': {}, 'modes': {}}
    cells: dict[tuple[str, str], Counter] = {}
    for trace_id, agent_mode in agent.items():
        if not isinstance(agent_mode, str) or (agent_mode not in modes and agent_mode != 'none'):
            # Scoring a label that names no mode would turn the model's correct flags into false positives.
            report['unknown_agent_labels'][trace_id] = agent_mode
            continue
        row = rows.get(trace_id)
        flagged = verdicts(row, questions) if row and 'answers' in row else None
        if flagged is None:
            report['missing_or_unreadable'].append(trace_id)
            continue
        report['compared'] += 1
        for mode in modes:
            truth, guess = agent_mode == mode, mode in flagged
            cell = 'tp' if truth and guess else 'fp' if guess else 'fn' if truth else 'tn'
            for stratum in ('all', row['route'], *(('random',) if trace_id in random_ids else ())):
                cells.setdefault((mode, stratum), Counter())[cell] += 1
    for mode in modes:
        strata = {}
        for stratum in ('all', 'short', 'long', 'random'):
            c = cells.get((mode, stratum))
            if c:
                tp, fp, fn, tn = c['tp'], c['fp'], c['fn'], c['tn']
                strata[stratum] = {
                    'agent_positives': tp + fn,
                    'tp': tp,
                    'fp': fp,
                    'fn': fn,
                    'tn': tn,
                    'recall': round(tp / (tp + fn), 3) if tp + fn else None,
                    'precision': round(tp / (tp + fp), 3) if tp + fp else None,
                    'kappa': kappa(tp, fp, fn, tn),
                }
        overall = strata.get('all', {'agent_positives': 0, 'recall': None})
        if overall['agent_positives'] < MIN_POSITIVES:
            clears = f'no: {overall["agent_positives"]} agent positives, {MIN_POSITIVES} needed'
        elif (overall['recall'] or 0) < MIN_RECALL:
            clears = f'no: recall {overall["recall"]} below {MIN_RECALL}'
        else:
            clears = 'yes'
        report['modes'][mode] = {'clears': clears, **strata}
    print(json.dumps(report, indent=2))


def wilson(count: int, n: int) -> list[float]:
    """95% Wilson interval of a share: the flag rate's sampling spread, not the model's error."""
    if not n:
        return [0.0, 0.0]
    z, p = 1.96, count / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - spread), 4), round(min(1.0, centre + spread), 4)]


def cmd_rates(args: argparse.Namespace) -> None:
    questions = load_questions(args.questions)
    path, rows = existing_rows(args, questions)
    meta = load_meta(path)
    window_ids = meta.pop('ids', None)
    modes = mode_names(questions)
    read = set(read_ids(args.read_ids)) if args.read_ids else set()
    labelled: Counter = Counter()
    flagged_by: dict[str, list[tuple[int, str]]] = {mode: [] for mode in modes}
    routes: dict[str, Counter] = {mode: Counter() for mode in modes}
    not_labelled: Counter = Counter()
    multi = 0
    for row in rows.values():
        flagged = verdicts(row, questions) if 'answers' in row else None
        if flagged is None:
            not_labelled[(row.get('error') or 'unreadable answer').split(':')[0]] += 1
            continue
        labelled[row['route']] += 1
        multi += len(flagged) > 1
        for mode in flagged:
            flagged_by[mode].append((row.get('order', 0), row['trace_id']))
            routes[mode][row['route']] += 1
    n = sum(labelled.values())
    report = {
        **meta,
        'labelled': n,
        'labelled_by_route': dict(labelled),
        'not_labelled': dict(not_labelled),
        # Window traces with no row at all: the pass stopped at its budget (or was interrupted) before reaching them.
        'not_started': sum(trace_id not in rows for trace_id in window_ids) if window_ids is not None else None,
        'flagged_more_than_one_mode': multi,
        'modes': {
            mode: {
                'count': len(flagged_by[mode]),
                'flag_rate': round(len(flagged_by[mode]) / n, 4) if n else None,
                'interval': wilson(len(flagged_by[mode]), n),
                'by_route': dict(routes[mode]),
                'flagged_not_read': [trace_id for _, trace_id in sorted(flagged_by[mode]) if trace_id not in read][
                    :FLAGGED_LIST
                ],
            }
            for mode in modes
        },
    }
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)

    def models(p: argparse.ArgumentParser) -> None:
        p.add_argument('--short-model', default=SHORT_MODEL, help=f'one of {", ".join(MODELS)}')
        p.add_argument('--long-model', default=LONG_MODEL)

    def window(p: argparse.ArgumentParser) -> None:
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument('--search-body', type=Path, help='traces search body file: from, to, filters')
        source.add_argument('--trace-ids', type=Path, help='one trace id per line, instead of a search')
        p.add_argument('--max-traces', type=int, help=f'label only the newest N (needed above {DEFAULT_MAX_TRACES:,})')

    def run(p: argparse.ArgumentParser) -> None:
        models(p)
        p.add_argument('--questions', type=Path, required=True)
        p.add_argument('--run-dir', type=Path, default=Path('decide-runs'))

    p = sub.add_parser('probe')
    models(p)
    p = sub.add_parser('estimate')
    models(p)
    window(p)
    p.add_argument('--questions', type=Path, help='size the sample with these questions')
    p.add_argument('--sample', type=int, default=30)
    p.add_argument('--seed', type=int, default=0)
    p = sub.add_parser('label')
    run(p)
    window(p)
    p.add_argument('--budget-usd', type=float, required=True, help='stop starting calls once this much is spent')
    p.add_argument('--concurrency', type=int, default=16)
    p = sub.add_parser('pick')
    run(p)
    p.add_argument('--question', help='the noul question to pick on (default: the first)')
    p.add_argument('--flagged', type=int, default=30)
    p.add_argument('--random', type=int, default=50)
    p.add_argument('--seed', type=int, default=0)
    p = sub.add_parser('agreement')
    run(p)
    p.add_argument('--agent-labels', type=Path, required=True, help='{trace_id: mode slug or "none"}, traces read')
    p = sub.add_parser('rates')
    run(p)
    p.add_argument('--read-ids', type=Path, help='traces already read, left out of the flagged lists')

    args = parser.parse_args()
    handlers = {'probe': cmd_probe, 'estimate': cmd_estimate, 'label': cmd_label}
    if args.command in handlers:
        asyncio.run(handlers[args.command](args))
    else:
        {'pick': cmd_pick, 'agreement': cmd_agreement, 'rates': cmd_rates}[args.command](args)


if __name__ == '__main__':
    main()
