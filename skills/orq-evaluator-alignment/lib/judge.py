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

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from evaluatorq.common.judge import EvaluatorResponsePayload, render_template
from evaluatorq.common.jury import Prediction, VerdictKind, run_jury
from evaluatorq.common.llm_call import execute_chat_completion
from evaluatorq.common.llm_client import resolve_llm_client

from lib.content import field_for_variable, stringify_messages
from lib.orq_client import tls_verify


def _strip_code_fences(text: str) -> str:
    """Strip a surrounding ```lang … ``` markdown fence, if present.

    Vendored (RES-978): evaluatorq stopped exporting its private
    ``_strip_code_fences``, which broke this module's import (and every test that
    loads it). Behaviour matches what parse_verdict relies on: only an *outer*
    fence is removed — a ``` that appears inside a JSON string value is left
    untouched, so a fenced explanation never truncates the payload.
    """
    s = (text or '').strip()
    if not s.startswith('```'):
        return text  # no outer fence — don't disturb inner ``` in string values
    lines = s.split('\n')
    lines = lines[1:]  # drop the opening ```lang line
    if lines and lines[-1].strip().startswith('```'):
        lines = lines[:-1]  # drop the closing fence
    return '\n'.join(lines).strip()

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


def build_response_format(output_type: str, labels: Sequence[str] | None = None) -> dict[str, Any]:
    """The structured-output contract to request, per output type (RES-978 §5.3).

    boolean → `value: boolean`; categorical → `value: string` constrained to an
    `enum` of the K declared labels; numeric → `value: number`. `explanation` is
    always first so the model reasons before committing. Best-effort only — some
    judges ignore json_schema and free-text the verdict, which the parsers recover.

    String is the exception: an orq string evaluator emits ONLY `value` (a
    free-form string) with no `explanation` field, so its schema has just `value`.
    """
    kind = (output_type or 'boolean').strip().lower()
    if kind == 'boolean':
        return JUDGE_RESPONSE_FORMAT
    if kind == 'string':
        return {
            'type': 'json_schema',
            'json_schema': {
                'name': 'evaluator_verdict',
                'strict': True,
                'schema': {
                    'type': 'object',
                    'properties': {'value': {'type': 'string', 'description': 'The free-form string verdict.'}},
                    'required': ['value'],
                    'additionalProperties': False,
                },
            },
        }
    if kind == 'categorical':
        value_schema: dict[str, Any] = {'type': 'string', 'description': 'Exactly one of the allowed labels.'}
        if labels:
            value_schema['enum'] = list(labels)
    elif kind in {'number', 'numeric'}:
        value_schema = {'type': 'number', 'description': 'The numeric score the rubric asks for.'}
    else:
        raise ValueError(f'unknown output_type {output_type!r} (expected boolean | categorical | number | string)')
    return {
        'type': 'json_schema',
        'json_schema': {
            'name': 'evaluator_verdict',
            'strict': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'explanation': {'type': 'string', 'description': 'Reasoning, written BEFORE the verdict.'},
                    'value': value_schema,
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
_VALUE_LABEL = re.compile(
    r'(?:^|\n)\s*(?:value|verdict|answer|score|rating)\s*[:=]\s*', re.IGNORECASE
)


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


# ── Categorical / numeric canonicalization (RES-978 §4a) ───────────────────────
# instability.py only ever sees clean, canonical verdicts, so all the messy
# "what did the judge actually emit" logic lives here. A completion that parses
# but is off-contract (a non-K label, a non-number, a number outside the declared
# scale) is a SURFACED `wrong_output_type` signal — recorded and shown to the
# human as a target for eval improvement — never silently scored or bucketed.

_WRONG_OUTPUT_TYPE = 'wrong_output_type'
_OK = 'ok'
# First numeric token: optional sign, digits, optional decimal via '.' or ','.
_NUM_TOKEN = re.compile(r'[-+]?\d+(?:[.,]\d+)?')


@dataclass
class ParsedVerdict:
    """A canonical categorical/numeric verdict, or an off-contract signal.

    `status` is `ok` (`value` is a clean canonical verdict — the declared label
    string, or a float) or `wrong_output_type` (`value` is None; the completion
    parsed but violated the contract).
    """

    status: str
    value: str | float | None
    explanation: str = ''


def _loads_obj(text: str) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object from a fence-stripped completion."""
    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001 — free text is the expected fallback
        return None
    return obj if isinstance(obj, dict) else None


def _freetext_candidate(text: str) -> str:
    """The label-ish token a free-text judge emitted.

    Prefers the text after the last `Value:`/`Verdict:`/`Answer:` label (the
    verdict is emitted last); otherwise the last non-empty line.
    """
    label: re.Match[str] | None = None
    for lm in _VALUE_LABEL.finditer(text):
        label = lm
    if label is not None:
        tail = text[label.end():].strip().splitlines()
        if tail and tail[0].strip():
            return tail[0].strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else text.strip()


def parse_categorical(raw: str, labels: Sequence[str]) -> ParsedVerdict:
    """Canonicalize a categorical completion: casefold+strip, exact one-of-K.

    Matches the emitted label against the declared set case-insensitively and
    returns the DECLARED label (so K-counting groups on the contract's own
    spelling). No fuzzy/substring matching — merging `abuse` with `abuse (severe)`
    would corrupt K and inflate entropy far worse than a visible miss — so a
    non-match is `wrong_output_type`, never a new bucket.
    """
    text = _strip_code_fences((raw or '').strip()).strip()
    obj = _loads_obj(text)
    if obj is not None and 'value' in obj:
        candidate = str(obj.get('value', ''))
        explanation = str(obj.get('explanation', '') or '')
    else:
        candidate = _freetext_candidate(text)
        explanation = ''
    key = candidate.casefold().strip()
    for declared in labels:
        if declared.casefold().strip() == key:
            return ParsedVerdict(_OK, declared, explanation)
    return ParsedVerdict(_WRONG_OUTPUT_TYPE, None, explanation or candidate)


# Phrases that name the *scale* rather than the verdict: "on a scale of 1 to 5",
# "4 out of 5", "3/5". Scrubbed before the verdict is read so a scale endpoint can
# never be mistaken for the score.
_SCALE_MENTION = re.compile(
    r'(?:\bout\s+of\b|\bof\b|\bto\b|/)\s*[-+]?\d+(?:[.,]\d+)?', re.IGNORECASE
)


def _verdict_number_token(text: str) -> tuple[str | None, str]:
    """The numeric token a free-text judge emitted as its verdict.

    Reads the verdict the way `parse_categorical` does — `_freetext_candidate`,
    i.e. after the last `Value:`/`Score:` label or on the last non-empty line —
    rather than taking the first number anywhere in the completion.
    `'On a scale of 1 to 5, I give this a 4'` used to parse as **1.0** with status
    `ok`: inside the declared scale, so nothing downstream re-checked it, and the
    wrong score entered `mean`, `stdev` and the instability band.

    Scale mentions are scrubbed first, then the remaining token is the verdict.
    Returns ``(token, reason)``; on failure `token` is None and `reason` says why.
    A verdict line still carrying two different numbers after scrubbing is
    **ambiguous** and surfaced as off-contract — guessing between them is exactly
    how a wrong score gets scored silently.
    """
    for source in (_freetext_candidate(text), text):
        if not source:
            continue
        tokens = _NUM_TOKEN.findall(_SCALE_MENTION.sub(' ', source)) or _NUM_TOKEN.findall(source)
        if not tokens:
            continue
        if len({t.replace(',', '.') for t in tokens}) > 1:
            return None, f'ambiguous free-text number ({", ".join(tokens)}): {source[:160]}'
        return tokens[-1], ''
    return None, ''


def _json_number(obj: dict[str, Any] | None) -> float | None:
    """The `value` of a JSON completion as a float, or None if it isn't one.

    Accepts a QUOTED number (`{"value": "4"}`) as well as a bare one. Models quote
    numbers routinely, and requiring `isinstance(value, (int, float))` sent those
    completions down the free-text path — where the surrounding explanation's own
    digits made the verdict line ambiguous, and a judge that had answered correctly
    in the schema it was given was recorded as off-contract. Bools are excluded
    because `True` is an `int` in Python and a boolean verdict is not a score.
    """
    if obj is None or 'value' not in obj:
        return None
    value = obj['value']
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        token = value.strip()
        if token.count(',') == 1 and '.' not in token:
            token = token.replace(',', '.')
        try:
            return float(token)
        except ValueError:
            return None
    return None


def parse_numeric(raw: str, scale: tuple[float, float] | None = None) -> ParsedVerdict:
    """Canonicalize a numeric completion: the verdict token, `,`/`.` normalized.

    A single `,` with no `.` is a decimal separator (`3,4` → `3.4`); this does NOT
    disambiguate thousands separators (`3,400`), which bounded eval scores never
    take (§4a, documented limit). Not a number, or an ambiguous free-text verdict
    line → `wrong_output_type`; when a scale is set, a value outside
    `[scale_min, scale_max]` is off-contract too.
    """
    text = _strip_code_fences((raw or '').strip()).strip()
    obj = _loads_obj(text)
    explanation = ''
    json_number = _json_number(obj)
    if json_number is not None:
        number = json_number
        explanation = str(obj.get('explanation', '') or '')
    else:
        token, reason = _verdict_number_token(text)
        if token is None:
            return ParsedVerdict(_WRONG_OUTPUT_TYPE, None, (reason or text)[:200])
        if token.count(',') == 1 and '.' not in token:
            token = token.replace(',', '.')
        try:
            number = float(token)
        except ValueError:
            return ParsedVerdict(_WRONG_OUTPUT_TYPE, None, token)
    if scale is not None and not (scale[0] <= number <= scale[1]):
        return ParsedVerdict(_WRONG_OUTPUT_TYPE, None, explanation or str(number))
    return ParsedVerdict(_OK, number, explanation)


def parse_string(raw: str) -> ParsedVerdict:
    """Canonicalize a free-form string completion — the value IS the whole output.

    Unlike the other three types there is no `explanation` field: a string
    evaluator emits only `value`. We take `value` from a JSON object if the judge
    honored the `{value}` schema, otherwise the entire completion is the value.
    Canonicalize with casefold + strip + inner-whitespace collapse so trivial
    formatting differences don't read as instability under exact-match counting
    (`instability.string`). An empty result is `wrong_output_type` — a surfaced
    non-verdict, not a counted empty string.
    """
    text = _strip_code_fences((raw or '').strip()).strip()
    obj = _loads_obj(text)
    candidate = str(obj.get('value', '')) if obj is not None and 'value' in obj else text
    canonical = ' '.join(candidate.casefold().split())
    if not canonical:
        return ParsedVerdict(_WRONG_OUTPUT_TYPE, None, '')
    return ParsedVerdict(_OK, canonical, '')


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
    code serves any single-judge boolean evaluator, via the one suffix table in
    `lib.content` that the trace scanner also reverses with — a second copy here
    is how a value gets recovered into a field this renderer never reads back.
    Variables we can't map are left out — evaluatorq's `render_template` keeps an
    unmatched `{{var}}` literal rather than blanking it, so nothing silently
    vanishes.
    """
    repl: dict[str, Any] = {}
    for var in variables:
        field = field_for_variable(var)
        if field is None:
            continue
        if field == 'messages':
            repl[var] = stringify_messages(row.get('messages'))
        else:
            repl[var] = row.get(field, '') or ''
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


def build_judge_fn(
    spec: JudgeSpec,
    client: Any,
    *,
    output_type: str = 'boolean',
    labels: Sequence[str] | None = None,
    scale: tuple[float, float] | None = None,
) -> Callable[[str], Awaitable[Prediction]]:
    """Return a `judge_fn(model) -> Prediction` bound to one datapoint.

    The requested response format and the verdict parser are both chosen by
    `output_type` (§5.3). An off-contract completion — a non-K label, a
    non-number, or a number outside `scale` — becomes an ABSTAINED prediction: it
    produced output (so it is not an error / failed repetition) but it is not a
    scored verdict either (§4a). The boolean path is unchanged.
    """
    prompt = render_template(spec.prompt_template, spec.replacements)
    # Emulate orq: the rubric alone in a single user message, no system turn.
    messages = [{'role': 'user', 'content': prompt}]
    kind = (output_type or 'boolean').strip().lower()
    response_format = build_response_format(kind, labels)

    def _to_prediction(raw: str, usage: Any) -> Prediction:
        if kind == 'boolean':
            payload = parse_verdict(raw)  # raises on no verdict → failed repetition
            return Prediction(
                value=payload.value,
                explanation=payload.explanation,
                token_usage=usage,
                abstained=payload.abstain,
            )
        if kind == 'categorical':
            parsed = parse_categorical(raw, labels or [])
        elif kind == 'string':
            parsed = parse_string(raw)
        else:
            parsed = parse_numeric(raw, scale)
        if parsed.status == _OK:
            return Prediction(value=parsed.value, explanation=parsed.explanation, token_usage=usage)
        # Off-contract: abstain (surfaced, not scored, not a failure).
        return Prediction(
            abstained=True,
            explanation=f'{_WRONG_OUTPUT_TYPE}: {parsed.explanation}'.strip(),
            token_usage=usage,
        )

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
                        response_format=response_format,
                    )
            raw = response.choices[0].message.content or '{}'
            return _to_prediction(raw, usage)
        except Exception as exc:  # noqa: BLE001 — recorded as a failed repetition
            return Prediction(error=f'{type(exc).__name__}: {exc}')

    return judge_fn


def _count_off_contract(repetitions: list[Any], repetitions_failed: int) -> int:
    """Off-contract (wrong_output_type) repetition count.

    Abstained reps surface as `None` in the jury's `repetitions` list but are NOT
    in `repetitions_failed` (which counts errored calls only). So the off-contract
    tally is the `None` count minus the failures. Never negative.
    """
    n_none = sum(1 for r in repetitions if r is None)
    return max(0, n_none - repetitions_failed)


async def run_jury_for_row(
    spec: JudgeSpec,
    judge_model: str,
    *,
    client: Any,
    repetitions: int,
    output_type: str = 'boolean',
    labels: Sequence[str] | None = None,
    scale: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Run the single-judge panel `repetitions` times over one datapoint.

    Returns the raw per-repetition verdicts (`repetitions` — bool/str/float for
    the type, `None` for a failed or off-contract rep), the count that errored
    (`repetitions_failed`), the off-contract count split out of the Nones
    (`n_wrong_output_type`, §4a), and evaluatorq's aggregated verdict (`value`).
    `propagate_errors=False` keeps a transient judge outage on one repetition
    from aborting the whole row. The numeric type aggregates by mean, the others
    by plurality (`VerdictKind`).
    """
    kind = (output_type or 'boolean').strip().lower()
    verdict_kind = VerdictKind.NUMERIC if kind in {'number', 'numeric'} else VerdictKind.CATEGORICAL
    deliberation = await run_jury(
        judge_fn=build_judge_fn(spec, client, output_type=kind, labels=labels, scale=scale),
        panel=[judge_model],  # single-judge "panel"
        repetitions=repetitions,
        verdict_kind=verdict_kind,
        propagate_errors=False,
    )
    vote = deliberation.jury.votes[0]
    reps = list(vote.repetitions)
    return {
        # evaluatorq marks an all-failed vote success=False and carries the
        # underlying judge error (e.g. a router 500) on vote.error. Propagate
        # both so callers never mistake an all-None row for a real verdict.
        'success': vote.success,
        'error': vote.error,
        'repetitions': reps,
        'repetitions_failed': vote.repetitions_failed,
        # Off-contract reps abstained (surfaced as None but not errored), so split
        # them out of the Nones for the §4a signal / the usable-verdict floor.
        'n_wrong_output_type': _count_off_contract(reps, vote.repetitions_failed),
        'value': vote.value,
        'output_type': kind,
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
