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


def extract_categorical_labels(data: dict[str, Any]) -> list[str]:
    """The full DECLARED label set (K) of a categorical evaluator, in order.

    orq's record carries the rich `categorical_labels` — a list of
    ``{"value", "description"}`` — plus a flat `categories` mirror (§8.1). We keep
    the `value` strings (the actual verdict labels); fall back to `categories`; and
    return ``[]`` when neither is present, so a non-categorical record is a no-op.
    K is ``len()`` of this list — the count of allowed labels, never the observed.
    """
    labels = data.get('categorical_labels')
    if isinstance(labels, list):
        values = [item['value'] for item in labels if isinstance(item, dict) and item.get('value')]
        if values:
            return values
    flat = data.get('categories')
    if isinstance(flat, list):
        return [c for c in flat if isinstance(c, str) and c]
    return []


def _routable_slug(model_record: dict[str, Any]) -> str | None:
    """The routable slug for a `/v2/models` registry entry.

    Prefer `refId` — the provider-qualified id the router actually routes on
    (e.g. `groq/openai/gpt-oss-120b`, `openai/gpt-4o-mini`) — over `model_id`,
    which is a display alias that can address the wrong provider path. Concretely:
    an open-weights model served via groq has `model_id="openai/gpt-oss-120b"`,
    and that bare `openai/…` slug hits OpenAI's provider path → `403
    permission_error`, while its `refId="groq/openai/gpt-oss-120b"` routes. Fall
    back to `model_id` when no `refId` is present.
    """
    return model_record.get('refId') or model_record.get('model_id') or None


def _normalise_labels(labels: list[Any]) -> list[dict[str, str]]:
    """Coerce a declared label set into orq's rich ``[{value, description}]``.

    A source evaluator's labels reach us in one of two shapes: the rich record
    form (a list of ``{value, description}`` dicts, straight from the API) or the
    flat form (a list of ``value`` strings, as `evaluator.json`'s top-level
    `categorical_labels` stores them). orq's `POST /v2/evaluators` body wants the
    rich shape, so we widen flat strings to ``{value, description: ''}`` and keep
    any descriptions the rich form already carries. Non-string/dict entries and
    empty values are dropped, never invented.
    """
    out: list[dict[str, str]] = []
    for item in labels:
        if isinstance(item, dict):
            value = item.get('value')
            if isinstance(value, str) and value:
                out.append({'value': value, 'description': str(item.get('description') or '')})
        elif isinstance(item, str) and item:
            out.append({'value': item, 'description': ''})
    return out


def build_create_body(
    *,
    key: str,
    path: str,
    prompt: str,
    model: str,
    description: str | None,
    output_type: str,
    categorical_labels: list[Any] | None = None,
    scale: list[Any] | None = None,
    guardrail_value: bool = True,
) -> dict[str, Any]:
    """Assemble the `POST /v2/evaluators` request body for a given verdict type.

    A pure function (no httpx) so the per-type request shape is unit-testable:
    - **boolean**  → `output_type='boolean'` + a boolean `guardrail_config`;
    - **categorical** → `output_type='categorical'` + the declared label set in
      orq's rich ``categorical_labels`` shape (``[{value, description}]``) with a
      flat `categories` mirror, plus a categorical guardrail. Labels are
      required — a categorical evaluator with no labels has an undefined verdict
      space, so we refuse rather than send an empty set.
    - **numeric** (`'number'`) → `output_type='number'`; a `scale` is sent ONLY
      when the source record carried one — never invented (the record has no
      scale field of its own; §8.1). No label set is attached.

    Any other `output_type` is rejected: only the three supported verdict spaces
    can be created.
    """
    body: dict[str, Any] = {
        'type': 'llm_eval',
        'mode': 'single',
        'model': model,
        'prompt': prompt,
        'output_type': output_type,
        'path': path,
        'key': key,
    }
    if description is not None:
        body['description'] = description

    if output_type == 'boolean':
        body['guardrail_config'] = {
            'type': 'boolean',
            'value': guardrail_value,
            'enabled': True,
            'alert_on_failure': False,
        }
    elif output_type == 'categorical':
        rich = _normalise_labels(categorical_labels or [])
        if not rich:
            raise ValueError(
                'Cannot create a categorical evaluator without a label set: the '
                'verdict space would be undefined. Pass the source evaluator\'s '
                'categorical_labels through.'
            )
        body['categorical_labels'] = rich
        body['categories'] = [item['value'] for item in rich]
        body['guardrail_config'] = {
            'type': 'categorical',
            'enabled': True,
            'alert_on_failure': False,
        }
    elif output_type == 'number':
        # No scale field is invented: the record carries none of its own, so we
        # forward one only when the source actually declared it (fixtures pass
        # [min, max]). A numeric evaluator never carries a label set.
        if scale:
            body['scale'] = scale
    else:
        raise ValueError(
            f'Unsupported output_type {output_type!r}: expected one of '
            "'boolean', 'categorical', 'number'."
        )
    return body


@dataclass
class EvaluatorConfig:
    """The audited evaluator's config, normalised for downstream steps."""

    id: str
    key: str
    prompt: str
    judge_model: str
    output_type: str
    variables: list[str]
    categorical_labels: list[str] = field(default_factory=list)
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
            categorical_labels=extract_categorical_labels(data),
            raw=data,
        )

    async def resolve_model_slug(self, model_id: str) -> str | None:
        """Map an evaluator's opaque model config id to a routable slug.

        Evaluator configs store the judge model as a workspace-registry UUID
        (e.g. ``ce490df4-...``), not a routable slug. ``GET /v2/models`` returns
        the registry, so we look the UUID up there and return its **routable**
        slug — the provider-qualified ``refId`` (``groq/openai/gpt-oss-120b``),
        NOT the ``model_id`` display alias, which can address the wrong provider
        path and 403 (see ``_routable_slug``). Returns ``None`` if the id isn't
        found or the call fails — the caller then falls back to trace resolution
        or an explicit override.
        """
        resp = await self._client.get('/v2/models')
        if resp.status_code >= 400:
            logger.warning(f'GET /v2/models [{resp.status_code}]; cannot resolve judge model slug')
            return None
        for m in _envelope_list(resp.json(), 'data', 'models', 'items'):
            if isinstance(m, dict) and m.get('id') == model_id:
                return _routable_slug(m)
        return None

    async def model_slug_map(self) -> dict[str, str]:
        """Map every registry `model_id` (display alias) to its routable slug.

        `GET /v2/models` once, return ``{model_id: refId-or-fallback}`` via
        ``_routable_slug``. The model string recorded on a judge span is the
        display alias the judge was *called* with (e.g. ``openai/gpt-oss-120b``),
        which addresses the wrong provider path and 403s; this map lets step 2
        (``fetch_traces._resolve_judge_model``) normalise that alias to the
        provider-qualified ``refId`` (``groq/openai/gpt-oss-120b``) before pinning
        it — the same guarantee ``resolve_model_slug`` gives step 1. Returns ``{}``
        (never raises) when the call fails, so the caller falls back to the raw
        observed slug rather than aborting the run.
        """
        resp = await self._client.get('/v2/models')
        if resp.status_code >= 400:
            logger.warning(f'GET /v2/models [{resp.status_code}]; cannot normalise judge slugs')
            return {}
        out: dict[str, str] = {}
        for m in _envelope_list(resp.json(), 'data', 'models', 'items'):
            if isinstance(m, dict):
                model_id = m.get('model_id')
                slug = _routable_slug(m)
                if model_id and slug:
                    out[model_id] = slug
        return out

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
    async def create_evaluator(
        self,
        *,
        key: str,
        path: str,
        prompt: str,
        model: str,
        description: str | None = None,
        output_type: str,
        categorical_labels: list[Any] | None = None,
        scale: list[Any] | None = None,
        guardrail_value: bool = True,
    ) -> CreateResult:
        """Create a single-judge LLM-as-judge evaluator of any verdict type.

        Sends the correct `POST /v2/evaluators` body per ``output_type`` (built by
        the pure :func:`build_create_body`): boolean, categorical (the declared
        label set travels in orq's rich ``categorical_labels`` shape), or numeric
        (`'number'`; a scale is forwarded only when the source declared one — never
        invented). The verdict space is preserved, so the created evaluator scores
        the same space as its source. Co-location with the source project is the
        caller's job via ``path`` (whose first segment names the owning project).
        """
        body = build_create_body(
            key=key,
            path=path,
            prompt=prompt,
            model=model,
            description=description,
            output_type=output_type,
            categorical_labels=categorical_labels,
            scale=scale,
            guardrail_value=guardrail_value,
        )
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
        """Create a boolean evaluator. Thin wrapper over :meth:`create_evaluator`
        so existing boolean callers keep working unchanged."""
        return await self.create_evaluator(
            key=key,
            path=path,
            prompt=prompt,
            model=model,
            description=description,
            output_type='boolean',
            guardrail_value=guardrail_value,
        )


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
