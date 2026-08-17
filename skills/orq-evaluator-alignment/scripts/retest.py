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
"""Step 8 — retest / validation of the human-aligned evaluator (RES-978 §2.5).

Part 1 could only tell whether a judge was *stable*, not whether it was *right*:
a judge that is consistently wrong but never flips scores a perfect instability of
0. So the retest gates a rewrite on BOTH signals, over the same confusers:

  (a) instability DROPPED  — new evaluator's mean instability < the baseline's, and
  (b) verdicts AGREE with the human labels — `lib.agreement` between the new
      evaluator's majority verdict and the human `value` per confuser
      (boolean: TPR/TNR; categorical: accuracy; numeric: MAE / within-tolerance).

Report success = (a) AND (b). (a) alone is gameable; (b) keeps it honest.

**Both signals are weaker than they look, and the report says so rather than
quietly rounding up.** Three things bias them, none of which the numbers reveal:

  1. *Selection.* The retested rows were chosen for their highest observed
     instability, so re-measuring them regresses toward the mean whatever the new
     judge does. `--baseline_rerun` re-runs the OLD judge over the same rows in the
     same pass, which is the only comparison that isolates the rewrite.
  2. *Overlap.* The grey-zone answers drove the rewrite and then score it. There is
     no holdout in v1; the caveat is stated instead of implied.
  3. *Provenance.* The human answers a rule question and the conductor derives each
     point's label from it. Unless those labels were read back and confirmed, (b)
     scores the rewrite against one model's reading of the rule.

Signal (b) also carries a **before** number, computed for free from the original
run's `stability.json` — without it, "accuracy 0.78, PASS" can be a regression from
0.85 and reads as a win.

Orchestration: build a `retest/` sub-run whose `evaluator.json` is the NEW
evaluator and whose `traces.jsonl` is the original run's confusers, then re-run
the EXISTING `stability.py` + `metrics.py` mains over it (imported, never
modified). Writes `retest_metrics.json` in the parent run dir.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/retest.py --run_dir runs/<key>_<ts>
    uv run scripts/retest.py --run_dir runs/<key>_<ts> --baseline_rerun   # true A/B
    uv run scripts/retest.py --run_dir runs/<key>_<ts> --num_samples 2    # smoke
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import grey_zone, runner
from lib.content import traces_fingerprint

load_dotenv()

# The two signals default to strict thresholds; overridable via config/CLI. `(a)`
# is a strict drop (new < old). `(b)`'s bar is type-native and lives in the config
# keys read below (accuracy for bool/categorical, within-tolerance for numeric).
_DEFAULT_MIN_ACCURACY = 0.7
_DEFAULT_MIN_TPR = 0.7
_DEFAULT_MIN_TNR = 0.7
_DEFAULT_MIN_WITHIN_TOL = 0.7
_DEFAULT_NUMERIC_TOL = 0.5
# Matches metrics.py / stability.py. Only fires on a config missing the key.
_DEFAULT_N_REPEATS = 5


def _new_evaluator_config(new_eval: dict[str, Any], source_eval: dict[str, Any]) -> dict[str, Any]:
    """The `evaluator.json` for the retest sub-run: the NEW evaluator's prompt +
    model, carrying the verdict space forward from the source so stability.py /
    metrics.py dispatch to the same type. §3: new_evaluator.json records
    output_type + categorical_labels/scale; older runs fall back to the source's.
    """
    output_type = (
        new_eval.get('output_type') or source_eval.get('output_type') or 'boolean'
    )
    categorical_labels = (
        new_eval.get('categorical_labels')
        if new_eval.get('categorical_labels') is not None
        else source_eval.get('categorical_labels', [])
    )
    scale = new_eval.get('scale') if new_eval.get('scale') is not None else source_eval.get('scale')
    return {
        'id': new_eval['id'],
        'key': new_eval.get('key'),
        'prompt': new_eval['prompt'],
        'judge_model': new_eval['judge_model'],
        'output_type': output_type,
        'categorical_labels': categorical_labels or [],
        'scale': scale,
        'variables': source_eval.get('variables', []),
        'source_evaluator_id': new_eval.get('source_evaluator_id') or source_eval.get('id'),
        'raw': new_eval.get('raw', {}),
    }


def _materialize_subrun(
    out_dir: Path, sub: str, evaluator: dict[str, Any], selected: list[tuple[int, dict[str, Any]]]
) -> tuple[Path, dict[int, int]]:
    """Write a `<sub>/` sub-run holding `evaluator` and the chosen rows.

    `source_index` is the row's *position* in `traces.jsonl`, so a filtered set
    renumbers it. Returns `(dir, index_map)` mapping the new position back to the
    original index, which is what the human labels are keyed by.
    """
    sub_dir = out_dir / sub
    sub_dir.mkdir(parents=True, exist_ok=True)
    index_map = {new_i: original_i for new_i, (original_i, _row) in enumerate(selected)}
    runner.write_json(sub_dir / 'evaluator.json', evaluator)
    runner.write_jsonl(sub_dir / 'traces.jsonl', [row for _i, row in selected])
    runner.write_json(sub_dir / 'index_map.json', {str(k): v for k, v in index_map.items()})
    return sub_dir, index_map


def _select_rows(
    out_dir: Path, wanted: set[int] | None, num_samples: int | None
) -> list[tuple[int, dict[str, Any]]]:
    """`(original_index, row)` for the rows to re-judge, in file order.

    `wanted=None` re-judges everything (`--all_rows`). `num_samples` caps the
    selection **after** scoping, so the instability comparison is recomputed over
    exactly the rows that were re-judged: it used to narrow only the retest run
    while the "before" mean stayed over the full label set, which is the
    subset-vs-full-run artifact the rest of this file exists to avoid.
    """
    rows = runner.read_jsonl(out_dir / 'traces.jsonl')
    if not rows:
        raise SystemExit(f'No datapoints in {out_dir / "traces.jsonl"} — nothing to retest.')
    if wanted is None:
        selected = list(enumerate(rows))
    else:
        selected = [(i, r) for i, r in enumerate(rows) if i in wanted]
        if not selected:
            raise SystemExit(
                'None of the labelled rows are present in traces.jsonl — cannot retest. '
                '(Were the labels written against a different run directory?)'
            )
    if num_samples is not None and num_samples >= 0:
        selected = selected[:num_samples]
    return selected


def _check_fingerprint(out_dir: Path, original_metrics: dict[str, Any]) -> None:
    """Refuse when traces.jsonl was rewritten after the run that produced the labels.

    Labels are keyed by position in traces.jsonl and `fetch_traces` rewrites that
    file wholesale, so re-fetching between labelling and retesting silently pairs
    each label to a different datapoint. Only a *total* miss raised before; partial
    overlap produced a clean-looking agreement score for a comparison that never
    happened.
    """
    recorded = original_metrics.get('metadata', {}).get('traces_fingerprint')
    if not recorded:
        return  # run dir predates the fingerprint; nothing to compare against
    current = traces_fingerprint(runner.read_jsonl(out_dir / 'traces.jsonl'))
    if current != recorded:
        raise SystemExit(
            f'traces.jsonl has changed since the stability run ({recorded} → {current}). '
            'Every human label is keyed by position in that file, so retesting now would '
            'score each label against a different datapoint. Re-run stability.py → '
            'metrics.py → build_queue.py and redo the labelling, or restore the original '
            'traces.jsonl.'
        )


def _load_labels(out_dir: Path) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    """Load the human labels, grey-zone policy first (RES-980 default), then the
    UI-fallback annotations. Returns `(labels, source, policy)`:

    - `grey_zone_policy.json` present → its per-point policy labels (validated),
      source `'grey_zone_policy'`, and the raw policy (so the numeric tolerance
      band and the label provenance can be read from it).
    - else `annotations.json` (the interactive UI fallback) → source
      `'annotations'`, policy `None`.

    Both yield `{str(source_index): {'value': ..., 'tolerance'?: ...}}` so the
    pairing loop is identical.

    When BOTH exist the **newer file wins**. The documented fallback runs in this
    direction — try the grey-zone Q&A, find the examples don't group, open the
    annotation UI — so a fixed grey-zone-first preference discarded the artifact
    the user had just spent their time on, silently.
    """
    gz_path = out_dir / 'grey_zone_policy.json'
    ann_path = out_dir / 'annotations.json'
    if gz_path.exists() and ann_path.exists():
        newer = 'annotations' if ann_path.stat().st_mtime > gz_path.stat().st_mtime else 'grey_zone_policy'
        logger.warning(
            f'⚠ Both grey_zone_policy.json and annotations.json exist; using the newer '
            f'({newer}). Delete the stale one to make this unambiguous.'
        )
        if newer == 'annotations':
            return runner.read_json(ann_path), 'annotations', None
    if gz_path.exists():
        policy = runner.read_json(gz_path)
        grey_zone.validate_policy(policy)
        return grey_zone.policy_labels(policy), 'grey_zone_policy', policy
    if ann_path.exists():
        return runner.read_json(ann_path), 'annotations', None
    raise SystemExit(
        f'No human labels in {out_dir}: need grey_zone_policy.json (grey-zone flow) '
        'or annotations.json (UI fallback). Run the grey-zone stage or serve_annotation.py first.'
    )


def _resolve_numeric_tol(
    cli_tol: float | None,
    cfg: dict[str, Any],
    policy: dict[str, Any] | None,
    scale: Sequence[float] | None = None,
) -> float:
    """The run-wide numeric band, in priority order: explicit `--tol` → a *uniform*
    per-point band from the grey-zone policy → configured `numeric_tol` → a band
    derived from the evaluator's declared scale.

    Per-point *varying* bands are no longer dropped here: they are passed through to
    `agreement.numeric_agreement` as `tols`, and this value is what a point without
    its own band falls back to.

    The derived default is the important part. A fixed 0.5 is 50% of a 0–1 scale and
    0.5% of a 0–100 one, so the gate that exists to stop signal (a) being gamed went
    vacuous on exactly the 0–1 judges most groundedness rubrics use. §1 of this work
    normalizes instability by the declared range for the same reason; signal (b) now
    matches.
    """
    if cli_tol is not None:
        return float(cli_tol)
    if policy is not None:
        bands = {lbl.get('tolerance') for lbl in policy.get('labels', []) if lbl.get('tolerance') is not None}
        if len(bands) == 1:
            return float(next(iter(bands)))
    configured = cfg.get('numeric_tol')
    if configured not in (None, ''):
        return float(configured)
    from lib import agreement as agreement_lib  # noqa: PLC0415 — pure stdlib module

    return agreement_lib.default_tolerance(
        scale, fraction=float(cfg.get('numeric_tol_fraction', agreement_lib.DEFAULT_TOL_FRACTION))
    )


def _instability_by_index(
    metrics: dict[str, Any], index_map: dict[int, int] | None = None
) -> dict[int, float]:
    """`{original_source_index: instability}` for the MEASURABLE rows of a metrics.json.

    Unmeasurable rows (instability `None` — too few usable verdicts, or no scale to
    normalize by) are absent rather than zero, so the caller can see which rows a
    run could and could not measure. `index_map` translates a sub-run's positional
    index back to the original, as in `_verdicts_by_index`.
    """
    out: dict[int, float] = {}
    for row in metrics.get('per_row') or []:
        idx = row.get('source_index')
        if index_map is not None:
            idx = index_map.get(idx, idx)
        value = row.get('instability')
        if idx is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
            out[idx] = float(value)
    return out


def _mean_over(by_idx: dict[int, float], indices: set[int]) -> float | None:
    values = [by_idx[i] for i in indices if i in by_idx]
    return (sum(values) / len(values)) if values else None


def _verdicts_by_index(stability: dict[str, Any], index_map: dict[int, int] | None = None) -> dict[int, Any]:
    """`{original_source_index: aggregate_value}` from a stability.json.

    `index_map` translates a sub-run's positional `source_index` back to the
    original index the labels are keyed by; the original run needs no translation.
    """
    out: dict[int, Any] = {}
    for row in stability.get('rows', []):
        idx = row.get('source_index')
        if index_map is not None:
            idx = index_map.get(idx, idx)
        out[idx] = row.get('aggregate_value')
    return out


def _pair_with_labels(
    labels: dict[str, Any], judge_by_idx: dict[int, Any]
) -> tuple[list[tuple[Any, Any]], list[float | None]]:
    """Zip each human label to a judge verdict on the same datapoint.

    The human `value` is ground truth; the judge side is the jury's
    `aggregate_value` (the majority bool/str/float per type). Rows with no usable
    judge verdict or no human label are skipped — they cannot inform agreement.

    Returns `(pairs, tols)`, positionally aligned. `tols[i]` is that point's own
    numeric band when the human gave it one (`None` otherwise), so a policy that
    banded each point differently is scored against what the human actually said
    instead of against the run-wide default.
    """
    pairs: list[tuple[Any, Any]] = []
    tols: list[float | None] = []
    for key, ann in labels.items():
        if not isinstance(ann, dict) or ann.get('value') is None:
            continue
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        judge_value = judge_by_idx.get(idx)
        if judge_value is None:
            continue
        pairs.append((ann['value'], judge_value))
        band = ann.get('tolerance')
        tols.append(float(band) if isinstance(band, (int, float)) and not isinstance(band, bool) else None)
    return pairs, tols


def _evaluate_agreement(
    pairs: list[tuple[Any, Any]], output_type: str, tol: float,
    min_accuracy: float, min_tpr: float, min_tnr: float, min_within_tol: float,
    tols: list[float | None] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Compute signal (b) and its pass/fail against the type-native bar."""
    from lib import agreement as agreement_lib

    scores = agreement_lib.agreement(output_type, pairs, tol=tol, tols=tols)
    if output_type == 'boolean':
        # A None rate (no positives / no negatives among labels) does not fail the
        # gate on its own — accuracy still must clear the bar, and any present rate
        # must too.
        tpr, tnr = scores.get('tpr'), scores.get('tnr')
        passed = (
            scores['accuracy'] >= min_accuracy
            and (tpr is None or tpr >= min_tpr)
            and (tnr is None or tnr >= min_tnr)
        )
    elif output_type == 'categorical':
        passed = scores['accuracy'] >= min_accuracy
    else:  # numeric
        passed = scores['within_tolerance_rate'] >= min_within_tol
    return scores, passed


def _low_flip_indices(out_dir: Path) -> set[int]:
    """The stable spot-check rows the queue held back (`low_flip_sample`).

    They carry no labels, so they never enter agreement — they are the regression
    check: rows the OLD judge was completely steady on. A rewrite that fixes the
    grey zone by unsettling everything else shows up here and nowhere else.
    """
    queue_path = out_dir / 'queue.json'
    if not queue_path.exists():
        return set()
    return {
        item.get('source_index')
        for item in runner.read_json(queue_path).get('items', [])
        if item.get('low_flip_sample') and item.get('source_index') is not None
    }


def _regression_report(
    old_by_idx: dict[int, Any], new_by_idx: dict[int, Any], indices: set[int]
) -> dict[str, Any] | None:
    """How the new judge answered the rows the old one never wavered on."""
    compared = [i for i in sorted(indices) if i in old_by_idx and i in new_by_idx]
    if not compared:
        return None
    changed = [i for i in compared if old_by_idx[i] != new_by_idx[i]]
    return {
        'n_compared': len(compared),
        'n_changed': len(changed),
        'changed_source_indices': changed,
        'note': (
            'Rows the ORIGINAL judge was completely consistent on. A changed verdict here '
            'is not automatically wrong — nobody labelled these — but it is the rewrite '
            'moving behaviour outside the grey zone it was asked to settle.'
        ),
    }


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    num_samples: int | None = None,
    n_repeats: int | None = None,
    temperature: float | None = None,
    tol: float | None = None,
    all_rows: bool = False,
    with_low_flip: bool = False,
    baseline_rerun: bool = False,
) -> str:
    """Retest the new evaluator over the confusers and score both signals.

    Args:
        run_dir: Run directory (defaults to most recent) — must hold
            new_evaluator.json, evaluator.json, traces.jsonl, the human labels
            (grey_zone_policy.json or annotations.json), and metrics.json.
        config: TOML config path.
        num_samples: Cap rows re-judged (smoke testing). Applied to the scoped
            selection, so the before/after means stay over the same rows.
        all_rows: Re-judge every row in traces.jsonl, not just the labelled ones.
            Costs proportionally more and adds no agreement signal; use it only
            to re-measure run-wide instability with the new evaluator.
        with_low_flip: Also re-judge the queue's stable spot-check rows, as an
            unlabelled regression check on behaviour outside the grey zone.
        baseline_rerun: Re-run the ORIGINAL judge over the same rows in this pass.
            Doubles the cost and is the only way gate (a) isolates the rewrite from
            the regression-to-the-mean that selecting the most unstable rows causes.
        n_repeats: Repeats for the retest run. Defaults to the ORIGINAL run's
            count so the comparison is like-for-like; a lower value is warned about
            and marks the comparison not comparable.
        temperature: Judge temperature. Defaults to the original run's, for the
            same reason.
        tol: Numeric within-tolerance band, in the judge's own units. Defaults to a
            uniform band from the grey-zone policy, then `numeric_tol`, then a
            fraction of the evaluator's declared scale. Points the policy banded
            individually keep their own band regardless of this.
    """
    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run create_eval.py --approve first.')

    if not (out_dir / 'new_evaluator.json').exists():
        raise SystemExit(
            f'No new_evaluator.json in {out_dir}. Approve + create the aligned '
            'evaluator (create_eval.py --approve) before retesting.'
        )
    original_metrics = runner.read_json(out_dir / 'metrics.json')
    _check_fingerprint(out_dir, original_metrics)

    new_eval = runner.read_json(out_dir / 'new_evaluator.json')
    source_eval = runner.read_json(out_dir / 'evaluator.json')

    # Run parameters come from the ORIGINAL run unless overridden. Instability is
    # estimated from N repetitions at a given temperature, and both bias the
    # estimate — retesting at a different N or temperature compares two different
    # measurements and calls the difference an improvement.
    original_meta = original_metrics.get('metadata', {})
    original_n = original_meta.get('n_repeats')
    resolved_n = int(n_repeats or original_n or cfg.get('n_repeats', _DEFAULT_N_REPEATS))
    resolved_temp = temperature if temperature is not None else original_meta.get('temperature')
    comparable = original_n is None or resolved_n >= int(original_n)
    if not comparable:
        logger.warning(
            f'⚠ Retesting at {resolved_n} repeats against a stability run of {original_n}. '
            'Fewer repeats under-estimate instability, so a "drop" here can be the sample '
            'size alone. Gate (a) is marked not comparable.'
        )

    # 1) Materialize the retest sub-run against the NEW evaluator. Scope it to the
    #    rows that carry a human label: those are the only ones agreement can score,
    #    and re-judging the rest costs money for a number nothing reads.
    labels, label_source, policy = _load_labels(out_dir)
    wanted: set[int] | None = None
    if not all_rows:
        wanted = {
            int(k) for k, v in labels.items()
            if isinstance(v, dict) and v.get('value') is not None and str(k).lstrip('-').isdigit()
        }
        if with_low_flip:
            wanted |= _low_flip_indices(out_dir)
    selected = _select_rows(out_dir, wanted, num_samples)
    retest_dir, index_map = _materialize_subrun(
        out_dir, 'retest', _new_evaluator_config(new_eval, source_eval), selected
    )

    scope = set(index_map.values())
    original_inst = _instability_by_index(original_metrics)
    n_total_rows = len(runner.read_jsonl(out_dir / 'traces.jsonl'))
    logger.info(
        f'  Retesting {len(scope)} row(s)'
        + ('' if all_rows else f' of {n_total_rows} in the run')
        + f'; {resolved_n} repeats each, temperature={resolved_temp}.'
    )

    # 2) Re-run the EXISTING stability + metrics mains over it (imported, unmodified).
    #    Heavy import guarded inside the function to keep this module import-safe.
    from stability import main as stability_main  # noqa: PLC0415

    stability_main(
        run_dir=str(retest_dir),
        config=config,
        n_repeats=resolved_n,
        temperature=resolved_temp,
        metrics=True,  # stability.main chains metrics.main when metrics=True
    )
    retest_metrics = runner.read_json(retest_dir / 'metrics.json')
    retest_inst = _instability_by_index(retest_metrics, index_map)
    retest_stability = runner.read_json(retest_dir / 'stability.json')
    new_by_idx = _verdicts_by_index(retest_stability, index_map)

    # 2b) Optional true A/B: the OLD judge over the SAME rows, in the same pass.
    baseline_inst: dict[int, float] = {}
    if baseline_rerun:
        logger.info('  Re-running the ORIGINAL judge over the same rows (--baseline_rerun).')
        baseline_dir, baseline_map = _materialize_subrun(
            out_dir, 'retest_baseline', source_eval, selected
        )
        stability_main(
            run_dir=str(baseline_dir), config=config, n_repeats=resolved_n,
            temperature=resolved_temp, metrics=True,
        )
        baseline_inst = _instability_by_index(
            runner.read_json(baseline_dir / 'metrics.json'), baseline_map
        )

    # 3a) Signal (a): instability dropped. Against the re-measured baseline when we
    #     have one, else against the original run's measurement of the same rows.
    #
    # Both means are taken over the rows BOTH sides could measure. Averaging each
    # side over its own measurable set let a rewrite pass gate (a) by going
    # *unmeasurable* on the hardest rows: an off-contract verdict on more than half
    # the repetitions drops that row below metrics.py's floor, out of the retest
    # mean, and the remaining rows average lower. The rows selected here are the
    # most unstable ones in the run, so that is precisely where it would happen —
    # and the mean alone cannot show it. `n_lost_unmeasurable` reports the gap.
    before_inst = baseline_inst if baseline_rerun else original_inst
    comparable_rows = {i for i in scope if i in before_inst and i in retest_inst}
    n_lost_unmeasurable = len({i for i in scope if i in before_inst} - comparable_rows)
    scoped = bool(comparable_rows)
    if scoped:
        original_mean = _mean_over(original_inst, comparable_rows)
        baseline_mean = _mean_over(baseline_inst, comparable_rows) if baseline_rerun else None
        retest_mean = _mean_over(retest_inst, comparable_rows)
    else:
        # No per-row overlap (an older metrics.json with no `per_row`, or nothing
        # measurable on either side). Fall back to the run-wide numbers and say so
        # rather than present a delta over two different row sets as like-for-like.
        original_mean = original_metrics.get('scores', {}).get('mean_instability')
        baseline_mean = None
        retest_mean = retest_metrics.get('scores', {}).get('mean_instability')
        logger.warning(
            '⚠ No rows are measurable on both sides; falling back to the RUN-WIDE means. '
            'The before/after numbers below are over different row sets.'
        )
    if n_lost_unmeasurable:
        logger.warning(
            f'⚠ {n_lost_unmeasurable} row(s) the old judge could measure are UNMEASURABLE under '
            'the new one (too few on-contract verdicts). They are excluded from both means, so '
            'gate (a) says nothing about them — check total_wrong_output_type in the retest run.'
        )

    compare_against = baseline_mean if baseline_mean is not None else original_mean
    if compare_against is None or retest_mean is None:
        instability_dropped = False
        drop = None
    else:
        drop = compare_against - retest_mean
        instability_dropped = retest_mean < compare_against

    # 3b) Signal (b): the new evaluator agrees with the human labels — and, for
    #     free, what the OLD one scored on the same labels.
    retest_eval = runner.read_json(retest_dir / 'evaluator.json')
    output_type = (retest_eval.get('output_type') or 'boolean').strip().lower()
    if output_type == 'string':
        # String is detect + annotate only (no rewrite/retest); agreement() has no
        # string branch, so reaching here would raise a raw ValueError inside
        # asyncio.run. Fail with a clean, actionable message instead.
        raise SystemExit(
            'retest does not support string evaluators — the string type is '
            'detect + annotate only. Stop the alignment after annotation.'
        )
    resolved_tol = _resolve_numeric_tol(tol, cfg, policy, retest_eval.get('scale'))
    pairs, pair_tols = _pair_with_labels(labels, new_by_idx)
    if not pairs:
        raise SystemExit(
            'No (human, judge) pairs to score agreement — need labelled annotations '
            'with a usable retest verdict on the same confusers.'
        )
    bars = (
        float(cfg.get('retest_min_accuracy', _DEFAULT_MIN_ACCURACY)),
        float(cfg.get('retest_min_tpr', _DEFAULT_MIN_TPR)),
        float(cfg.get('retest_min_tnr', _DEFAULT_MIN_TNR)),
        float(cfg.get('retest_min_within_tol', _DEFAULT_MIN_WITHIN_TOL)),
    )
    agreement_scores, agreement_passed = _evaluate_agreement(
        pairs, output_type, resolved_tol, *bars, tols=pair_tols
    )

    # The "before" side of (b) costs nothing: the original run already judged these
    # very rows. Without it a passing score can still be worse than what the judge
    # did before the rewrite, and nothing in the report would show it.
    original_stability_path = out_dir / 'stability.json'
    old_by_idx: dict[int, Any] = {}
    agreement_before: dict[str, Any] | None = None
    if original_stability_path.exists():
        old_by_idx = _verdicts_by_index(runner.read_json(original_stability_path))
        before_pairs, before_tols = _pair_with_labels(labels, old_by_idx)
        if before_pairs:
            agreement_before, _ = _evaluate_agreement(
                before_pairs, output_type, resolved_tol, *bars, tols=before_tols
            )
            agreement_before['n_pairs'] = len(before_pairs)

    regression = _regression_report(old_by_idx, new_by_idx, _low_flip_indices(out_dir) & scope)

    success = bool(instability_dropped and agreement_passed and comparable)
    provenance = grey_zone.label_provenance(policy) if policy is not None else None
    payload = {
        'metadata': {
            'output_type': output_type,
            'source_evaluator_id': original_meta.get('evaluator_id'),
            'new_evaluator_id': new_eval['id'],
            'label_source': label_source,
            'label_provenance': provenance,
            'n_pairs': len(pairs),
            'n_rows_retested': len(index_map),
            'scoped_to_labelled': not all_rows,
            'n_repeats': resolved_n,
            'original_n_repeats': original_n,
            'temperature': resolved_temp,
            'timestamp': runner.utc_timestamp(),
        },
        'instability': {
            'original_mean': original_mean,
            'original_mean_scoped_to_retested_rows': scoped,
            'baseline_rerun_mean': baseline_mean,
            'compared_against': 'baseline_rerun' if baseline_mean is not None else 'original_run',
            'retest_mean': retest_mean,
            'drop': drop,
            'dropped': instability_dropped,
            'comparable': comparable,
            # Both means are over these rows and no others. `n_lost_unmeasurable`
            # is the rows the old judge measured and the new one could not — they
            # are outside the comparison, so the drop says nothing about them.
            'n_rows_compared': len(comparable_rows),
            'n_lost_unmeasurable': n_lost_unmeasurable,
            'retest_wrong_output_type': retest_metrics.get('scores', {}).get('total_wrong_output_type'),
        },
        'agreement': {
            **agreement_scores,
            'passed': agreement_passed,
            'before': agreement_before,
        },
        'regression_on_stable_rows': regression,
        'success': success,
        # Read these out with the numbers, not instead of them. Each names a way
        # the two gates overstate the result, and none is visible in the scores.
        'caveats': _caveats(baseline_mean is not None, provenance, regression, n_lost_unmeasurable),
    }
    runner.write_json(out_dir / 'retest_metrics.json', payload)

    logger.info('── Retest validation ──')
    logger.info(
        f'  (a) instability: {original_mean} → {retest_mean} (drop={drop}, vs '
        f'{payload["instability"]["compared_against"]}, over {len(comparable_rows)} row(s) '
        f'measurable on both sides) → '
        f'{"DROPPED" if instability_dropped else "NOT dropped"}'
        + ('' if comparable else '  [NOT COMPARABLE: fewer repeats than the original run]')
    )
    before_txt = f' (before: {agreement_before})' if agreement_before else ' (no before-score available)'
    logger.info(
        f'  (b) agreement ({output_type}, n={len(pairs)}): {agreement_scores}{before_txt} → '
        f'{"PASS" if agreement_passed else "FAIL"}'
    )
    if regression:
        logger.info(
            f'  regression check: {regression["n_changed"]} of {regression["n_compared"]} '
            'previously-stable row(s) changed verdict.'
        )
    for caveat in payload['caveats']:
        logger.warning(f'  ⚠ {caveat}')
    logger.info(f'✓ Wrote {out_dir / "retest_metrics.json"} — success={success}')
    print(out_dir)
    return str(out_dir)


def _caveats(
    has_baseline: bool, provenance: dict[str, int] | None, regression: dict[str, Any] | None,
    n_lost_unmeasurable: int = 0,
) -> list[str]:
    """The limits of the two gates, in the words the conductor should use.

    Stated on every run, including the good ones — especially then. A number that
    is only valid under conditions nobody mentions gets quoted without them.
    """
    out: list[str] = []
    if not has_baseline:
        out.append(
            'These rows were picked for the highest observed instability, so re-measuring '
            'them drifts toward the middle on its own. Some of the drop is that, not the '
            'rewrite. Pass --baseline_rerun to re-run the old judge over the same rows.'
        )
    out.append(
        'The same examples produced the guidance for the rewrite and the labels that score '
        'it. There is no holdout, so treat the agreement number as an upper bound.'
    )
    if provenance and provenance.get('derived'):
        out.append(
            f'{provenance["derived"]} of {sum(provenance.values())} labels were derived by '
            'applying the rule rather than confirmed by the user, so agreement partly '
            'measures whether the new judge matches that reading of the rule.'
        )
    if regression is None:
        out.append(
            'Behaviour outside the grey zone was not re-measured. Pass --with_low_flip to '
            'check the rows the old judge was completely steady on.'
        )
    if n_lost_unmeasurable:
        out.append(
            f'{n_lost_unmeasurable} row(s) the old judge could measure are unmeasurable under the '
            'new one, so they sit outside gate (a) entirely. A rewrite that answers off-contract '
            'on the hardest rows removes them from the mean, which reads as a drop.'
        )
    return out


if __name__ == '__main__':
    fire.Fire(main)
