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
"""Step 6 — build the human-annotation queue, ranked by judge ambiguity.

Takes `metrics.json` (already sorted most-ambiguous-first) and the annotation
count the user chose *after* seeing the flip report (an informed decision, not a
fixed config value — design §2 step 6). Produces `queue.json`: the top-`count`
flipped datapoints, optionally followed by a small random sample of LOW-flip
items as a sanity check against the consistently-wrong blind spot (design §1).

Each item is self-contained so the annotation UI needs no knowledge of the
on-disk metric formats.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/build_queue.py --run_dir runs/<key>_<ts> --count 25
"""

from __future__ import annotations

import random
import re
from typing import Any

import fire
from loguru import logger

import _bootstrap  # noqa: F401
from lib import grey_zone as gzlib
from lib import runner

# Matches an orq prompt placeholder, e.g. `{{ log.output }}`. Names can carry
# dots (`log.output`) and surrounding whitespace; we keep the trimmed name.
_PLACEHOLDER = re.compile(r'\{\{\s*([^}]+?)\s*\}\}')


def _invert_template(template: str, rendered: str) -> list[dict[str, str]] | None:
    """Recover each `{{variable}}` value from a fully-rendered judge prompt.

    The trace capture stores `output` as the *whole* rendered judge prompt with
    the evaluator's `{{...}}` variables already substituted (fetch_traces keeps
    it verbatim because evaluators wrap variables differently). To show the
    annotator the actual inputs — not the entire prompt — we split the template
    on its placeholders and match the rendered string against the literal
    segments around them, capturing what each variable was bound to.

    Returns `[{name, value}]` in template order (deduped by name, first
    occurrence wins) or None when the template does not match the rendering
    (e.g. trace capture altered the wrapping), so the caller can fall back to
    showing the raw rendered output.
    """
    if not template or not rendered:
        return None
    names: list[str] = []
    parts: list[str] = []
    last = 0
    for m in _PLACEHOLDER.finditer(template):
        parts.append(re.escape(template[last:m.start()]))
        parts.append('(.*?)')
        names.append(m.group(1).strip())
        last = m.end()
    if not names:
        return None
    parts.append(re.escape(template[last:]))
    match = re.match('^' + ''.join(parts) + '$', rendered, re.DOTALL)
    if not match:
        return None
    ordered: list[dict[str, str]] = []
    seen: set[str] = set()
    for name, value in zip(names, match.groups()):
        if name in seen:
            continue
        seen.add(name)
        ordered.append({'name': name, 'value': value})
    return ordered


def _is_confuser(e: dict[str, Any]) -> bool:
    # Measurable AND unstable — the unified, type-agnostic replacement for the
    # boolean flip test (§5.6). Keys off `instability`, never on type.
    inst = e.get('instability')
    return isinstance(inst, (int, float)) and inst > 0.0


def _is_low_instability(e: dict[str, Any]) -> bool:
    # Measurable AND perfectly consistent (instability 0) — exactly the bucket
    # that can hide a *consistent* bias and never surfaces in the ranking, so it
    # feeds the low-instability sanity sample.
    return e.get('instability') == 0.0 and (e.get('n_successful_repeats') or 0) >= 2


def _verdict_space(output_type: str, labels: list[str], scale: tuple[float, float] | None) -> dict[str, Any]:
    """The self-contained verdict space attached to the queue + each item, so any
    downstream consumer (Part 2 chat clustering, an optional point view) needs no
    knowledge of the on-disk formats."""
    if output_type == 'categorical':
        return {'type': 'categorical', 'labels': labels, 'k': len(labels)}
    if output_type in {'number', 'numeric'}:
        return {'type': 'number', 'scale': list(scale) if scale else None}
    if output_type == 'string':
        return {'type': 'string'}  # free-form: no label set, no scale
    return {'type': 'boolean', 'labels': [False, True]}


def _display_item(
    rank: int, e: dict[str, Any], low_flip: bool, template: str, verdict_space: dict[str, Any],
    src: dict[str, Any], reason: str = 'instability',
) -> dict[str, Any]:
    # The judged input (query/output/messages) comes from `src` — the stability.json
    # row for this datapoint — not from the metrics entry `e`, which no longer
    # carries it (metrics.json stays lean so the conductor's report read can't slurp
    # every input; see metrics.py).
    output = src.get('output', '')
    return {
        'rank': rank,
        'source_index': e.get('source_index'),
        'low_flip_sample': low_flip,
        # Why this datapoint is in the queue: 'instability' (self-inconsistent),
        # 'cross_model' (two models disagree — §11.3 opt 4), or 'low_flip' (sanity).
        'reason': reason,
        # A seeded row's provenance flows through from stability (§11.6), if present.
        'synthetic': src.get('synthetic', False),
        'source': src.get('source'),
        'query': src.get('query', ''),
        'output': output,
        # Variables recovered from the rendered prompt so the UI can show the
        # actual judged inputs instead of the entire judge prompt. None when the
        # template could not be inverted; the UI falls back to raw `output`.
        'variables': _invert_template(template, output),
        'messages': src.get('messages'),
        # Self-contained verdict space (§5.6): the Part 1 → Part 2 boundary.
        'verdict_space': verdict_space,
        'ambiguity': {
            'instability': e.get('instability'),
            'band': e.get('band'),
            # Boolean-only legacy detail (None for categorical / numeric).
            'flip_rate': e.get('flip_rate'),
            'mode_value': e.get('mode_value'),
            'mode_rate': e.get('mode_rate'),
            'n_repeats': e.get('n_successful_repeats'),
        },
        'judge_votes': {
            'n_true': e.get('n_true'),        # boolean
            'n_false': e.get('n_false'),      # boolean
            'counts': e.get('counts'),        # categorical
            'mean': e.get('mean'),            # numeric
            'stdev': e.get('stdev'),          # numeric
            'representative_explanation': e.get('representative_explanation'),
        },
    }


def _project_grey_zone(queue: dict[str, Any], cfg: dict) -> dict[str, Any] | None:
    """What the step-6 grey-zone payload will cost in the conductor's context (§12.4).

    The context budget rides on the count the user just chose rather than adding a
    gate of its own, so the projection is reported here. It runs the *same* pure
    `assemble_payload` that step 6 will run — a projection computed a second way
    would eventually promise one thing and deliver another.

    Returns None (and warns) if sizing fails: the queue is the artifact that matters.
    """
    try:
        payload = gzlib.assemble_payload(
            queue,
            top_k=cfg.get('grey_zone_top_k'),
            max_chars=int(cfg.get('grey_zone_max_chars', 600)),
            max_tokens=int(cfg.get('grey_zone_max_tokens', 60000)),
        )
        return payload['budget']
    except Exception:  # noqa: BLE001 — never cost the user their queue over a courtesy
        logger.warning('⚠ Could not project the grey-zone context budget; the queue is unaffected.')
        return None


def _log_projection(projection: dict[str, Any] | None, n_items: int) -> None:
    """Tell the user, in the same breath as the count they chose, how much of it
    will actually reach the conductor's context at step 6."""
    if not projection:
        return
    logger.info(
        f'  Grey-zone context projection: ~{projection["estimated_tokens"]} tokens of '
        f'{projection["budget_tokens"]}.'
    )
    dropped = projection.get('n_dropped_by_budget') or 0
    if dropped:
        logger.warning(
            f'⚠ {n_items - dropped} of {n_items} will enter context at step 6 (the most '
            f'unstable); {dropped} exceed the token budget. Tell the user before clustering.'
        )


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    count: int = -1,
    low_flip_sample_size: int | None = None,
) -> str:
    """Build the annotation queue.

    Args:
        run_dir: Run directory (defaults to most recent).
        config: TOML config path.
        count: How many top-ambiguous (flipped) items to annotate. -1 = all
            flipped items; 0 = none (low-flip sanity sample only). Choose this
            after reading the step-5 flip report.
        low_flip_sample_size: Random low-flip items to append as a sanity check
            (overrides config `low_flip_sample_size`; 0 disables).
    """
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run metrics.py first.')

    metrics = runner.read_json(out_dir / 'metrics.json')
    per_row = metrics.get('per_row', [])
    # The judged input lives in stability.json (metrics.json is kept lean); index it
    # by source_index so each queue item can carry its actual input.
    stab_path = out_dir / 'stability.json'
    stability = runner.read_json(stab_path) if stab_path.exists() else {}
    inputs_by_idx = {r.get('source_index'): r for r in stability.get('rows', [])}
    low_n = cfg.get('low_flip_sample_size', 5) if low_flip_sample_size is None else int(low_flip_sample_size)
    seed = int(cfg.get('seed', 42))

    # The judge prompt template lets us invert each rendered datapoint back into
    # its `{{variable}}` bindings for the annotation UI (kept visible there too).
    # The same record carries the verdict space (§5.6) attached to every item.
    ev_path = out_dir / 'evaluator.json'
    ev = runner.read_json(ev_path) if ev_path.exists() else {}
    template = ev.get('prompt') or ''
    output_type = (ev.get('output_type') or metrics.get('metadata', {}).get('output_type') or 'boolean').strip().lower()
    labels = ev.get('categorical_labels') or []
    scale_raw = ev.get('scale')
    scale = tuple(scale_raw) if isinstance(scale_raw, (list, tuple)) and len(scale_raw) == 2 else None
    verdict_space = _verdict_space(output_type, labels, scale)

    flipped = [e for e in per_row if _is_confuser(e)]  # already most-unstable-first
    # -1 (or any negative) = all; count >= 0 = take exactly that many (0 → none, so
    # the queue is the low-flip sanity sample only). `if count and count > 0` used to
    # fold 0 into "all", an undocumented trap.
    if count >= 0:
        flipped = flipped[:count]
    flipped_idx = {e.get('source_index') for e in flipped}

    # Cross-model disagreers (§11.3 opt 4): datapoints two models split on are
    # confusers even at instability 0, so add any not already flipped.
    cm_path = out_dir / 'cross_model.json'
    cross_idx = set(runner.read_json(cm_path).get('disagreeing_indices', [])) if cm_path.exists() else set()
    cross_only = [e for e in per_row if e.get('source_index') in cross_idx and e.get('source_index') not in flipped_idx]

    confusers = [(e, 'instability') for e in flipped] + [(e, 'cross_model') for e in cross_only]
    items = [
        _display_item(i + 1, e, False, template, verdict_space, inputs_by_idx.get(e.get('source_index'), {}), reason)
        for i, (e, reason) in enumerate(confusers)
    ]

    low_pool = [e for e in per_row if _is_low_instability(e)]
    sampled_low: list[dict[str, Any]] = []
    if low_n > 0 and low_pool:
        rng = random.Random(seed)
        sampled_low = rng.sample(low_pool, min(low_n, len(low_pool)))
        start = len(items)
        items.extend(
            _display_item(start + i + 1, e, True, template, verdict_space,
                          inputs_by_idx.get(e.get('source_index'), {}), 'low_flip')
            for i, e in enumerate(sampled_low)
        )

    queue = {
        'meta': {
            'evaluator_id': metrics.get('metadata', {}).get('evaluator_id'),
            'evaluator_key': metrics.get('metadata', {}).get('evaluator_key'),
            'judge_model': metrics.get('metadata', {}).get('judge_model'),
            'verdict_space': verdict_space,  # the judge's own verdict space (per type)
            'eval_prompt': template,  # shown in the UI for context on how variables are used
            'n_flipped_items': len(flipped),
            'n_cross_model': len(cross_only),
            'n_low_flip_sample': len(sampled_low),
            'n_items': len(items),
        },
        'items': items,
    }
    queue['meta']['grey_zone_projection'] = _project_grey_zone(queue, cfg)
    runner.write_json(out_dir / 'queue.json', queue)
    logger.info(
        f'✓ Wrote {out_dir / "queue.json"}: {len(flipped)} flipped + '
        f'{len(cross_only)} cross-model + {len(sampled_low)} low-flip sanity items = {len(items)} to annotate'
    )
    _log_projection(queue['meta']['grey_zone_projection'], len(items))
    if not flipped and not cross_only:
        logger.warning(
            '⚠ No confusers. The judge was unanimous everywhere at this temperature '
            'and no cross-model disagreements were found — the flip queue is empty. '
            'Raise temperature, seed data (dataset/synthetic), add a second model, or '
            'rely on the low-flip sanity sample (design §8 / §11).'
        )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
