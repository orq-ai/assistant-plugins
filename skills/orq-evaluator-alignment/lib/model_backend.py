"""Pluggable text-completion backend for the recommend (step 8) and rewrite
(step 9) stages.

One interface — `await complete(prompt, *, system=None, ...)` returning
`{text, cost_usd}` — with five implementations selected by config:

- `orq_router`       **the default.** Any model in the workspace's router registry,
  reached over the OpenAI-compatible `/v3/router` endpoint with `ORQ_API_KEY` —
  the same credential and route the judge itself uses, so no second vendor account
  is involved. Cost is priced from the registry's own per-1K rates.
- `claude_subagent`  shell out to `claude -p ... --output-format json` (per-call
  cost is read straight off the CLI's `total_cost_usd`); independent processes,
  so step 8 parallelism is just a bounded pool of these. Needs the `claude` CLI on
  PATH, which a user running this skill from a non-Claude harness won't have.
- `orq_deployment`   invoke a workspace deployment via the orq SDK.
- `anthropic_api`    direct Anthropic Messages API.
- `fake`             deterministic canned completions for tests — no `claude -p`.

### Why `orq_router` is the default

The rewrite is a text transform, not an agentic task, so it does not need a coding
agent — and `claude_subagent` quietly assumed one was installed *and* logged in.
Routing through orq means the only credential in play is the one the skill already
requires. The cost is that the chosen model must actually be reachable on the
user's workspace: a model listed in `/v2/models` can still 403 for want of a
provider key. `preflight()` exists to turn that into a sentence the user can act
on, at the gate, rather than a stack trace mid-rewrite.

### The nested-variable hazard (orq_deployment only)

The meta-prompt embeds the audited *judge prompt* as the value of
`{{evaluator_prompt}}`, and PO2 embeds it as the prompt to rewrite. That judge
prompt itself contains `{{query}}` / `{{output}}` tokens. The string backends
(`claude_subagent`, `anthropic_api`) concatenate text, so those tokens stay
literal. But orq's deployment templating WOULD re-substitute them and, with no
value supplied, blank them — the model would then see an empty rubric. So the
`orq_deployment` backend passes every nested token back to itself
(`output={{output}}`) so the engine renders `{{output}}` unchanged. The string
backends ignore `variables` entirely.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass
from typing import Any, Protocol


_VAR_TOKEN = re.compile(r'\{\{\s*([^}]+?)\s*\}\}')

# Per-Mtoken USD pricing for the Anthropic backend cost estimate (input, output).
_ANTHROPIC_PRICES: dict[str, tuple[float, float]] = {
    'claude-opus-4-8': (5.0, 25.0),
    'claude-sonnet-4-6': (3.0, 15.0),
    'claude-haiku-4-5': (1.0, 5.0),
}


@dataclass
class CompletionResult:
    text: str
    cost_usd: float


class BackendUnavailable(RuntimeError):
    """The backend is configured but cannot be used — with a human-readable why.

    Raised by `preflight()` so step 9's gate can offer the user a choice (different
    model, or the Claude subagent) instead of surfacing a provider stack trace.
    """


class Backend(Protocol):
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> CompletionResult: ...


# ── orq_router (default) ─────────────────────────────────────────────────────
class OrqRouterBackend:
    """Any workspace model, over the OpenAI-compatible orq router.

    A *string* backend like `claude_subagent` / `anthropic_api`: it concatenates
    text and never templates it, so the embedded judge prompt's own `{{query}}` /
    `{{output}}` tokens survive verbatim. (The nested-variable hazard is specific to
    `orq_deployment`, which renders its inputs.) `variables` is ignored here for the
    same reason.
    """

    def __init__(self, model: str, *, max_tokens: int = 8192, timeout_s: float = 180.0) -> None:
        if not model:
            raise RuntimeError("backend='orq_router' needs a model (config `backend_model`).")
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self._prices: tuple[float, float] | None = None
        self._prices_loaded = False
        api_key = os.environ.get('ORQ_API_KEY')
        if not api_key:
            raise BackendUnavailable(
                "backend='orq_router' needs ORQ_API_KEY — the same key the trace and "
                'evaluator steps already use. Set it, or switch to another backend.'
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("backend='orq_router' needs the `openai` package.") from exc
        import httpx

        host = os.environ.get('ORQ_BASE_URL', 'https://my.orq.ai').rstrip('/')
        # verify=False on Windows only: the conda OpenSSL aborts the process on some
        # cert chains. Same workaround, and same reasoning, as `judge.make_judge_client`.
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=f'{host}/v3/router',
            http_client=httpx.AsyncClient(verify=_tls_verify(), timeout=timeout_s),  # noqa: S501
        )

    async def preflight(self) -> None:
        """One minimal call, so an unusable model fails at the gate with a reason.

        A model can appear in `GET /v2/models` with `enabled: true` and still refuse
        at call time — that field means "listed in the registry", not "this workspace
        has a provider key for it". The only reliable check is to actually call it,
        which is why this sends a 1-token completion rather than reading a flag.
        """
        try:
            await self._client.chat.completions.create(
                model=self.model,
                messages=[{'role': 'user', 'content': 'ok'}],
                max_tokens=1,
            )
        except Exception as exc:  # noqa: BLE001 — translated, not swallowed
            raise BackendUnavailable(_router_failure_reason(self.model, exc)) from exc

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> CompletionResult:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append({'role': 'user', 'content': prompt})
        try:
            resp = await self._client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=self.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            raise BackendUnavailable(_router_failure_reason(self.model, exc)) from exc
        text = (resp.choices[0].message.content or '') if resp.choices else ''
        return CompletionResult(text=text, cost_usd=await self._cost(resp))

    async def _cost(self, resp: Any) -> float:
        """Price the call from the registry's own per-1K rates (best effort → 0.0).

        Fetched once and cached. Reporting 0.0 on a lookup failure is deliberate:
        the run must not fail over a cost annotation, and a wrong number would be
        worse than a missing one.
        """
        usage = getattr(resp, 'usage', None)
        if usage is None:
            return 0.0
        if not self._prices_loaded:
            self._prices_loaded = True
            self._prices = await _fetch_router_prices(self.model)
        if self._prices is None:
            return 0.0
        pin, pout = self._prices
        return (getattr(usage, 'prompt_tokens', 0) / 1000) * pin + (
            getattr(usage, 'completion_tokens', 0) / 1000
        ) * pout


def _tls_verify() -> bool:
    """Off on Windows only (conda OpenSSL Applink abort). Mirrors `orq_client.tls_verify`."""
    import sys

    return sys.platform != 'win32'


def _router_failure_reason(model: str, exc: Exception) -> str:
    """Turn a router exception into something the user can act on at the gate."""
    status = getattr(exc, 'status_code', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
    if status in (401, 403):
        return (
            f"'{model}' is not available on this workspace (HTTP {status}). Usually this "
            'means no provider key is configured for it — enable the model in orq under '
            'Settings → Models, pick a different model, or use the Claude subagent backend.'
        )
    if status == 404:
        return (
            f"'{model}' was not found by the router (HTTP 404). Check the slug: it must be "
            "the provider-qualified id (e.g. 'deepseek/deepseek-v4-pro'), which is the "
            "`refId` in GET /v2/models — not the shorter display alias."
        )
    if status == 429:
        return f"'{model}' is rate-limited right now (HTTP 429). Retry, or pick another model."
    return f'{model}: {type(exc).__name__}: {exc}'


async def _fetch_router_prices(model: str) -> tuple[float, float] | None:
    """(input, output) USD per 1K tokens for `model`, from GET /v2/models."""
    try:
        import httpx

        api_key = os.environ.get('ORQ_API_KEY')
        host = os.environ.get('ORQ_API_BASE_URL', 'https://api.orq.ai').rstrip('/')
        async with httpx.AsyncClient(
            base_url=host, headers={'Authorization': f'Bearer {api_key}'},
            verify=_tls_verify(), timeout=30,  # noqa: S501
        ) as client:
            resp = await client.get('/v2/models', params={'limit': 1000})
            if resp.status_code >= 400:
                return None
            payload = resp.json()
            items = payload if isinstance(payload, list) else payload.get('data') or []
        for m in items:
            if isinstance(m, dict) and m.get('refId') == model:
                pin, pout = m.get('input_cost'), m.get('output_cost')
                if pin is None or pout is None:
                    return None
                return (float(pin), float(pout))
    except Exception:  # noqa: BLE001 — cost is a courtesy, never a failure mode
        return None
    return None


# ── claude_subagent ──────────────────────────────────────────────────────────
class ClaudeSubagentBackend:
    """Shell out to `claude -p` as a pure, side-effect-free text transform."""

    def __init__(self, model: str = 'claude-opus-4-8') -> None:
        # `_run` must go through the shell on Windows: `claude` resolves to
        # `claude.CMD`, and CreateProcess cannot launch a .cmd/.bat directly
        # (shell=False raises WinError 193), so a shell is unavoidable and
        # `model` reaches cmd.exe on the command line. It's operator config, not
        # remote input, but reject shell metacharacters so a stray `&`/`|`/`^`
        # can't break or inject the command line. On POSIX shell=False already
        # makes this moot; the guard is the control for the Windows path.
        if not re.fullmatch(r'[\w.:/-]+', model):
            raise ValueError(f'invalid model slug {model!r}: expected letters, digits, and . : / -')
        self.model = model
        self.exe = shutil.which('claude')
        if self.exe is None:
            raise RuntimeError(
                "backend='claude_subagent' but the `claude` CLI is not on PATH."
            )

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> CompletionResult:
        full = f'{system}\n\n{prompt}' if system else prompt
        return await asyncio.to_thread(self._run, full)

    def _run(self, full_prompt: str) -> CompletionResult:
        import os
        import subprocess

        cmd = [
            self.exe, '-p',
            '--output-format', 'json',
            '--model', self.model,
            '--allowedTools', '',
        ]
        # On Windows `claude` resolves to `claude.CMD`, which CreateProcess can't
        # launch directly — it must go through the shell (cmd.exe /c). Python
        # joins the list via list2cmdline when shell=True, so stdin piping of the
        # (large) meta-prompt is unaffected.
        proc = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            encoding='utf-8',
            shell=(os.name == 'nt'),
        )
        if proc.returncode != 0:
            raise RuntimeError(f'`claude -p` exited {proc.returncode}: {proc.stderr[:500]}')
        payload = json.loads(proc.stdout)
        if payload.get('is_error'):
            raise RuntimeError(f'`claude -p` reported is_error: {payload.get("result")!r}')
        return CompletionResult(
            text=payload.get('result', ''),
            cost_usd=float(payload.get('total_cost_usd') or 0.0),
        )


# ── anthropic_api ────────────────────────────────────────────────────────────
class AnthropicBackend:
    def __init__(self, model: str = 'claude-opus-4-8', max_tokens: int = 4096) -> None:
        self.model = model
        self.max_tokens = max_tokens
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("backend='anthropic_api' needs the `anthropic` package.") from exc
        self._client = AsyncAnthropic()

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> CompletionResult:
        kwargs: dict[str, Any] = {
            'model': self.model,
            'max_tokens': self.max_tokens,
            'messages': [{'role': 'user', 'content': prompt}],
        }
        if system:
            kwargs['system'] = system
        resp = await self._client.messages.create(**kwargs)
        text = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')
        return CompletionResult(text=text, cost_usd=self._cost(resp))

    def _cost(self, resp: Any) -> float:
        prices = _ANTHROPIC_PRICES.get(self.model)
        usage = getattr(resp, 'usage', None)
        if not prices or usage is None:
            return 0.0
        pin, pout = prices
        return (usage.input_tokens / 1e6) * pin + (usage.output_tokens / 1e6) * pout


# ── orq_deployment ───────────────────────────────────────────────────────────
class OrqDeploymentBackend:
    """Invoke a workspace deployment, self-referencing nested template tokens."""

    def __init__(self, deployment_key: str) -> None:
        if not deployment_key:
            raise RuntimeError("backend='orq_deployment' needs backend_deployment_key.")
        self.deployment_key = deployment_key
        try:
            from orq_ai_sdk import Orq
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("backend='orq_deployment' needs the `orq-ai-sdk` package.") from exc
        self._orq = Orq(api_key=os.environ['ORQ_API_KEY'])

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> CompletionResult:
        # Defend the embedded judge prompt's own `{{tokens}}` from the deployment
        # templating engine: map each one to itself so it renders unchanged.
        inputs = _self_reference_tokens(f'{system or ""}\n{prompt}')
        if variables:
            inputs.update(variables)
        inputs['input_instructions'] = system or ''
        inputs['prompt'] = prompt
        resp = await asyncio.to_thread(
            self._orq.deployments.invoke,
            key=self.deployment_key,
            inputs=inputs,
        )
        text = _extract_orq_text(resp)
        return CompletionResult(text=text, cost_usd=0.0)


# ── fake ─────────────────────────────────────────────────────────────────────
class FakeBackend:
    """Deterministic canned completions for tests.

    Default behaviour covers the two prompt shapes the pipeline issues:
    a meta-prompt call (returns valid `{reasoning, recommendation}` JSON) and a
    PO2 rewrite call (identity stub — echoes the embedded `<prompt>` back so the
    variable-preservation check passes). Inject `responder` to override.
    """

    def __init__(self, responder: Any = None) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> CompletionResult:
        self.calls.append({'prompt': prompt, 'system': system})
        if self.responder is not None:
            return CompletionResult(text=self.responder(prompt, system), cost_usd=0.0)
        if '<input_instructions>' in prompt or (system and 'prompt engineer specializing' in system):
            return CompletionResult(text=_echo_inner_prompt(prompt), cost_usd=0.0)
        return CompletionResult(
            text=json.dumps(
                {
                    'reasoning': 'FAKE: canned reasoning for the smoke test.',
                    'recommendation': 'FAKE: preserve the rubric; clarify borderline cases generally.',
                }
            ),
            cost_usd=0.0,
        )


# Each backend's own default model, used when `backend_model` is blank. Keeping
# `backend_model` optional means switching `backend` alone is a valid edit — the old
# single-default arrangement silently handed a Claude model id to the router (and
# vice versa) whenever someone changed one key without the other.
_DEFAULT_MODELS = {
    'orq_router': 'deepseek/deepseek-v4-pro',
    'claude_subagent': 'claude-opus-4-8',
    'anthropic_api': 'claude-opus-4-8',
}

DEFAULT_BACKEND = 'orq_router'


def get_backend(config: dict[str, Any]) -> Backend:
    """Construct the backend named by `config['backend']`."""
    name = config.get('backend') or DEFAULT_BACKEND
    model = config.get('backend_model') or _DEFAULT_MODELS.get(name, '')
    if name == 'orq_router':
        return OrqRouterBackend(model=model)
    if name == 'claude_subagent':
        return ClaudeSubagentBackend(model=model)
    if name == 'anthropic_api':
        return AnthropicBackend(model=model)
    if name == 'orq_deployment':
        return OrqDeploymentBackend(deployment_key=config.get('backend_deployment_key', ''))
    if name == 'fake':
        return FakeBackend()
    raise ValueError(f'Unknown backend {name!r} (config.backend).')


def describe_backend(config: dict[str, Any]) -> str:
    """One line naming the backend + model actually in play, for the step-9 gate."""
    name = config.get('backend') or DEFAULT_BACKEND
    if name == 'orq_deployment':
        return f'orq_deployment (deployment {config.get("backend_deployment_key") or "<unset>"!r})'
    model = config.get('backend_model') or _DEFAULT_MODELS.get(name, '')
    return f'{name} ({model})' if model else name


def _self_reference_tokens(text: str) -> dict[str, str]:
    return {tok: '{{' + tok + '}}' for tok in {m.group(1) for m in _VAR_TOKEN.finditer(text)}}


def _echo_inner_prompt(prompt: str) -> str:
    """Pull the `<prompt>...</prompt>` body out of a PO2 user message (identity stub)."""
    m = re.search(r'<prompt>(.*)</prompt>', prompt, re.DOTALL)
    return m.group(1).strip() if m else prompt


def _extract_orq_text(resp: Any) -> str:
    # The orq SDK response shape has drifted across versions; probe the common
    # nestings. If none match, RAISE — returning str(resp) would feed the SDK
    # object's repr downstream as a "recommendation"/"rewritten prompt", which
    # silently poisons the output instead of surfacing the shape drift.
    for path in (
        lambda r: r.choices[0].message.content,
        lambda r: r['choices'][0]['message']['content'],
        lambda r: r.content,
    ):
        try:
            val = path(resp)
            if isinstance(val, str) and val:
                return val
        except Exception:  # noqa: BLE001, S110
            pass
    raise ValueError(f'orq_deployment: could not locate text in response (shape drift): {resp!r}')
