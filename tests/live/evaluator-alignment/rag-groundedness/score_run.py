# /// script
# requires-python = ">=3.11"
# dependencies = ["fire>=0.7.0"]
# ///
"""Score an evaluator-alignment run against this test case's answer key.

    uv run tests/live/evaluator-alignment/rag-groundedness/score_run.py \
        --run_dir skills/orq-evaluator-alignment/runs/<key>_<ts>_<model>_<N>dp

Run from the repo root.

Joins `stability.json` back onto `answer_key.json` on the ANSWER TEXT, because
`dataset_inputs.py pull` does not preserve the authoring order of datapoints.json
— `source_index` is the API's order, not ours.

Reports, per case: the judge's verdict tally, its instability, and whether the
majority verdict matches the reference policy. Then the two checks that matter:

  PC1  do the grey-zone cases outrank the anchors on instability?
  PC3  which cases are STABLE AND WRONG — the blind spot instability cannot see?

Also diffs this run's wrong-case ids against the ones `measured_baseline` records for
this judge. The recorded agreement figure is a count from a past run; the id lists
beside it are what a new run can actually be checked against, so this reports both
and names the difference instead of asserting either.

Failed repetitions come back as null and are excluded from the majority, not
counted as a verdict.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import fire

HERE = Path(__file__).resolve().parent


def _majority(reps: list[Any]) -> tuple[Any, Counter, int]:
    """Modal verdict over the non-null repetitions, plus the failure count."""
    values = [r for r in reps if r is not None]
    tally = Counter(json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for v in values)
    top = tally.most_common(1)[0][0] if tally else None
    return top, tally, len(reps) - len(values)


def _matches(expected: Any, actual: Any, output_type: str, tol: float) -> bool:
    if expected is None or actual is None:
        return False
    if output_type == 'number':
        try:
            return abs(float(expected) - float(actual)) <= tol
        except (TypeError, ValueError):
            return False
    return str(expected).strip().lower() == str(actual).strip().lower()


def _baseline_wrong_ids(baseline: dict[str, Any]) -> set[str]:
    """The wrong cases a recorded baseline NAMES, as one set.

    `stable_but_wrong_cases` and `unstable_and_wrong_cases` partition the misses —
    same verdict every repeat vs. not — so the union is every case that baseline
    identified. It is not necessarily every case that baseline COUNTED: the
    flash-lite row records 18/24 against five named ids, and the sixth is simply
    unrecorded — `cases[]` holds expected verdicts only, so nothing in the repo
    remembers what that judge returned. Hence report, don't reconcile.
    """
    return set(baseline.get('stable_but_wrong_cases') or []) | set(baseline.get('unstable_and_wrong_cases') or [])


def _compare_to_baseline(key_doc: dict[str, Any], judge: str, rows: list[dict[str, Any]]) -> None:
    """Put this run's wrong-case ids next to the recorded ones and name the difference.

    The agreement figure in `answer_key.json` is prose from a past run, so nothing
    stops it drifting from the id lists directly above it — and it has. This does not
    try to decide which is right: it prints the recorded figure, the recorded ids and
    the ids THIS run got wrong, which is the only one of the three the reader can act
    on. A judge that has genuinely moved shows up as a named id, not as a number that
    changed for unknown reasons.
    """
    baseline = (key_doc.get('measured_baseline') or {}).get(judge)
    if not isinstance(baseline, dict):
        print(f'\nbaseline: no recorded baseline for {judge} — nothing to compare against.')
        return

    expected_wrong = _baseline_wrong_ids(baseline)
    recorded = baseline.get('agreement_with_reference_policy', '(no figure recorded)')
    print(f'\nbaseline for {judge}: recorded {recorded}')
    print(f'     named wrong there ({len(expected_wrong)}): {", ".join(sorted(expected_wrong)) or "none"}')

    actual_wrong = {r['id'] for r in rows if not r['agrees']}
    print(f'     wrong here      ({len(actual_wrong)}): {", ".join(sorted(actual_wrong)) or "none"}')
    if actual_wrong == expected_wrong:
        print('     Same id set. This run reproduces the recorded blind spot exactly.')
        return
    newly_wrong = sorted(actual_wrong - expected_wrong)
    newly_right = sorted(expected_wrong - actual_wrong)
    if newly_wrong:
        print(f'     wrong here but not named in the baseline: {", ".join(newly_wrong)}')
    if newly_right:
        print(f'     named in the baseline but right here:     {", ".join(newly_right)}')
    print('     If this run is the better measurement, update measured_baseline from it —')
    print('     both the id lists AND the figure, so the two keep saying the same thing.')


def main(run_dir: str, tol: float = 0.5) -> None:
    run = Path(run_dir).resolve()
    stability = json.loads((run / 'stability.json').read_text(encoding='utf-8'))
    metrics = json.loads((run / 'metrics.json').read_text(encoding='utf-8'))
    key_doc = json.loads((HERE / 'answer_key.json').read_text(encoding='utf-8'))
    datapoints = json.loads((HERE / 'datapoints.json').read_text(encoding='utf-8'))

    output_type = stability['metadata']['output_type']
    judge = stability['metadata']['judge_model']
    expected_field = 'number' if output_type in {'number', 'numeric'} else output_type

    case_of_answer = {d['inputs']['output'].strip(): d['_case_id'] for d in datapoints}
    key = {c['id']: c for c in key_doc['cases']}
    instability_of = {r['source_index']: r.get('instability') for r in metrics.get('per_row', [])}

    print(f'run:   {run.name}')
    print(f'judge: {judge}   type: {output_type}\n')
    header = f"{'case':<5} {'class':<11} {'zone':<5} {'expected':<18} {'judge majority':<18} {'instab':>6} {'fail':>4}  verdict"
    print(header)
    print('-' * len(header))

    rows: list[dict[str, Any]] = []
    for row in stability['rows']:
        case_id = case_of_answer.get((row.get('output') or '').strip())
        if case_id is None:
            print(f"{'?':<5} unmatched row source_index={row.get('source_index')}")
            continue
        entry = key[case_id]
        expected = entry['expected'].get(expected_field)
        actual, _tally, n_failed = _majority(row.get('repetitions') or [])
        inst = instability_of.get(row['source_index'])
        agrees = _matches(expected, actual, expected_field, tol)
        rows.append({
            'id': case_id, 'class': entry['class'], 'cluster': entry.get('cluster'),
            'expected': expected, 'actual': actual, 'instability': inst, 'agrees': agrees,
        })
        print(
            f"{case_id:<5} {entry['class']:<11} {str(entry.get('cluster') or '-'):<5} "
            f"{str(expected):<18} {str(actual):<18} "
            f"{('-' if inst is None else format(inst, '.2f')):>6} {n_failed:>4}  "
            f"{'ok' if agrees else 'DISAGREES'}"
        )

    if not rows:
        raise SystemExit('No rows matched the answer key — is this the right run directory?')

    print('-' * len(header))
    agree = sum(r['agrees'] for r in rows)
    print(f'agreement with reference policy: {agree}/{len(rows)}')
    _compare_to_baseline(key_doc, judge, rows)

    # PC1 — instability must rank the engineered ambiguity above the anchors.
    unstable = [r for r in rows if (r['instability'] or 0) > 0]
    grey_unstable = [r for r in unstable if r['class'] == 'grey']
    anchor_unstable = [r for r in unstable if r['class'].startswith('anchor')]
    print(
        f'\nPC-1 instability ranking: {len(unstable)} unstable '
        f'({len(grey_unstable)} grey, {len(anchor_unstable)} anchor). '
        + ('PASS' if unstable and len(grey_unstable) > len(anchor_unstable) else 'CHECK')
    )
    if unstable:
        ranked = sorted(unstable, key=lambda r: -(r['instability'] or 0))
        print('     ' + ', '.join(f"{r['id']}={r['instability']:.2f}" for r in ranked))
    else:
        print('     Flat profile — this is the SKILL.md step-4a starved-run branch.')

    # PC3 — the blind spot: confidently wrong, therefore invisible to ranking.
    blind = [r for r in rows if not r['agrees'] and (r['instability'] or 0) == 0]
    print(f'\nPC-3 stable-but-wrong (invisible to instability ranking): {len(blind)}')
    for r in blind:
        print(f"     {r['id']:<5} ({r['class']}) expected {r['expected']!r}, judge said {r['actual']!r} with zero disagreement")
    print('\n     These reach a human ONLY via the low_flip sanity sample. The conductor\'s')
    print('     final summary must state this caveat — that is the point of the test case.')


if __name__ == '__main__':
    fire.Fire(main)
