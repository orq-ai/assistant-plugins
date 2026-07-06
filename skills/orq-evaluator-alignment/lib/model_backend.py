"""Pluggable text-completion backend for the recommend (step 8) and rewrite
(step 9) stages.

One interface — `await complete(prompt, *, system=None, ...)` returning
`{text, cost_usd}` — with four implementations selected by config:

- `claude_subagent`  shell out to `claude -p ... --output-format json` (per-call
  cost is read straight off the CLI's `total_cost_usd`); independent processes,
  so step 8 parallelism is just a bounded pool of these.
- `orq_deployment`   invoke a workspace deployment via the orq SDK.
- `anthropic_api`    direct Anthropic Messages API.
- `fake`             deterministic canned completions for tests — no `claude -p`.

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


class Backend(Protocol):
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> CompletionResult: ...


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


def get_backend(config: dict[str, Any]) -> Backend:
    """Construct the backend named by `config['backend']`."""
    name = config.get('backend', 'claude_subagent')
    model = config.get('backend_model', 'claude-opus-4-8')
    if name == 'claude_subagent':
        return ClaudeSubagentBackend(model=model)
    if name == 'anthropic_api':
        return AnthropicBackend(model=model)
    if name == 'orq_deployment':
        return OrqDeploymentBackend(deployment_key=config.get('backend_deployment_key', ''))
    if name == 'fake':
        return FakeBackend()
    raise ValueError(f'Unknown backend {name!r} (config.backend).')


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
