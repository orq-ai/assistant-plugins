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


def _is_flipped(e: dict[str, Any]) -> bool:
    fr = e.get('flip_rate')
    return isinstance(fr, (int, float)) and fr > 0.0


def _is_low_flip(e: dict[str, Any]) -> bool:
    # Measurable (>=2 repeats) AND unanimous — the judge was perfectly
    # consistent here, which is exactly the bucket that can hide a consistent
    # bias and never surfaces in the flip ranking.
    return e.get('flip_rate') == 0.0 and (e.get('n_successful_repeats') or 0) >= 2


def _display_item(rank: int, e: dict[str, Any], low_flip: bool, template: str) -> dict[str, Any]:
    return {
        'rank': rank,
        'source_index': e.get('source_index'),
        'low_flip_sample': low_flip,
        'query': e.get('query', ''),
        'output': e.get('output', ''),
        # Variables recovered from the rendered prompt so the UI can show the
        # actual judged inputs instead of the entire judge prompt. None when the
        # template could not be inverted; the UI falls back to raw `output`.
        'variables': _invert_template(template, e.get('output', '')),
        'messages': e.get('messages'),
        'ambiguity': {
            'flip_rate': e.get('flip_rate'),
            'mode_value': e.get('mode_value'),
            'mode_rate': e.get('mode_rate'),
            'n_repeats': e.get('n_successful_repeats'),
        },
        'judge_votes': {
            'n_true': e.get('n_true'),
            'n_false': e.get('n_false'),
            'representative_explanation': e.get('representative_explanation'),
        },
    }


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
            flipped items. Choose this after reading the step-5 flip report.
        low_flip_sample_size: Random low-flip items to append as a sanity check
            (overrides config `low_flip_sample_size`; 0 disables).
    """
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run metrics.py first.')

    metrics = runner.read_json(out_dir / 'metrics.json')
    per_row = metrics.get('per_row', [])
    low_n = cfg.get('low_flip_sample_size', 5) if low_flip_sample_size is None else int(low_flip_sample_size)
    seed = int(cfg.get('seed', 42))

    # The judge prompt template lets us invert each rendered datapoint back into
    # its `{{variable}}` bindings for the annotation UI (kept visible there too).
    ev_path = out_dir / 'evaluator.json'
    template = (runner.read_json(ev_path).get('prompt') or '') if ev_path.exists() else ''

    flipped = [e for e in per_row if _is_flipped(e)]  # already most-ambiguous-first
    if count and count > 0:
        flipped = flipped[:count]

    items = [_display_item(i + 1, e, low_flip=False, template=template) for i, e in enumerate(flipped)]

    low_pool = [e for e in per_row if _is_low_flip(e)]
    sampled_low: list[dict[str, Any]] = []
    if low_n > 0 and low_pool:
        rng = random.Random(seed)
        sampled_low = rng.sample(low_pool, min(low_n, len(low_pool)))
        start = len(items)
        items.extend(_display_item(start + i + 1, e, low_flip=True, template=template) for i, e in enumerate(sampled_low))

    queue = {
        'meta': {
            'evaluator_id': metrics.get('metadata', {}).get('evaluator_id'),
            'evaluator_key': metrics.get('metadata', {}).get('evaluator_key'),
            'judge_model': metrics.get('metadata', {}).get('judge_model'),
            'label_scheme': ['false', 'true'],  # the judge's own boolean verdict space
            'eval_prompt': template,  # shown in the UI for context on how variables are used
            'n_flipped_items': len(flipped),
            'n_low_flip_sample': len(sampled_low),
            'n_items': len(items),
        },
        'items': items,
    }
    runner.write_json(out_dir / 'queue.json', queue)
    logger.info(
        f'✓ Wrote {out_dir / "queue.json"}: {len(flipped)} flipped + '
        f'{len(sampled_low)} low-flip sanity items = {len(items)} to annotate'
    )
    if not flipped:
        logger.warning(
            '⚠ No flipped datapoints. The judge was unanimous everywhere at this '
            'temperature — the flip queue is empty. Raise temperature or rely on '
            'the low-flip sanity sample (design §8).'
        )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
