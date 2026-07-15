"""Wrap the audited evaluator's judge (prompt + model) for evaluatorq.

The hosted orq evaluation path supports no repetitions flag and silently drops
client temperature overrides (project memory, verified 2026-05-27). So instead
of invoking the stored evaluator we reconstruct its judge — the same judge
prompt and judge model — as an evaluatorq `judge_fn`, and run it through
`run_jury(..., repetitions=N)` once per datapoint. evaluatorq applies the
temperature client-side and hands back the N raw per-repetition verdicts on
`JuryVote.repetitions`. That is the entire reason stability routes through
evaluatorq rather than orq.

`build_judge_fn` produces the `Callable[[str], Awaitable[Prediction]]` that
`run_jury` calls once per repetition (the `str` it passes is the panel model
id). `run_jury_for_row` is the per-row job the stability step fans out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from evaluatorq.common.judge import EvaluatorResponsePayload, _strip_code_fences, render_template
from evaluatorq.common.jury import Prediction, VerdictKind, run_jury
from evaluatorq.common.llm_call import execute_chat_completion
from evaluatorq.common.llm_client import resolve_llm_client

from lib.orq_client import tls_verify

# Mirror of orq's boolean `explanation_and_value` contract. orq puts the whole
# rubric in a single user message (no system turn) and pins the output shape with
# structured output, so we do the same: the judge sees ONLY the rendered rubric,
# and the {explanation, value} contract is enforced by `response_format` below
# rather than a hand-written system prompt. `explanation` is listed first so the
# model emits its reasoning before the verdict. We use a json_schema (not a tool
# call) because some judge models leak forced tool calls (project memory:
# local-judge response_format).
JUDGE_RESPONSE_FORMAT: dict[str, Any] = {
    'type': 'json_schema',
    'json_schema': {
        'name': 'evaluator_verdict',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'explanation': {
                    'type': 'string',
                    'description': 'Reasoning, written BEFORE the verdict.',
                },
                'value': {
                    'type': 'boolean',
                    'description': 'The verdict the rubric asks for (true / false).',
                },
            },
            'required': ['explanation', 'value'],
            'additionalProperties': False,
        },
    },
}

# Trailing/standalone boolean token. Tool-capable judges (e.g. glm-5.2 via the
# orq router) ignore `response_format: json_schema` and instead follow the
# judge prompt's literal free-text contract ("explanation, value"), emitting
# plain text that ends in True/False. We accept both.
_BOOL_TOKEN = re.compile(r'\b(true|false)\b', re.IGNORECASE)
_VALUE_LABEL = re.compile(r'(?:^|\n)\s*(?:value|verdict|answer)\s*[:=]\s*', re.IGNORECASE)


def _clean_explanation(text: str, *spans: tuple[int, int]) -> str:
    """Drop the given char spans (label + verdict token) and normalise space.

    Removes only the scaffolding so the explanation never carries the raw
    "Value: true" the caller anchored on.
    """
    out = text
    for start, end in sorted(spans, reverse=True):
        out = out[:start] + ' ' + out[end:]
    return ' '.join(out.split()).strip()


def parse_verdict(raw: str) -> EvaluatorResponsePayload:
    """Parse a judge completion into a verdict, tolerant of non-JSON output.

    First tries the strict JSON contract (`response_format: json_schema`),
    reusing evaluatorq's fence-stripper (which correctly ignores a ``` that
    appears *inside* an explanation string). If the model ignored the contract
    and returned free text — as the judge prompt literally asks for
    ("explanation BEFORE the value") — we recover the boolean:

    - If a "Value:"/"Verdict:"/"Answer:" label is present, take the boolean that
      follows a label (last such pair, since the verdict is emitted last). This
      keeps trailing prose ("...which would be false otherwise") from inverting
      a labelled verdict.
    - If no label is followed by a boolean, fall back to the last boolean token
      anywhere — so a bare "It is true." (label absent or empty) still parses
      rather than becoming a failed repetition.

    The explanation has the label + verdict scaffolding stripped out. Raises
    ValueError only when no boolean exists at all, so the caller records a
    failed repetition rather than a silent wrong answer.
    """
    text = _strip_code_fences((raw or '').strip()).strip()
    try:
        return EvaluatorResponsePayload.model_validate_json(text)
    except Exception:  # noqa: BLE001 — fall through to free-text parsing
        pass

    # Prefer a boolean that follows a label; the verdict is emitted last, so the
    # last label→boolean pair wins.
    label: re.Match[str] | None = None
    verdict: re.Match[str] | None = None
    for lm in _VALUE_LABEL.finditer(text):
        bm = _BOOL_TOKEN.search(text, lm.end())
        if bm is not None:
            label, verdict = lm, bm

    if verdict is None:  # no labelled boolean — take the last boolean anywhere
        matches = list(_BOOL_TOKEN.finditer(text))
        if not matches:
            raise ValueError(f'no boolean verdict found in judge output: {text[:200]!r}')
        verdict = matches[-1]

    value = verdict.group(1).lower() == 'true'
    spans = [(verdict.start(), verdict.end())]
    if label is not None:
        spans.append((label.start(), label.end()))
    explanation = _clean_explanation(text, *spans)
    return EvaluatorResponsePayload(value=value, explanation=explanation)


@dataclass
class JudgeSpec:
    """Everything needed to reconstruct the audited judge for one datapoint."""

    prompt_template: str
    replacements: dict[str, Any]
    temperature: float | None
    timeout_s: float = 120.0


def make_replacements(variables: list[str], row: dict[str, Any]) -> dict[str, Any]:
    """Map each declared judge variable to the trace row's value.

    Evaluators name their variables differently (`log.input`/`log.output`,
    `query`/`output`, `input`/`response`, ...). We match by suffix so the same
    code serves any single-judge boolean evaluator: anything ending in `input`
    or `query` takes the latest user prompt; `output`/`response` takes the
    assistant output; `messages` takes the serialised prior turns. Variables we
    can't map are left out — evaluatorq's `render_template` keeps an unmatched
    `{{var}}` literal rather than blanking it, so nothing silently vanishes.
    """
    query = row.get('query', '') or ''
    output = row.get('output', '') or ''
    messages = row.get('messages')
    repl: dict[str, Any] = {}
    for var in variables:
        leaf = var.split('.')[-1].strip().lower()
        if leaf in {'input', 'query', 'prompt'}:
            repl[var] = query
        elif leaf in {'output', 'response', 'completion', 'answer'}:
            repl[var] = output
        elif leaf in {'messages', 'history', 'conversation'}:
            repl[var] = messages if isinstance(messages, str) else _stringify(messages)
        elif leaf in {'reference', 'expected', 'expected_output'}:
            repl[var] = row.get('reference', '') or ''
    return repl


_TRANSIENT_MARKERS = ('429', 'rate limit', 'timeout', 'timed out', 'http2', 'too many requests', 'overloaded')


def _is_transient(exc: BaseException) -> bool:
    """True for retryable judge-call failures: rate limits, timeouts, upstream 5xx.

    The orq router fronts the actual provider (z.ai for glm-5.2), so a provider
    429/timeout can surface as an openai exception OR as a generic error whose
    message carries the upstream status. We match both. Parse failures
    (ValueError from parse_verdict) are deterministic and never retried.
    """
    try:
        import openai

        if isinstance(
            exc,
            (
                openai.RateLimitError,
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.InternalServerError,
            ),
        ):
            return True
        if isinstance(exc, openai.APIStatusError) and exc.status_code in (429, 500, 502, 503, 504):
            return True
    except Exception:  # noqa: BLE001 — openai always importable here; defensive
        pass
    if isinstance(exc, ValueError):
        return False
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


def _stringify(messages: Any) -> str:
    if not messages:
        return ''
    if isinstance(messages, list):
        lines = []
        for m in messages:
            if isinstance(m, dict):
                lines.append(f'{m.get("role", "?")}: {m.get("content", "")}')
            else:
                lines.append(str(m))
        return '\n'.join(lines)
    return str(messages)


def build_judge_fn(
    spec: JudgeSpec, client: Any
) -> Callable[[str], Awaitable[Prediction]]:
    """Return a `judge_fn(model) -> Prediction` bound to one datapoint."""
    prompt = render_template(spec.prompt_template, spec.replacements)
    # Emulate orq: the rubric alone in a single user message, no system turn.
    messages = [{'role': 'user', 'content': prompt}]

    async def judge_fn(model: str) -> Prediction:
        try:
            # Retry only the model call, and only on transient provider errors
            # (rate limit / timeout / upstream 5xx). Parsing is outside the
            # retry so a malformed completion is recorded, not retried.
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(_is_transient),
                stop=stop_after_attempt(5),
                wait=wait_exponential_jitter(initial=2, max=30),
                reraise=True,
            ):
                with attempt:
                    response, usage = await execute_chat_completion(
                        client=client,
                        model=model,
                        messages=messages,
                        span=None,
                        timeout_s=spec.timeout_s,
                        temperature=spec.temperature,
                        response_format=JUDGE_RESPONSE_FORMAT,
                    )
            raw = response.choices[0].message.content or '{}'
            payload = parse_verdict(raw)
            return Prediction(
                value=payload.value,
                explanation=payload.explanation,
                token_usage=usage,
                abstained=payload.abstain,
            )
        except Exception as exc:  # noqa: BLE001 — recorded as a failed repetition
            return Prediction(error=f'{type(exc).__name__}: {exc}')

    return judge_fn


async def run_jury_for_row(
    spec: JudgeSpec,
    judge_model: str,
    *,
    client: Any,
    repetitions: int,
) -> dict[str, Any]:
    """Run the single-judge panel `repetitions` times over one datapoint.

    Returns the raw per-repetition verdicts (`repetitions`), the count that
    errored (`repetitions_failed`), and evaluatorq's aggregated majority
    verdict (`value`). `propagate_errors=False` keeps a transient judge outage
    on one repetition from aborting the whole row.
    """
    deliberation = await run_jury(
        judge_fn=build_judge_fn(spec, client),
        panel=[judge_model],  # single-judge "panel"
        repetitions=repetitions,
        verdict_kind=VerdictKind.CATEGORICAL,  # boolean Pass/Fail (no BINARY kind)
        propagate_errors=False,
    )
    vote = deliberation.jury.votes[0]
    return {
        # evaluatorq marks an all-failed vote success=False and carries the
        # underlying judge error (e.g. a router 500) on vote.error. Propagate
        # both so callers never mistake an all-None row for a real verdict.
        'success': vote.success,
        'error': vote.error,
        'repetitions': list(vote.repetitions),
        'repetitions_failed': vote.repetitions_failed,
        'value': vote.value,
        # run_jury collapses the N rationales to one representative for the
        # majority class. We keep that for the annotation queue's "what did the
        # judge say" panel; per-repetition rationales are not exposed by the
        # jury layer, a deliberate V1 simplification.
        'explanation': vote.explanation,
    }


def make_judge_client() -> Any:
    """An AsyncOpenAI client for the reconstructed judge — orq router or OpenAI.

    Default: route judge calls through the orq router (``ORQ_API_KEY``), matching
    the production judge. Set ``JUDGE_DIRECT_OPENAI=1`` to instead call the real
    OpenAI API (``OPENAI_API_KEY``). ``ORQ_API_KEY`` may stay set — the
    trace/evaluator fetch steps still need it; only the judge calls re-route.
    Caveat: only flip a judge to OpenAI when its model genuinely IS an OpenAI model
    (e.g. ``gpt-5-mini``). Pointing a non-OpenAI judge (``glm-5.2``, gemini, ...)
    at OpenAI changes the judge, not just the route, so
    ``evaluator.json["judge_model"]`` must already be an OpenAI-routable slug.

    The direct branch pins ``https://api.openai.com/v1`` explicitly: the OpenAI
    SDK otherwise reads the ambient ``OPENAI_BASE_URL`` from the env, which in this
    repo points at the local llama.cpp judge — not what direct-OpenAI mode means.
    A dedicated ``JUDGE_OPENAI_BASE_URL`` still allows an intentional override
    (Azure/proxy) without disturbing ``OPENAI_BASE_URL`` used elsewhere.

    On Windows conda Pythons the default httpx SSL context aborts the process with
    ``OPENSSL_Uplink(...): no OPENSSL_Applink`` (project memory: the repo's
    established workaround is an httpx client with ``verify=False``). We apply that
    on win32 for both branches and keep TLS verification on everywhere else; the
    client is built here because ``resolve_llm_client`` exposes no ``http_client``
    hook. Any other env falls back to the shared resolver.
    """
    import os
    import sys

    direct_openai = os.environ.get('JUDGE_DIRECT_OPENAI', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    if direct_openai:
        openai_api_key = os.environ.get('OPENAI_API_KEY')
        if not openai_api_key:
            raise RuntimeError(
                'JUDGE_DIRECT_OPENAI is set but OPENAI_API_KEY is missing. Set '
                'OPENAI_API_KEY (optionally OPENAI_BASE_URL), or unset JUDGE_DIRECT_OPENAI '
                'to route judge calls through the orq router.'
            )
        from openai import AsyncOpenAI

        base_url = os.environ.get('JUDGE_OPENAI_BASE_URL', 'https://api.openai.com/v1')
        kwargs: dict[str, Any] = {'api_key': openai_api_key, 'base_url': base_url}
        if sys.platform == 'win32':
            import httpx

            kwargs['http_client'] = httpx.AsyncClient(verify=tls_verify())  # noqa: S501
        return AsyncOpenAI(**kwargs)

    orq_api_key = os.environ.get('ORQ_API_KEY')
    if sys.platform == 'win32' and orq_api_key:
        import httpx
        from openai import AsyncOpenAI

        host = os.environ.get('ORQ_BASE_URL', 'https://my.orq.ai').rstrip('/')
        return AsyncOpenAI(
            api_key=orq_api_key,
            base_url=f'{host}/v3/router',
            http_client=httpx.AsyncClient(verify=tls_verify()),  # noqa: S501
        )
    return resolve_llm_client().client
