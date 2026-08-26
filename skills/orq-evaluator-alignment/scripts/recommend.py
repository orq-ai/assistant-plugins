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
"""Step 8a — type-aware human-vs-judge disagreements → rubric recommendations.

For each human-labelled confuser we compare the human `value` (from
`annotations.json`) against the judge's majority/central verdict (read straight
off `metrics.json`'s `per_row` detail), **per verdict type** (RES-978 Part 2
§2.2):

  - boolean     → flip (human ≠ judge mode);
  - categorical → confusion pair (judge label vs human label);
  - numeric     → signed error (judge central − human) + magnitude/direction.

Those typed disagreements — plus the human's `reason` — are the grounding for a
rubric-improvement suggestion per datapoint. When a real backend is configured
(`lib.model_backend`) the typed disagreement is the INPUT to the meta-prompt,
which returns one structured `{reasoning, recommendation}`; the `fake` backend
in tests returns canned JSON. Everything is written to `recommendations.json`
with the typed disagreement attached, so `aggregate.py` and the rewrite step can
consume it without re-deriving anything.

The meta-prompt embeds the judge prompt (which carries its own `{{query}}` /
`{{output}}` tokens) as a variable value. `render_template` substitutes the four
meta-prompt variables in a single pass and does not re-scan the inserted text,
so those nested tokens stay literal — the model sees the real rubric.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/recommend.py --run_dir runs/<key>_<ts>
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner
from lib.model_backend import (
    BackendUnavailable,
    apply_backend_overrides,
    describe_backend,
    get_backend,
)

load_dotenv()

# Per-datapoint recommendation prompt. Deliberately NOT `meta_prompt.md`:
# Workstream B repurposed that file into the single verdict-space-preserving
# *rewrite* prompt (consumed by rewrite_eval.py). recommend.py owns the distinct
# per-datapoint *suggestion* prompt, whose four variables it fills below.
RECOMMEND_PROMPT = (runner.SKILL_ROOT / 'prompts' / 'recommend_prompt.md').read_text(encoding='utf-8')

_NUMERIC_TYPES = {'number', 'numeric'}
# Default agreement band for numeric: |judge − human| at or below this counts as
# agreement, not a disagreement. Only used when no scale is declared — see
# `_resolve_tolerance`.
_DEFAULT_NUMERIC_TOLERANCE = 0.5


def _resolve_tolerance(cfg: dict[str, Any], scale: Any) -> float:
    """The numeric agreement band, from one config key shared with retest/cross_model.

    This step used to read `numeric_tolerance` while `retest.py` and
    `cross_model.py` read `numeric_tol`, so setting the one that looked right moved
    what counts as a disagreement HERE and left the gate that signs the rewrite off
    on its default — two different bands for one concept, neither documented.
    `numeric_tol` is now the single key; the band is otherwise derived from the
    evaluator's declared scale (`lib.agreement.default_tolerance`).
    """
    from lib import agreement as agreement_lib  # noqa: PLC0415 — pure stdlib module

    configured = cfg.get('numeric_tol', cfg.get('numeric_tolerance'))
    if configured not in (None, ''):
        return float(configured)
    return agreement_lib.default_tolerance(
        scale,
        fraction=float(cfg.get('numeric_tol_fraction', agreement_lib.DEFAULT_TOL_FRACTION)),
        fallback=_DEFAULT_NUMERIC_TOLERANCE,
    )


# ── pure per-type disagreement extraction (§2.2) ─────────────────────────────


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value) if value in (0, 1) else None
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {'true', 'yes', 'pass', '1'}:
            return True
        if v in {'false', 'no', 'fail', '0'}:
            return False
    return None


def _annotation_reason(annotation: dict[str, Any]) -> str:
    """The human's free-text note. `reason` is the Part 2 contract (§2.1); the
    older UI persisted it as `explanation` — accept either so both shapes work."""
    return (annotation.get('reason') or annotation.get('explanation') or '').strip()


def _judge_central(row: dict[str, Any], output_type: str) -> Any:
    """The judge's majority/central verdict for a metrics per_row entry, per type.

    Boolean → modal value (`mode_value`, falling back to the n_true/n_false split
    when a single-repeat row has no mode). Categorical → argmax of `counts`.
    Numeric → the row `mean` (the central value the instability spread is around).
    """
    if output_type == 'boolean':
        mode = row.get('mode_value')
        if isinstance(mode, bool):
            return mode
        coerced = _coerce_bool(mode)
        if coerced is not None:
            return coerced
        n_true = int(row.get('n_true') or 0)
        n_false = int(row.get('n_false') or 0)
        if n_true == 0 and n_false == 0:
            return None
        return n_true >= n_false
    if output_type == 'categorical':
        counts = row.get('counts') or {}
        if not counts:
            return None
        # Deterministic argmax: highest count, ties broken by label order.
        return max(sorted(counts), key=lambda label: counts[label])
    if output_type in _NUMERIC_TYPES:
        mean = row.get('mean')
        return float(mean) if isinstance(mean, (int, float)) else None
    return None


def _disagreement(
    row: dict[str, Any],
    annotation: dict[str, Any],
    output_type: str,
    labels: list[str] | None = None,
    tolerance: float = _DEFAULT_NUMERIC_TOLERANCE,
) -> dict[str, Any] | None:
    """Compare a human label against the judge's central verdict, type-natively.

    Returns a typed disagreement record, or None when they agree. Records share a
    common spine (`source_index`, `type`, `kind`, `judge_value`, `human_value`,
    `reason`) plus type-specific detail:
      - boolean     kind='flip';
      - categorical kind='confusion_pair', + `confusion_pair` (judge, human);
      - numeric     kind='signed_error', + `signed_error` (judge − human),
                    `magnitude`, `direction` (over|under).
    """
    judge = _judge_central(row, output_type)
    reason = _annotation_reason(annotation)
    base = {
        'source_index': row.get('source_index'),
        'type': 'number' if output_type in _NUMERIC_TYPES else output_type,
        'judge_value': judge,
        'reason': reason,
    }

    if output_type == 'boolean':
        human = _coerce_bool(annotation.get('value'))
        base['human_value'] = human
        if human is None or judge is None or human == judge:
            return None
        return {**base, 'kind': 'flip'}

    if output_type == 'categorical':
        human = annotation.get('value')
        human = str(human) if human is not None else None
        base['human_value'] = human
        if human is None or judge is None or human == judge:
            return None
        return {**base, 'kind': 'confusion_pair', 'confusion_pair': (judge, human)}

    if output_type in _NUMERIC_TYPES:
        try:
            human = float(annotation.get('value'))
        except (TypeError, ValueError):
            human = None
        base['human_value'] = human
        if human is None or judge is None:
            return None
        signed = judge - human
        if abs(signed) <= tolerance:
            return None
        return {
            **base,
            'kind': 'signed_error',
            'signed_error': signed,
            'magnitude': abs(signed),
            'direction': 'over' if signed > 0 else 'under',
        }

    return None


def _disagreement_summary(d: dict[str, Any]) -> str:
    """One-line, human-readable rendering of a typed disagreement for the prompt."""
    kind = d['kind']
    if kind == 'flip':
        return f'judge said {d["judge_value"]}, human said {d["human_value"]} (flip)'
    if kind == 'confusion_pair':
        return f'judge labelled "{d["judge_value"]}", human labelled "{d["human_value"]}" (confusion pair)'
    if kind == 'signed_error':
        return (
            f'judge scored {d["judge_value"]:.2f}, human scored {d["human_value"]:.2f} — '
            f'judge {d["direction"]}-scored by {d["magnitude"]:.2f}'
        )
    return f'judge {d.get("judge_value")}, human {d.get("human_value")}'


# ── meta-prompt rendering (typed disagreement is the INPUT) ──────────────────


def _render_meta(judge_prompt: str, row: dict[str, Any], annotation: dict[str, Any],
                 disagreement: dict[str, Any] | None, output_type: str) -> str:
    from evaluatorq.common.judge import render_template

    messages = row.get('messages')
    msg_str = messages if isinstance(messages, str) else (str(messages) if messages else '')
    input_block = (
        (f'<conversation>\n{msg_str}\n</conversation>\n' if msg_str else '')
        + f'<query>{row.get("query", "")}</query>\n'
        + f'<assistant_output>{row.get("output", "")}</assistant_output>'
    )
    judge_central = _judge_central(row, output_type)
    detail = _judge_detail_str(row, output_type)
    judge_block = (
        f'output_type: {output_type}\n'
        f'judge central verdict: {judge_central}\n'
        f'judge verdict spread: {detail}\n'
        f'representative explanation: {row.get("representative_explanation") or "(none)"}'
    )
    if disagreement is not None:
        human_block = (
            f'human label: {disagreement.get("human_value")}\n'
            f'disagreement: {_disagreement_summary(disagreement)}\n'
            f'human reason: {_annotation_reason(annotation) or "(none)"}'
        )
    else:
        human_block = (
            f'human label: {annotation.get("value")}\n'
            f'agreement: human matches the judge on this datapoint.\n'
            f'human reason: {_annotation_reason(annotation) or "(none)"}'
        )
    return render_template(
        RECOMMEND_PROMPT,
        {
            'evaluator_prompt': judge_prompt,
            'input': input_block,
            'judge_output': judge_block,
            'human_annotation': human_block,
        },
    )


def _judge_detail_str(row: dict[str, Any], output_type: str) -> str:
    if output_type == 'boolean':
        return f'{row.get("n_true", "?")}T / {row.get("n_false", "?")}F'
    if output_type == 'categorical':
        return f'counts={row.get("counts")} (k={row.get("k")})'
    if output_type in _NUMERIC_TYPES:
        return f'mean={row.get("mean")}, stdev={row.get("stdev")}, scale={row.get("scale")}'
    return ''


def _parse_recommendation(text: str) -> dict[str, Any]:
    import json
    import re

    # Prefer the whole payload as JSON (the contract). Only if that fails fall
    # back to carving out the outermost {...} — the greedy match can span across
    # prose braces, so it's the last resort, not the first try.
    m = re.search(r'\{.*\}', text, re.DOTALL)
    obj = None
    for candidate in (text, m.group(0) if m else None):
        if candidate is None:
            continue
        try:
            obj = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if obj is None:
        # Distinguish a parse bug from an API outage in the recorded error.
        raise ValueError(f'no JSON object in recommendation output: {text[:200]!r}')
    return {'reasoning': obj.get('reasoning', ''), 'recommendation': obj.get('recommendation', '')}


def _labeled_annotations(annotations: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """Human-labelled entries with a concrete value, keyed by source_index.

    Accepts both the Part 2 contract (`{value, reason, low_flip_sample}`, no
    status) and the older UI shape (`{status: 'labeled', value, explanation,
    provenance}`). An explicit non-'labeled' status is skipped; otherwise any
    entry carrying a non-None `value` is taken.
    """
    out: list[tuple[int, dict[str, Any]]] = []
    for k, a in annotations.items():
        if not isinstance(a, dict):
            continue
        status = a.get('status')
        if status is not None and status != 'labeled':
            continue
        if a.get('value') is None:
            continue
        try:
            out.append((int(k), a))
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda t: t[0])


def _low_flip_flag(annotation: dict[str, Any], row: dict[str, Any]) -> bool:
    prov = annotation.get('provenance') or {}
    return bool(
        annotation.get('low_flip_sample')
        or prov.get('low_flip_sample')
        or row.get('low_flip_sample')
    )


def _rows_by_index(metrics: dict[str, Any], stability: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """Index metrics `per_row` by source_index, enriched with the judged input
    (query/output/messages) from stability.json. metrics.json no longer carries the
    input itself (kept lean so the conductor's report read can't slurp every
    datapoint); stability.json is the canonical copy. Metric fields are preserved."""
    inputs = {r.get('source_index'): r for r in stability.get('rows', [])}
    merged: dict[Any, dict[str, Any]] = {}
    for r in metrics.get('per_row', []):
        idx = r.get('source_index')
        src = inputs.get(idx, {})
        merged[idx] = {
            **r,
            'query': src.get('query', ''),
            'output': src.get('output', ''),
            'messages': src.get('messages'),
        }
    return merged


async def _run(out_dir: Path, cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    evaluator = runner.read_json(out_dir / 'evaluator.json')
    metrics = runner.read_json(out_dir / 'metrics.json')
    stability = runner.read_json(out_dir / 'stability.json')
    annotations = runner.read_json(out_dir / 'annotations.json')

    output_type = (evaluator.get('output_type') or metrics.get('metadata', {}).get('output_type') or 'boolean').strip().lower()
    labels = evaluator.get('categorical_labels') or []
    tolerance = _resolve_tolerance(cfg, evaluator.get('scale'))

    rows_by_idx = _rows_by_index(metrics, stability)
    labeled = _labeled_annotations(annotations)
    if not labeled:
        raise RuntimeError('No labeled annotations in annotations.json — run the annotation step first.')

    backend = get_backend(cfg)
    # Fail here, with a reason, rather than partway through a paid run: an
    # `orq_router` model can be listed in the registry and still refuse for want
    # of a provider key on this workspace.
    preflight = getattr(backend, 'preflight', None)
    if preflight is not None:
        try:
            await preflight()
        except BackendUnavailable as exc:
            raise SystemExit(
                f'Cannot use {describe_backend(cfg)}: {exc}\n'
                '  Pick another model with `backend_model` in config.toml, or set\n'
                '  `backend = "claude_subagent"` to use the Claude CLI instead.'
            ) from exc
    sem = asyncio.Semaphore(int(cfg.get('recommend_concurrency', 4)))
    judge_prompt = evaluator['prompt']

    async def _one(idx: int, annotation: dict[str, Any]) -> dict[str, Any]:
        row = rows_by_idx.get(idx)
        if row is None:
            return {'source_index': idx, 'error': 'no metrics per_row for this annotation', 'success': False}
        disagreement = _disagreement(row, annotation, output_type, labels=labels, tolerance=tolerance)
        prompt = _render_meta(judge_prompt, row, annotation, disagreement, output_type)
        async with sem:
            try:
                res = await backend.complete(prompt)
                parsed = _parse_recommendation(res.text)
                return {
                    'source_index': idx,
                    'success': True,
                    'output_type': 'number' if output_type in _NUMERIC_TYPES else output_type,
                    'agreement': disagreement is None,
                    'disagreement': disagreement,  # typed record (§2.2) or None on agreement
                    'human_value': annotation.get('value'),
                    'judge_value': _judge_central(row, output_type),
                    'reason': _annotation_reason(annotation),
                    'low_flip_sample': _low_flip_flag(annotation, row),
                    'reasoning': parsed['reasoning'],
                    'recommendation': parsed['recommendation'],
                    'cost_usd': res.cost_usd,
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception(f'✗ recommendation failed for #{idx}')
                return {'source_index': idx, 'success': False, 'error': f'{type(exc).__name__}: {exc}'}

    results = await asyncio.gather(*(_one(idx, a) for idx, a in labeled))
    meta = {'output_type': 'number' if output_type in _NUMERIC_TYPES else output_type,
            'labels': labels, 'numeric_tolerance': tolerance}
    return sorted(results, key=lambda r: r['source_index']), meta


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    backend: str | None = None,
    backend_model: str | None = None,
    backend_base_url: str | None = None,
) -> str:
    """Generate per-annotation, type-aware recommendations via the configured backend.

    Args:
        run_dir: Run directory (defaults to most recent).
        config: TOML config path.
        backend: Which model runs this step, overriding config `backend`. One of
            orq_router (default), claude_subagent, anthropic_api, orq_deployment,
            fake.
        backend_model: Model id for that backend, overriding config `backend_model`.
            For orq_router it must be the provider-qualified `refId` from
            GET /v2/models (e.g. `deepseek/deepseek-v4-pro`), not the display alias.
        backend_base_url: Endpoint for that backend, overriding config
            `backend_base_url`. Use for a self-hosted or proxied orq / an
            Anthropic-compatible gateway. Falls back to ORQ_BASE_URL (orq_router),
            ORQ_API_BASE_URL (orq_deployment) or ANTHROPIC_BASE_URL (anthropic_api),
            then the hosted default.

    API keys are read from the environment or a .env file only — ORQ_API_KEY for the
    orq backends, ANTHROPIC_API_KEY for anthropic_api — never from a flag, which
    would leave the credential in shell history and in `ps`.
    """
    cfg = runner.load_config(config)
    cfg = apply_backend_overrides(
        cfg, backend=backend, backend_model=backend_model, backend_base_url=backend_base_url
    )
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run the annotation step first.')

    results, meta = asyncio.run(_run(out_dir, cfg))
    ok = [r for r in results if r.get('success')]
    n_disagree = sum(1 for r in ok if r.get('disagreement'))
    total_cost = sum(r.get('cost_usd', 0.0) for r in ok)
    # Kinds seen (flip / confusion_pair / signed_error) for a quick at-a-glance tally.
    kinds = Counter(r['disagreement']['kind'] for r in ok if r.get('disagreement'))
    runner.write_json(
        out_dir / 'recommendations.json',
        {
            'metadata': {
                'backend': cfg.get('backend'),
                'output_type': meta['output_type'],
                'labels': meta['labels'],
                'numeric_tolerance': meta['numeric_tolerance'],
                'n_annotations': len(results),
                'n_ok': len(ok),
                'n_disagreements': n_disagree,
                'n_agreements': len(ok) - n_disagree,
                'disagreement_kinds': dict(kinds),
                'total_cost_usd': round(total_cost, 6),
            },
            'recommendations': results,
        },
    )
    logger.info(
        f'✓ Wrote {out_dir / "recommendations.json"} '
        f'({len(ok)}/{len(results)} ok, {n_disagree} disagreement(s), ${total_cost:.4f})'
    )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
