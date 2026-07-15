"""Async client for the orq.ai endpoints the alignment skill touches.

Covers four routes:
- GET  /v2/evaluators/{id}              — fetch the evaluator under audit (step 1)
- POST /v2/traces/v3oql                 — query traces by evaluator (step 2, hop 1)
- GET  /v2/traces/{trace_id}/v3spans    — per-trace spans (step 2, hop 2)
- POST /v2/evaluators                   — create the rewritten evaluator (step 9b)

Lifted and trimmed from the validated `evaluator_alignment.client.OrqEvalsClient`.
TLS verification is disabled *only on Windows*, where the bundled OpenSSL aborts
the process on some cert chains (project memory: OpenSSL Applink crash). On
macOS/Linux verification stays on — the API key travels on these connections.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

DEFAULT_BASE_URL = 'https://api.orq.ai'

# Matches `{{ var.path }}` template tokens in a judge prompt. The captured group
# is the trimmed variable path (e.g. `log.input`, `query`, `output`).
_VAR_TOKEN = re.compile(r'\{\{\s*([^}]+?)\s*\}\}')


def tls_verify() -> bool:
    """Whether httpx should verify TLS certificates.

    Off only on Windows, whose bundled OpenSSL aborts the process on some cert
    chains (``OPENSSL_Uplink ... no OPENSSL_Applink``). The orq API key rides
    these connections, so verification stays ON everywhere else. Single source of
    truth for the policy — imported by the judge client in ``judge.py`` too.
    """
    return sys.platform != 'win32'


def _envelope_list(payload: Any, *keys: str) -> list[Any]:
    """Peel a list out of an orq response: bare list, or {"data"|...: [...]}."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _envelope_dict(payload: Any) -> dict[str, Any] | None:
    """Peel a dict out of an orq response: unwrap a {"data": {...}} envelope.

    Returns the inner dict if enveloped, the payload itself if it's a bare dict,
    or None if the payload isn't a dict at all.
    """
    if not isinstance(payload, dict):
        return None
    inner = payload.get('data')
    return inner if isinstance(inner, dict) else payload


def extract_template_variables(prompt: str) -> list[str]:
    """Return the ordered, de-duplicated set of `{{...}}` variables in a prompt."""
    seen: dict[str, None] = {}
    for m in _VAR_TOKEN.finditer(prompt or ''):
        seen.setdefault(m.group(1), None)
    return list(seen)


@dataclass
class EvaluatorConfig:
    """The audited evaluator's config, normalised for downstream steps."""

    id: str
    key: str
    prompt: str
    judge_model: str
    output_type: str
    variables: list[str]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CreateResult:
    id: str
    key: str
    raw: dict[str, Any]


class OrqClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
    ) -> None:
        key = api_key or os.getenv('ORQ_API_KEY')
        if not key:
            raise RuntimeError('ORQ_API_KEY is not set (env or constructor arg).')
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            # Verification stays on everywhere except Windows (OpenSSL Applink
            # crash — see module docstring). The API key rides these requests.
            verify=tls_verify(),  # noqa: S501
            headers={
                'Authorization': f'Bearer {key}',
                'Content-Type': 'application/json',
            },
        )

    async def __aenter__(self) -> OrqClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    # ── Step 1 ───────────────────────────────────────────────────────────────
    async def get_evaluator(self, evaluator_id: str) -> EvaluatorConfig:
        """Fetch and normalise the evaluator under audit.

        A 404 is ambiguous: the id is wrong OR the evaluator lives in a project
        this API key cannot see. We surface both possibilities rather than
        asserting "not found" (design §8).
        """
        resp = await self._client.get(f'/v2/evaluators/{evaluator_id}')
        if resp.status_code == 404:
            raise EvaluatorNotFound(evaluator_id)
        if resp.status_code >= 400:
            logger.error(f'✗ get_evaluator failed [{resp.status_code}]: {resp.text}')
            resp.raise_for_status()
        data = resp.json()
        prompt = _first_str(data, ('prompt', 'instructions')) or ''
        judge_model = _extract_judge_model(data)
        output_type = _first_str(data, ('output_type', 'outputType')) or ''
        return EvaluatorConfig(
            id=data.get('_id', evaluator_id),
            key=data.get('key', ''),
            prompt=prompt,
            judge_model=judge_model,
            output_type=output_type,
            variables=extract_template_variables(prompt),
            raw=data,
        )

    async def resolve_model_slug(self, model_id: str) -> str | None:
        """Map an evaluator's opaque model config id to a routable slug.

        Evaluator configs store the judge model as a workspace-registry UUID
        (e.g. ``ce490df4-...``), not a routable slug. ``GET /v2/models`` returns
        the registry (``id`` -> ``model_id``), so we look the UUID up there.
        Returns the slug (e.g. ``mistral-large-latest``) or ``None`` if the id
        isn't found or the call fails — the caller then falls back to trace
        resolution or an explicit override.
        """
        resp = await self._client.get('/v2/models')
        if resp.status_code >= 400:
            logger.warning(f'GET /v2/models [{resp.status_code}]; cannot resolve judge model slug')
            return None
        for m in _envelope_list(resp.json(), 'data', 'models', 'items'):
            if isinstance(m, dict) and m.get('id') == model_id:
                return m.get('model_id') or None
        return None

    # ── Step 2 ───────────────────────────────────────────────────────────────
    async def query_traces(
        self,
        *,
        limit: int = 200,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """POST /v2/traces/v3oql — paginate recent traces.

        The v3oql body requires BOTH `filters` (an object) AND `fields` (an
        array); omitting `fields` returns a 400. There is no server-side
        "filter by evaluator" operator on this endpoint, so we page recent
        traces with an empty filter and match the evaluator client-side on each
        trace's spans (see ``fetch_traces._evaluation_matches``). This mirrors
        the proven shape in ``orq_shared.orq_traces``.
        """
        out: list[dict[str, Any]] = []
        page = 1
        while len(out) < limit:
            want = min(page_size, limit - len(out))
            body: dict[str, Any] = {
                'filters': {'operator': 'and', 'filters': []},
                'fields': [],
                'limit': want,
                'page': page,
            }
            resp = await self._client.post('/v2/traces/v3oql', json=body)
            if resp.status_code >= 500 and out:
                # Deep pagination can 500 server-side; keep what we already have
                # rather than discarding a good partial scan.
                logger.warning(f'⚠ v3oql {resp.status_code} on page {page}; stopping with {len(out)} traces')
                break
            if resp.status_code >= 400:
                logger.error(f'✗ v3oql failed [{resp.status_code}]: {resp.text}')
                resp.raise_for_status()
            payload = resp.json()
            batch = _envelope_list(payload, 'data', 'traces', 'items')
            if not batch:
                break
            out.extend(batch)
            if payload.get('has_more') is False or len(batch) < want:
                break
            page += 1
        return out[:limit]

    async def get_trace_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """GET /v2/traces/{trace_id}/v3spans — span list for one trace.

        The list view is enough to detect which evaluator ran (it carries
        ``attributes.orq.evaluator.id``); call ``get_span`` for the full content
        (rendered judge prompt, explanation).
        """
        resp = await self._client.get(f'/v2/traces/{trace_id}/v3spans')
        if resp.status_code >= 400:
            logger.error(f'✗ v3spans failed [{resp.status_code}]: {resp.text}')
            resp.raise_for_status()
        # The list view returns either a bare list of spans or {"data": [...]}.
        return _envelope_list(resp.json(), 'data', 'spans', 'items')

    async def get_span(self, trace_id: str, span_id: str) -> dict[str, Any] | None:
        """GET /v2/traces/{trace_id}/v3spans/{span_id} — one span's full content.

        Returns ``None`` on a >=400 status, a non-JSON body, or a non-dict
        payload — logging a warning in each case — so the caller can fall back to
        the lighter list-view span rather than dropping the datapoint. Unwraps a
        ``{"data": ...}`` envelope if the endpoint uses one.
        """
        resp = await self._client.get(f'/v2/traces/{trace_id}/v3spans/{span_id}')
        if resp.status_code >= 400:
            # Not raised — caller falls back to the light span — but never silent:
            # a run-wide 401/403/429 would otherwise hollow every datapoint
            # (empty query/output, no judge_model) behind a green pipeline.
            logger.warning(f'span detail [{resp.status_code}] {trace_id}/{span_id}; using list-view fallback')
            return None
        try:
            payload = resp.json()
        except ValueError:
            logger.warning(f'span detail {trace_id}/{span_id} returned non-JSON body; using list-view fallback')
            return None
        return _envelope_dict(payload)

    # ── Step 9b ──────────────────────────────────────────────────────────────
    async def create_boolean_evaluator(
        self,
        *,
        key: str,
        path: str,
        prompt: str,
        model: str,
        description: str | None = None,
        guardrail_value: bool = True,
    ) -> CreateResult:
        """Create a single-judge boolean LLM-as-judge evaluator (the rewrite)."""
        body: dict[str, Any] = {
            'type': 'llm_eval',
            'mode': 'single',
            'model': model,
            'prompt': prompt,
            'output_type': 'boolean',
            'path': path,
            'key': key,
            'guardrail_config': {
                'type': 'boolean',
                'value': guardrail_value,
                'enabled': True,
                'alert_on_failure': False,
            },
        }
        if description is not None:
            body['description'] = description
        resp = await self._client.post('/v2/evaluators', json=body)
        if resp.status_code >= 400:
            logger.error(f'✗ create evaluator failed [{resp.status_code}]: {resp.text}')
            resp.raise_for_status()
        data = _envelope_dict(resp.json()) or {}  # tolerate a {"data": {...}} envelope
        new_id = data.get('_id') or data.get('id')
        if not new_id:
            # The evaluator may already exist server-side; surface the shape so a
            # response drift doesn't crash with a bare KeyError mid-write.
            raise RuntimeError(f'create evaluator returned no id; response shape: {data!r}')
        return CreateResult(id=new_id, key=data.get('key', key), raw=data)


class EvaluatorNotFound(RuntimeError):
    """404 from GET /v2/evaluators/{id} — wrong id OR wrong project."""

    def __init__(self, evaluator_id: str) -> None:
        self.evaluator_id = evaluator_id
        super().__init__(
            f'Evaluator {evaluator_id!r} returned 404. Two possibilities:\n'
            f'  1. The id is wrong.\n'
            f'  2. The evaluator exists but lives in a project this ORQ_API_KEY '
            f'cannot access.\n'
            f'Check the id, and confirm the key is scoped to the right project.'
        )


def _first_str(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _extract_judge_model(data: dict[str, Any]) -> str:
    """Pull the judge model id from any of the shapes the API has used."""
    model = data.get('model')
    if isinstance(model, str):
        return model
    if isinstance(model, dict):
        mid = model.get('id') or model.get('model')
        if isinstance(mid, str):
            return mid
    return ''
