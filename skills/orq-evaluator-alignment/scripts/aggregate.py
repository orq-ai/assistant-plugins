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
"""Step 8b — consolidate type-aware recommendations into the rewrite input.

Reads `recommendations.json` (per-datapoint, type-aware — each carries its typed
disagreement: boolean flip / categorical confusion-pair / numeric signed-error,
plus the human's reason) and writes two artifacts (RES-978 Part 2 §2.2):

  - `aggregated.md`   — one actionable, human-readable summary: the changes to
    make (grouped disagreements, with the type-native disagreement detail and
    the human reasons surfaced) and the strengths to preserve (agreements). This
    is the `input_instructions` the PO2 rewrite (step 9) reads.
  - `aggregated.json` — the same content structured, so the rewrite step (or any
    programmatic consumer) can read the typed disagreements without re-parsing
    markdown: `{metadata, disagreements[], agreements[]}` where each disagreement
    carries `source_index`, `kind`, `judge_value`, `human_value`, the type-native
    detail (`confusion_pair` / `signed_error` / `direction`), `reason`, and the
    LLM-backed `recommendation`.

Deterministic (no LLM call): the meta-prompt already produced each
generalizable, prompt-level recommendation; aggregation just splits by
agreement, groups by type-native disagreement signature, and surfaces the human
reasons. The conductor may still refine `aggregated.md` in-context before the
rewrite — it is a plain markdown file.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/aggregate.py --run_dir runs/<key>_<ts>
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import fire
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner

_NUMERIC_TYPES = {'number', 'numeric'}


def _signature(rec: dict[str, Any]) -> str:
    """A type-native grouping key: cluster disagreements by the SAME failure
    shape, not by identical recommendation text.

    boolean     → the flip direction (judge True→human False vs the reverse);
    categorical → the confusion pair (judge label → human label);
    numeric     → the signed direction (judge over- vs under-scored).
    Falls back to the recommendation text for any untyped/legacy record.
    """
    d = rec.get('disagreement') or {}
    kind = d.get('kind')
    if kind == 'flip':
        return f'flip: judge={d.get("judge_value")} → human={d.get("human_value")}'
    if kind == 'confusion_pair':
        pair = d.get('confusion_pair') or (d.get('judge_value'), d.get('human_value'))
        return f'confusion: judge="{pair[0]}" → human="{pair[1]}"'
    if kind == 'signed_error':
        return f'{d.get("direction")}-scored (judge {d.get("direction")}-rates this class)'
    return (rec.get('recommendation') or '').strip() or f'#{rec.get("source_index")}'


def _detail_str(rec: dict[str, Any]) -> str:
    """Per-datapoint type-native disagreement detail for the markdown citation."""
    d = rec.get('disagreement') or {}
    kind = d.get('kind')
    idx = rec.get('source_index')
    if kind == 'flip':
        return f'#{idx}: judge={d.get("judge_value")}, human={d.get("human_value")}'
    if kind == 'confusion_pair':
        return f'#{idx}: judge="{d.get("judge_value")}" vs human="{d.get("human_value")}"'
    if kind == 'signed_error':
        return (
            f'#{idx}: judge={_num(d.get("judge_value"))}, human={_num(d.get("human_value"))} '
            f'({d.get("direction")} by {_num(d.get("magnitude"))})'
        )
    return f'#{idx}'


def _num(v: Any) -> str:
    return f'{v:.2f}' if isinstance(v, (int, float)) else str(v)


def _group(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group records by type-native signature, first-seen order preserved."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for r in recs:
        sig = _signature(r)
        if sig not in grouped:
            grouped[sig] = []
            order.append(sig)
        grouped[sig].append(r)
    return [{'signature': sig, 'members': grouped[sig]} for sig in order]


def _reason_lines(members: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for m in members:
        reason = (m.get('reason') or '').strip()
        if reason:
            lines.append(f'    - #{m.get("source_index")} human reason: {reason}')
    return lines


def _changes_section(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return '## Changes to make (from disagreements)\n\n_(none — human agreed with the judge everywhere)_\n'
    lines = ['## Changes to make (from disagreements)\n']
    for g in groups:
        members = g['members']
        cites = ', '.join(_detail_str(m) for m in sorted(members, key=lambda r: r.get('source_index') or 0))
        # Representative recommendation: the first non-empty in the group.
        rec_text = next((m.get('recommendation') for m in members if (m.get('recommendation') or '').strip()), '')
        lines.append(f'### {g["signature"]}  \n_({len(members)} datapoint(s): {cites})_\n')
        if rec_text:
            lines.append(f'- **Recommended rubric change:** {rec_text}')
        reasons = _reason_lines(members)
        if reasons:
            lines.append('- Grounding (human reasons):')
            lines.extend(reasons)
        lines.append('')
    return '\n'.join(lines)


def _strengths_section(recs: list[dict[str, Any]]) -> str:
    if not recs:
        return '## Strengths to preserve (from agreements)\n\n_(none)_\n'
    lines = ['## Strengths to preserve (from agreements)\n']
    grouped: dict[str, list[int]] = {}
    order: list[str] = []
    for r in recs:
        text = (r.get('recommendation') or '').strip()
        if not text:
            continue
        if text not in grouped:
            grouped[text] = []
            order.append(text)
        grouped[text].append(r.get('source_index'))
    if not order:
        return '## Strengths to preserve (from agreements)\n\n_(none)_\n'
    for text in order:
        idxs = grouped[text]
        cites = ', '.join(f'#{i}' for i in sorted(idxs))
        lines.append(f'- {text}  \n  _(from {len(idxs)} datapoint(s): {cites})_')
    return '\n'.join(lines) + '\n'


def _structured(disagree_groups: list[dict[str, Any]], agreements: list[dict[str, Any]],
                metadata: dict[str, Any]) -> dict[str, Any]:
    """The machine-readable consolidation the rewrite step can consume directly."""
    disagreements = [
        {
            'signature': g['signature'],
            'n': len(g['members']),
            'source_indices': sorted(m.get('source_index') for m in g['members']),
            'kind': (g['members'][0].get('disagreement') or {}).get('kind'),
            'members': [
                {
                    'source_index': m.get('source_index'),
                    'disagreement': m.get('disagreement'),
                    'reason': m.get('reason'),
                    'recommendation': m.get('recommendation'),
                    'low_flip_sample': m.get('low_flip_sample', False),
                }
                for m in g['members']
            ],
        }
        for g in disagree_groups
    ]
    return {
        'metadata': metadata,
        'disagreements': disagreements,
        'agreements': [
            {
                'source_index': r.get('source_index'),
                'recommendation': r.get('recommendation'),
                'reason': r.get('reason'),
                'low_flip_sample': r.get('low_flip_sample', False),
            }
            for r in agreements
        ],
    }


def main(run_dir: str | None = None, config: str = 'config.toml') -> str:
    """Aggregate type-aware recommendations into aggregated.md + aggregated.json."""
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run recommend.py first.')

    data = runner.read_json(out_dir / 'recommendations.json')
    recs = [r for r in data.get('recommendations', []) if r.get('success')]
    if not recs:
        raise SystemExit('No successful recommendations to aggregate.')

    meta = data.get('metadata', {})
    output_type = meta.get('output_type', 'boolean')

    # A recommendation is a disagreement iff recommend.py attached a typed
    # disagreement record. `agreement`/`disagreement` are authoritative — no
    # re-derivation, no re-coercion of stringified verdicts here.
    disagreements = [r for r in recs if r.get('disagreement')]
    agreements = [r for r in recs if not r.get('disagreement')]

    disagree_groups = _group(disagreements)
    kinds = Counter((r['disagreement'] or {}).get('kind') for r in disagreements)
    n_low_flip = sum(1 for r in recs if r.get('low_flip_sample'))

    header = (
        f'# Aggregated recommendations ({output_type} evaluator)\n\n'
        f'{len(recs)} annotation(s) analysed: **{len(disagreements)} disagreement(s)** '
        f'(human ≠ judge) and **{len(agreements)} agreement(s)**'
        + (f', incl. {n_low_flip} from the low-flip sanity sample' if n_low_flip else '')
        + '.\n\n'
        + (f'Disagreement kinds: {dict(kinds)}.\n\n' if kinds else '')
        + '> These instructions are the input to the PO2 rewrite (step 9). Edit this '
        'file before running rewrite_eval.py if you want to adjust, drop, or '
        're-prioritise any item. The rewrite must PRESERVE the verdict space '
        f'(output_type={output_type}) — never drop a declared label or move a scale.\n'
    )

    body = '\n'.join(
        [
            header,
            _changes_section(disagree_groups),
            _strengths_section(agreements),
        ]
    )
    agg_path = out_dir / 'aggregated.md'
    # grey_zone.py apply and this aggregate flow are *alternative* producers of the
    # same guidance artifact; warn (don't silently clobber) if the other wrote it.
    if agg_path.exists() and agg_path.read_text(encoding='utf-8-sig').strip():
        logger.warning(
            f'Replacing existing {agg_path.name} (from a prior grey-zone apply or '
            'aggregate run) with these aggregated recommendations.'
        )
    runner.write_text(agg_path, body)

    structured_meta = {
        'output_type': output_type,
        'labels': meta.get('labels', []),
        'numeric_tolerance': meta.get('numeric_tolerance'),
        'n_annotations': len(recs),
        'n_disagreements': len(disagreements),
        'n_agreements': len(agreements),
        'disagreement_kinds': dict(kinds),
        'n_low_flip_sample': n_low_flip,
    }
    runner.write_json(out_dir / 'aggregated.json', _structured(disagree_groups, agreements, structured_meta))

    logger.info(
        f'✓ Wrote {out_dir / "aggregated.md"} + aggregated.json '
        f'({len(disagreements)} change group(s) from {len(disagreements)} disagreement(s), '
        f'{len(agreements)} affirmation(s))'
    )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
