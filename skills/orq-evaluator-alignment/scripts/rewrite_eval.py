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
"""Step 9a — rewrite the judge prompt through the single, verdict-space-aware
meta prompt (propose only, no creation).

RES-978 Part 2 (Workstream B). One meta prompt (`prompts/meta_prompt.md`)
handles all three judge output types by parameterizing its verdict-space
section; it replaces PO2's boolean-only split (`prompts/po2.md` stays on disk
but is no longer routed to). The rewrite reads:

- `evaluator.json` — `output_type` plus `categorical_labels` / `scale`, which
  define the verdict space to preserve.
- `aggregated.md` — the aggregated human policy, treated as free-text guidance
  (the `<input_instructions>`); no exact schema is assumed.

and renders the meta prompt with a per-type verdict-space context:

- boolean / categorical → a "pick-a-label" rubric edit (the shared path): the
  declared label set is fixed and every label must survive the rewrite.
- numeric → **deliberately shallow**: nudge the scale's anchor descriptions
  (what a 1 vs a mid vs a max score means) toward the human's scoring. NOT a
  calibration model — the numeric section only reshapes anchor prose and pins
  the scale endpoints.

**Preservation gate.** The proposed rewrite must (a) keep the exact `{{...}}`
variable set (a judge whose `{{output}}` vanished scores against nothing), and
(b) preserve the verdict space — never drop a declared categorical label, never
move the numeric scale bounds. On a violation we re-invoke the meta prompt with
the violation spelled out, looping up to `max_attempts`. If it still fails we
write the proposal but record `var_check_passed: false` / `verdict_space_ok:
false` so step 9b refuses to create the evaluator until a human intervenes.

The judge prompt embeds its own `{{query}}` / `{{output}}` tokens; the string
backends keep them literal, and `orq_deployment` self-references them
(model_backend), so the meta prompt sees and preserves the real variables.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/rewrite_eval.py --run_dir runs/<key>_<ts>
"""

from __future__ import annotations

import asyncio
import re
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
from lib.orq_client import extract_template_variables

load_dotenv()

# The single verdict-space-parameterized meta prompt. It carries three
# `{...}`-style placeholders (verdict_space, preservation_rule, type_guidance)
# filled per output type by `build_verdict_space_context`, and the two `{{...}}`
# envelope tokens (input_instructions, prompt) filled per attempt.
META_PROMPT = (runner.SKILL_ROOT / 'prompts' / 'meta_prompt.md').read_text(encoding='utf-8')

_NUMERIC_TYPES = frozenset({'number', 'numeric'})


# ── verdict-space context (pure; the per-type parameterization) ───────────────
def build_verdict_space_context(evaluator: dict[str, Any]) -> dict[str, str]:
    """Render the meta prompt's verdict-space variables for this output type.

    Returns the three template values the meta prompt needs:

    - ``verdict_space``     — a human-readable description of the fixed verdict
      space (the boolean labels, the K categorical labels, or the numeric scale
      endpoints) so the rewriter knows exactly what must survive.
    - ``preservation_rule`` — the imperative one-liner naming what may never
      change (mirrors what ``check_preservation`` enforces).
    - ``type_guidance``     — the type-specific rubric-editing instruction.
      boolean / categorical share the "pick-a-label" path; numeric is
      deliberately shallow (anchor-nudging, not a calibration model).

    Raises ``ValueError`` for a categorical evaluator with no declared labels —
    its verdict space is undefined, so we fail loud rather than emit a
    label-less rubric.
    """
    output_type = (evaluator.get('output_type') or '').strip().lower()

    if output_type == 'boolean':
        verdict_space = (
            'Type: boolean. The judge returns exactly one of two verdicts: '
            '`True` or `False`. Both must remain valid, distinct outcomes.'
        )
        preservation_rule = (
            'Keep both boolean verdicts `True` and `False` as the only outcomes; '
            'do not collapse them into one, add a third, or change what the value means.'
        )
        type_guidance = _PICK_A_LABEL_GUIDANCE
        return {
            'verdict_space': verdict_space,
            'preservation_rule': preservation_rule,
            'type_guidance': type_guidance,
        }

    if output_type == 'categorical':
        labels = list(evaluator.get('categorical_labels') or [])
        if not labels:
            raise ValueError(
                'categorical evaluator has no declared categorical_labels — its verdict '
                'space is undefined. Cannot build the rewrite context.'
            )
        label_list = ', '.join(f'`{lbl}`' for lbl in labels)
        verdict_space = (
            f'Type: categorical with K={len(labels)} declared labels. The judge '
            f'returns exactly one of: {label_list}. All {len(labels)} labels must '
            'remain selectable — none may be dropped, merged, or renamed.'
        )
        preservation_rule = (
            f'Keep every one of these {len(labels)} categorical labels available and named '
            f'in the rubric: {label_list}. Do not drop, merge, rename, or add a label.'
        )
        type_guidance = _PICK_A_LABEL_GUIDANCE
        return {
            'verdict_space': verdict_space,
            'preservation_rule': preservation_rule,
            'type_guidance': type_guidance,
        }

    if output_type in _NUMERIC_TYPES:
        scale = evaluator.get('scale')
        if scale and len(scale) == 2:
            lo, hi = _fmt_num(scale[0]), _fmt_num(scale[1])
            verdict_space = (
                f'Type: numeric. The judge returns a score on the fixed scale '
                f'[{lo}, {hi}] (minimum {lo}, maximum {hi}). The scale bounds do not move.'
            )
            preservation_rule = (
                f'Keep the numeric scale exactly [{lo}, {hi}] — the minimum stays {lo} and the '
                f'maximum stays {hi}. Do not rescale, re-range, or renumber the anchors.'
            )
        else:
            # Unmeasurable numeric (§4a): no scale supplied. We can still nudge
            # anchor prose, but there are no explicit endpoints to pin.
            verdict_space = (
                'Type: numeric on the judge\'s existing scale (its numeric range is '
                'defined in the prompt below). The scale range does not move.'
            )
            preservation_rule = (
                'Keep the numeric scale exactly as it appears in the prompt — do not rescale, '
                're-range, or renumber the anchors.'
            )
        type_guidance = _NUMERIC_SHALLOW_GUIDANCE
        return {
            'verdict_space': verdict_space,
            'preservation_rule': preservation_rule,
            'type_guidance': type_guidance,
        }

    raise ValueError(
        f'output_type={output_type!r} is not supported for rewrite. Expected '
        'boolean | categorical | number.'
    )


_PICK_A_LABEL_GUIDANCE = (
    'This is a pick-a-label judge. Edit the rubric so the *criteria* that decide '
    'which label applies move toward the human labels — tighten, loosen, clarify, or '
    'reframe the label definitions and their boundaries. Adjust which label a class of '
    'cases should receive; never change the set of labels itself.'
)

_NUMERIC_SHALLOW_GUIDANCE = (
    'This is a numeric-score judge, and the rewrite here is deliberately shallow: do NOT '
    'build a calibration model, add scoring formulae, or introduce sub-scores. Instead, '
    'nudge the *anchor descriptions* — what a low score, a mid score, and the maximum '
    "score each mean — so they describe the behavior the human's scoring implies. Move the "
    'prose that anchors each point of the scale, keeping the scale itself and its endpoints '
    'unchanged.'
)


def _fmt_num(n: Any) -> str:
    """Render a scale bound without a trailing `.0` (so `1.0` reads as `1`)."""
    f = float(n)
    return str(int(f)) if f.is_integer() else str(f)


# ── prompt assembly ───────────────────────────────────────────────────────────
def _fill_system(context: dict[str, str]) -> str:
    """Fill the meta prompt's verdict-space `{...}` placeholders (system half).

    The trailing `<input_instructions>`/`<prompt>` envelope (carrying the two
    `{{...}}` tokens) is dropped here — it becomes the separate user message via
    `_user_message`, so the model gets the guidance + the judge prompt exactly
    once. `str.format` is not used (the doc is full of literal braces from the
    judge's own `{{...}}` examples); we substitute the three known tokens only.
    """
    system = META_PROMPT
    for key in ('verdict_space', 'preservation_rule', 'type_guidance'):
        system = system.replace('{' + key + '}', context[key])
    # Strip the input envelope; the user message supplies it filled.
    marker = '<input_instructions>'
    idx = system.rfind(marker)
    return system[:idx].rstrip() if idx != -1 else system


def _user_message(instructions: str, prompt: str) -> str:
    return f'<input_instructions>\n{instructions}\n</input_instructions>\n<prompt>\n{prompt}\n</prompt>'


def render_meta_prompt(instructions: str, prompt: str, context: dict[str, str]) -> str:
    """Return the full meta prompt (system + user) for inspection / tests.

    The production call splits these into ``system`` and the user message (see
    ``_rewrite``); this joins them so a test can assert on the whole rendered
    prompt in one place.
    """
    return _fill_system(context) + '\n\n' + _user_message(instructions, prompt)


# ── preservation guard ────────────────────────────────────────────────────────
def _mentions_number(text: str, number: str) -> bool:
    """Whether `number` appears in `text` as a number, not inside a longer one.

    A plain `in` test passes `1` on a rubric that only ever says `10`, and passes
    `5` on one that says `0.5` — so a rewrite that moved the scale from `[1, 5]`
    to `[1, 10]` cleared the guard while both endpoints were, in fact, gone.
    """
    return re.search(rf'(?<![\d.]){re.escape(number)}(?![\d.])', text) is not None


def _mentions_token(text: str, token: str) -> bool:
    """Whether `token` appears in `text` on its own word boundaries.

    Same failure as `_mentions_number`, one level up: `spam not in proposed` is
    False for a rubric that only mentions `spammy`, so a dropped label reads as
    preserved. Case-sensitive on purpose — the declared spelling is the contract.
    """
    return re.search(rf'(?<!\w){re.escape(token)}(?!\w)', text) is not None


def check_preservation(evaluator: dict[str, Any], proposed: str) -> tuple[bool, str]:
    """Check a proposed rewrite preserves BOTH the variable set and verdict space.

    Returns ``(ok, reason)``. ``reason`` is empty on success and, on failure,
    names exactly what broke (a dropped label / moved scale bound / changed
    variable) so it can be fed straight back to the meta prompt as a fix note.
    Order: the `{{...}}` variable-set check first (a scored-against-nothing judge
    is the worst failure), then the verdict-space check.
    """
    source_vars = set(evaluator.get('variables') or [])
    got_vars = set(extract_template_variables(proposed))
    if got_vars != source_vars:
        return False, _var_violation_note(source_vars, got_vars)

    output_type = (evaluator.get('output_type') or '').strip().lower()
    if output_type == 'categorical':
        labels = list(evaluator.get('categorical_labels') or [])
        dropped = [lbl for lbl in labels if not _mentions_token(proposed, lbl)]
        if dropped:
            named = ', '.join('`' + lbl + '`' for lbl in dropped)
            return False, (
                f'The rewrite DROPPED declared categorical label(s): {named}. Every declared '
                f'label must appear in the rubric: {", ".join("`" + lbl + "`" for lbl in labels)}.'
            )
    elif output_type in _NUMERIC_TYPES:
        scale = evaluator.get('scale')
        if scale and len(scale) == 2:
            missing = [
                b for b in (_fmt_num(scale[0]), _fmt_num(scale[1]))
                if not _mentions_number(proposed, b)
            ]
            if missing:
                return False, (
                    f'The rewrite MOVED the numeric scale: endpoint(s) {", ".join(missing)} no '
                    f'longer appear. Keep the scale exactly [{_fmt_num(scale[0])}, {_fmt_num(scale[1])}].'
                )

    return True, ''


def _var_violation_note(source: set[str], got: set[str]) -> str:
    missing = sorted(source - got)
    added = sorted(got - source)
    parts = []
    if missing:
        parts.append(f'You DROPPED these required variables: {", ".join("{{" + v + "}}" for v in missing)}.')
    if added:
        parts.append(f'You INTRODUCED these new variables (not allowed): {", ".join("{{" + v + "}}" for v in added)}.')
    return (
        ' '.join(parts)
        + ' The rewritten prompt MUST contain exactly these template variables and no others: '
        + ', '.join('{{' + v + '}}' for v in sorted(source))
        + '. Rewrite again, preserving every one of them verbatim.'
    )


# ── driver ────────────────────────────────────────────────────────────────────
async def _rewrite(out_dir: Path, cfg: dict[str, Any], max_attempts: int) -> dict[str, Any]:
    evaluator = runner.read_json(out_dir / 'evaluator.json')
    # aggregated.md is the aggregated human policy — free-text guidance, no exact
    # schema assumed. It falls back to recommendations.json only if aggregate.py
    # was skipped, so the rewrite still has something to act on.
    instructions = _read_guidance(out_dir)
    judge_prompt = evaluator['prompt']

    context = build_verdict_space_context(evaluator)
    system = _fill_system(context)

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
    attempts: list[dict[str, Any]] = []
    current_instructions = instructions
    proposed = judge_prompt
    total_cost = 0.0
    ok = False
    reason = ''

    for attempt in range(1, max_attempts + 1):
        res = await backend.complete(_user_message(current_instructions, judge_prompt), system=system)
        proposed = res.text.strip()
        total_cost += res.cost_usd
        ok, reason = check_preservation(evaluator, proposed)
        attempts.append(
            {
                'attempt': attempt,
                'preservation_ok': ok,
                'got_vars': sorted(extract_template_variables(proposed)),
                'reason': reason,
            }
        )
        if ok:
            logger.info(f'✓ Verdict space + variables preserved on attempt {attempt}.')
            break
        logger.warning(f'⚠ Attempt {attempt}: preservation failed — {reason} Re-invoking meta prompt.')
        # Append the violation to the instructions so the rewriter self-corrects.
        current_instructions = instructions + '\n\n## CRITICAL FIX\n' + reason

    got_vars = sorted(extract_template_variables(proposed))
    return {
        'proposed_prompt': proposed,
        'var_check_passed': set(got_vars) == set(evaluator.get('variables') or []),
        'verdict_space_ok': ok,
        'preservation_ok': ok,
        'reason': reason,
        'output_type': (evaluator.get('output_type') or '').strip().lower(),
        'source_vars': sorted(set(evaluator.get('variables') or [])),
        'new_vars': got_vars,
        'attempts': attempts,
        'cost_usd': round(total_cost, 6),
    }


def _read_guidance(out_dir: Path) -> str:
    """Read the aggregated human policy as free-text guidance.

    Prefer `aggregated.md` (the conductor-refined summary). Fall back to the raw
    recommendations if aggregate.py was skipped, so a rewrite is still possible;
    treat both as opaque guidance text (no schema dependency).
    """
    agg = out_dir / 'aggregated.md'
    if agg.exists():
        text = agg.read_text(encoding='utf-8-sig').strip()
        if text:
            return text
    recs_path = out_dir / 'recommendations.json'
    if recs_path.exists():
        recs = runner.read_json(recs_path)
        lines = [
            (r.get('recommendation') or '').strip()
            for r in recs.get('recommendations', [])
            if r.get('success') and (r.get('recommendation') or '').strip()
        ]
        if lines:
            return '## Recommendations\n' + '\n'.join(f'- {ln}' for ln in dict.fromkeys(lines))
    raise SystemExit(
        f'No guidance found in {out_dir}: neither a non-empty aggregated.md nor '
        'recommendations.json. Run recommend.py + aggregate.py first.'
    )


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    max_attempts: int = 3,
    backend: str | None = None,
    backend_model: str | None = None,
    backend_base_url: str | None = None,
) -> str:
    """Run the meta prompt to propose a rewritten judge prompt (no orq object created).

    Args:
        run_dir: Run directory (defaults to most recent).
        config: TOML config path.
        max_attempts: Rewrite attempts before giving up on the preservation guard.
        backend: Which model writes the rewrite, overriding config `backend`. One of
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
        raise SystemExit('No run directory. Run aggregate.py first.')

    result = asyncio.run(_rewrite(out_dir, cfg, max_attempts))
    runner.write_text(out_dir / 'new_prompt.md', result['proposed_prompt'])
    runner.write_json(
        out_dir / 'rewrite_status.json',
        {
            'var_check_passed': result['var_check_passed'],
            'verdict_space_ok': result['verdict_space_ok'],
            'preservation_ok': result['preservation_ok'],
            'reason': result['reason'],
            'output_type': result['output_type'],
            'source_vars': result['source_vars'],
            'new_vars': result['new_vars'],
            'attempts': result['attempts'],
            'cost_usd': result['cost_usd'],
            'backend': cfg.get('backend'),
        },
    )
    logger.info(f'✓ Wrote {out_dir / "new_prompt.md"} (PROPOSED — not yet created)')
    if not result['preservation_ok']:
        logger.error(
            '✗ Preservation FAILED after all attempts: '
            f'{result["reason"]} '
            'create_eval.py will refuse until this is fixed (edit new_prompt.md or rerun).'
        )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
