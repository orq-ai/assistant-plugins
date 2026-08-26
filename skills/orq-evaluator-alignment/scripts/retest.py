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
from lib.content import reference_is_judge_input, traces_fingerprint

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

    `num_samples == 0` refuses outright (§4.6) rather than silently materializing
    an empty retest sub-run: `0` reads as "no cap" one flag away (`-1` in
    stability.py's own convention) and would otherwise fail confusingly, much
    later, on the empty pairs.
    """
    if num_samples == 0:
        raise SystemExit('--num_samples must be >= 1 (0 would retest nothing)')
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
    """Load the human labels from the grey-zone policy, the UI-fallback
    annotations, or both. Returns `(labels, source, policy)`:

    - `grey_zone_policy.json` present → its per-point policy labels (validated),
      source `'grey_zone_policy'`, and the raw policy (so the numeric tolerance
      band and the label provenance can be read from it).
    - `annotations.json` (the interactive UI fallback) → source `'annotations'`,
      policy `None`.
    - BOTH present → the **union**, keyed by `source_index`, with the annotation
      winning any datapoint both cover. The policy is still returned, so the
      tolerance band and the derived-label provenance survive the merge.

    Both yield `{str(source_index): {'value': ..., 'tolerance'?: ...}}` so the
    pairing loop is identical.

    Merging rather than picking is what makes this deterministic. The documented
    fallback runs grey-zone Q&A first and the annotation UI second, so neither a
    fixed grey-zone-first preference (which discarded the artifact the user had
    just finished) nor a newest-file-wins rule (which made the result depend on
    filesystem timestamps, and threw away whichever half lost) is right: both
    files are the same human answering about different datapoints. Annotations win
    a collision because the UI is where the user goes to correct a policy label.
    """
    gz_path = out_dir / 'grey_zone_policy.json'
    ann_path = out_dir / 'annotations.json'
    if gz_path.exists() and ann_path.exists():
        policy = runner.read_json(gz_path)
        grey_zone.validate_policy(policy)
        labels = dict(grey_zone.policy_labels(policy))
        annotations = runner.read_json(ann_path)
        for key, value in annotations.items():
            entry = dict(value) if isinstance(value, dict) else {'value': value}
            # A widget answer is a human confirming a verdict, which is exactly what
            # the policy's own `human_confirmed` provenance bucket means.
            entry.setdefault('label_source', 'human_confirmed')
            labels[str(key)] = entry
        overlap = sorted(set(map(str, annotations)) & set(grey_zone.policy_labels(policy)))
        logger.info(
            f'Both grey_zone_policy.json and annotations.json exist; scoring against the '
            f'union ({len(labels)} labels)'
            + (f'. {len(overlap)} datapoint(s) in both — the annotation wins: {overlap}' if overlap else '.')
        )
        return labels, 'grey_zone_policy+annotations', policy
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


def _coerce_bool_tolerant(value: Any) -> bool | None:
    """Best-effort bool from a dataset reference; `None` (never a raise) on
    anything genuinely ambiguous. Mirrors `metrics.py::_coerce_bool` deliberately
    — NOT `lib.agreement._coerce_bool`, which raises: that raise fires AFTER the
    retest run has already spent the judge calls it exists to score (§1.1), so a
    single free-text reference among hundreds of parseable ones crashed the whole
    report instead of being counted and skipped.
    """
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


def _parse_reference_label(
    reference: Any, output_type: str, normalized_categorical_labels: set[str]
) -> Any:
    """Parse a raw dataset `reference` into THIS judge's verdict space, or `None`
    when it cannot be read there. `None` is the unreadable signal the caller
    counts and skips on — never coerced into a value that would silently score as
    a wrong (or, worse, a lucky right) answer.
    """
    if output_type == 'boolean':
        return _coerce_bool_tolerant(reference)
    if output_type == 'categorical':
        if not normalized_categorical_labels:
            return str(reference)
        return str(reference) if str(reference).strip().lower() in normalized_categorical_labels else None
    if output_type in ('number', 'numeric'):
        try:
            return float(reference)
        except (TypeError, ValueError):
            return None
    return str(reference)  # categorical: the label is compared as text


def _merge_dataset_labels(
    out_dir: Path,
    labels: dict[str, Any],
    output_type: str,
    categorical_labels: list[str],
    variables: list[str],
) -> tuple[dict[str, Any], int, int]:
    """Fill unanswered rows from the dataset's own `reference`, tagged as such.

    Only rows the human did **not** answer about are filled — a grey-zone answer
    always wins, because it is the user's verdict on this rubric and the dataset's
    label is not. Returns `(labels, n_added, n_unreadable)`; both counts are 0
    when the run carried no ground truth, which is the ordinary case for a
    trace-scanned run.

    Two things a plain "copy `reference` in" skipped (§1.1):
      - An evaluator that declares a reference-family variable (`{{log.reference}}`
        etc.) was shown the reference as judge INPUT, so merging it back in as
        ground truth would grade the judge on what it was just told — the same
        circularity `metrics.py::_correctness` already refuses. Checked first, via
        the shared `lib.content.reference_is_judge_input` rule, and short-circuits
        the whole merge rather than filtering it row by row.
      - A `reference` that cannot be read into this judge's verdict space (free
        text for a boolean judge, a categorical label the judge never declares) is
        not "close enough" to a value — merging it anyway would either crash later
        or silently score a comparison that was never meaningful.
    """
    if reference_is_judge_input(variables):
        logger.info(
            '  Dataset references are judge input for this evaluator (a declared '
            "variable maps to `reference`); not merged as ground truth."
        )
        return labels, 0, 0
    stability_path = out_dir / 'stability.json'
    if not stability_path.exists():
        return labels, 0, 0
    normalized_cat = {str(lbl).strip().lower() for lbl in categorical_labels or []}
    merged = dict(labels)
    added = unreadable = 0
    for row in runner.read_json(stability_path).get('rows', []):
        idx, reference = row.get('source_index'), row.get('reference')
        if idx is None or reference in (None, ''):
            continue
        key = str(idx)
        if key in merged and isinstance(merged[key], dict) and merged[key].get('value') is not None:
            continue  # the human answered this one; their verdict stands
        value = _parse_reference_label(reference, output_type, normalized_cat)
        if value is None:
            unreadable += 1
            continue
        merged[key] = {'value': value, 'label_source': 'dataset_reference'}
        added += 1
    if unreadable:
        logger.warning(
            f'⚠ {unreadable} dataset reference(s) could not be read into this '
            "judge's verdict space; skipped rather than merged as ground truth."
        )
    return merged, added, unreadable


def _labelled_indices(labels: dict[str, Any]) -> set[int]:
    """Row indices carrying a usable value — human-answered or merged, whichever
    `labels` currently holds. Shared by the default retest scope and the
    unlabelled-regression view so both use the same definition of "labelled".
    """
    return {
        int(k) for k, v in labels.items()
        if isinstance(v, dict) and v.get('value') is not None and str(k).lstrip('-').isdigit()
    }


def _build_wanted(
    labels_before_merge: dict[str, Any],
    labels: dict[str, Any],
    all_rows: bool,
    with_dataset_labels: bool,
    with_low_flip: bool,
    low_flip: set[int],
) -> set[int] | None:
    """Rows to materialize into the retest sub-run (§3.6).

    Built from the labels the human actually answered, BEFORE the dataset merge —
    a merge should not silently multiply how many rows a retest re-judges (and
    pays for). Rows the merge added (`label_source == 'dataset_reference'`) are
    re-judged only when `with_dataset_labels` asks for them explicitly; otherwise
    the caller is told how many were left out and why, since gate (b) will only
    ever be scored against a subset of the merged labels without them.
    """
    if all_rows:
        return None
    wanted = _labelled_indices(labels_before_merge)
    dataset_indices = {
        int(k) for k, v in labels.items()
        if isinstance(v, dict) and v.get('label_source') == 'dataset_reference'
    }
    if with_dataset_labels:
        wanted |= dataset_indices
    elif dataset_indices:
        logger.info(
            f'  {len(dataset_indices)} dataset-labelled row(s) not re-judged; pass '
            '--with_dataset_labels to score gate (b) against them (costs '
            f'{len(dataset_indices)} x repeats).'
        )
    if with_low_flip:
        wanted |= low_flip
    return wanted


# `_resolve_numeric_tol`'s second return value, in priority order. 'fallback' is the
# one that matters: it means NONE of the real sources fired, so the number it
# returns (`lib.agreement.FALLBACK_TOL`) is arbitrary rather than derived — half the
# range on a 0–1 scale, 0.5% on a 0–100 one — which is exactly the case metrics.py's
# `_correctness` already refuses (`reason_omitted`). `main` refuses on it too, before
# any judging, instead of letting `tol_source: 'uniform'` in the payload describe a
# band nobody actually chose (MINOR 2).
_TOL_SOURCES = frozenset({'cli', 'policy_uniform', 'policy_median', 'configured', 'scale_derived', 'fallback'})


def _resolve_numeric_tol(
    cli_tol: float | None,
    cfg: dict[str, Any],
    policy: dict[str, Any] | None,
    scale: Sequence[float] | None = None,
) -> tuple[float, str]:
    """The run-wide numeric band, in priority order: explicit `--tol` → a *uniform*
    per-point band from the grey-zone policy → configured `numeric_tol` → a band
    derived from the evaluator's declared scale.

    Returns `(tol, source)`. `source` is one of `_TOL_SOURCES`, so the caller can
    tell a genuinely resolved band from the `'fallback'` case — an absolute
    `FALLBACK_TOL` with nothing behind it — and refuse on that case rather than
    silently score against it (§1's numeric gate (b) exists precisely so gate (a)
    can't be gamed; going vacuous defeats the point).

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
        return float(cli_tol), 'cli'
    if policy is not None:
        bands = {lbl.get('tolerance') for lbl in policy.get('labels', []) if lbl.get('tolerance') is not None}
        if len(bands) == 1:
            return float(next(iter(bands))), 'policy_uniform'
        if bands:
            median = float(sorted(bands)[len(bands) // 2])
            return median, 'policy_median'
    configured = cfg.get('numeric_tol')
    if configured not in (None, ''):
        return float(configured), 'configured'
    from lib import agreement as agreement_lib  # noqa: PLC0415 — pure stdlib module

    fraction = float(cfg.get('numeric_tol_fraction', agreement_lib.DEFAULT_TOL_FRACTION))
    if isinstance(scale, (list, tuple)) and len(scale) == 2:
        try:
            span = float(scale[1]) - float(scale[0])
        except (TypeError, ValueError):
            span = None
        if span and span > 0:
            return fraction * span, 'scale_derived'
    return agreement_lib.default_tolerance(scale, fraction=fraction), 'fallback'


def _comparable_measurement(
    original_n: int | None, resolved_n: int, original_temp: float | None, resolved_temp: float | None,
) -> bool:
    """Whether gate (a)'s retest measurement is like-for-like with the original run.

    N and temperature both bias the instability ESTIMATE, not just the judge's
    verdicts: fewer repeats under-count how often a judge flips, and a different
    temperature changes how often it flips in the first place (§4.5). Either one
    drifting makes "the new judge is steadier" indistinguishable from "the
    measurement moved" — a `None` original value means nothing was declared to
    compare against, so it cannot fail the check.
    """
    n_ok = original_n is None or resolved_n >= int(original_n)
    temp_ok = original_temp is None or resolved_temp == original_temp
    return n_ok and temp_ok


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
) -> tuple[list[tuple[Any, Any]], list[float | None], list[int]]:
    """Zip each human label to a judge verdict on the same datapoint.

    The human `value` is ground truth; the judge side is the jury's
    `aggregate_value` (the majority bool/str/float per type). Rows with no usable
    judge verdict or no human label are skipped — they cannot inform agreement.

    Returns `(pairs, tols, indices)`, all positionally aligned. `tols[i]` is that
    point's own numeric band when the human gave it one (`None` otherwise), so a
    policy that banded each point differently is scored against what the human
    actually said instead of against the run-wide default. `indices[i]` is the
    `source_index` the pair came from, so a scored subset can be traced back to
    the rows it covered.
    """
    pairs: list[tuple[Any, Any]] = []
    tols: list[float | None] = []
    indices: list[int] = []
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
        indices.append(idx)
    return pairs, tols, indices


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


def _primary_metric(output_type: str) -> str:
    """The single number `_evaluate_agreement` gates each type on — the same key
    `_regressed_vs_before` compares before vs. after."""
    return 'accuracy' if output_type in ('boolean', 'categorical') else 'within_tolerance_rate'


def _agreement_on_subset(
    all_pairs: list[tuple[Any, Any]], all_tols: list[float | None],
    all_indices: list[int], keep: set[int], output_type: str,
    tol: float, bars: tuple[float, ...],
) -> dict[str, Any] | None:
    """Filter a paired population down to `keep` indices and score agreement."""
    aligned = [
        (p, t, i) for p, t, i in zip(all_pairs, all_tols, all_indices)
        if i in keep
    ]
    if not aligned:
        return None
    sub_pairs = [a[0] for a in aligned]
    sub_tols = [a[1] for a in aligned]
    if not sub_pairs:
        return None
    scores, _ = _evaluate_agreement(sub_pairs, output_type, tol, *bars, tols=sub_tols)
    scores['n_pairs'] = len(sub_pairs)
    return scores


def _regressed_vs_before(
    output_type: str, after: dict[str, Any], before: dict[str, Any] | None
) -> bool | None:
    """Whether signal (b) got WORSE than the judge it is replacing (§2.1).

    `before` is optional (no original stability.json, or no rows both sides could
    pair) — a genuine "cannot tell" must read as `None`, never as `False`, because
    the gate below treats `None` as "does not block success" and `False` the same
    way a real non-regression would: only an ACTUAL regression may block it.
    """
    if not before:
        return None
    key = _primary_metric(output_type)
    after_value, before_value = after.get(key), before.get(key)
    if after_value is None or before_value is None:
        return None
    return after_value < before_value


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


_LOW_FLIP_NOTE = (
    'Rows the ORIGINAL judge was completely consistent on. A changed verdict here '
    'is not automatically wrong — nobody labelled these — but it is the rewrite '
    'moving behaviour outside the grey zone it was asked to settle.'
)

_UNLABELLED_NOTE = (
    'Every re-judged row carrying NO human label — the regression surface. The rows '
    'that WERE labelled are supposed to change; these are the ones that were never '
    'discussed, so a change here is the rewrite reaching further than it was asked '
    'to. Only as wide as what was re-judged: without --all_rows this is just the '
    'stable spot-check sample, not the original dataset.'
)


def _regression_report(
    old_by_idx: dict[int, Any], new_by_idx: dict[int, Any], indices: set[int],
    note: str = _LOW_FLIP_NOTE,
) -> dict[str, Any] | None:
    """How the new judge answered a set of rows the human never labelled.

    Returns None when no row in `indices` was answered by both judges — there is
    nothing to compare, which is reported as absent rather than as zero changes.
    """
    compared = [
        i for i in sorted(indices)
        if i in old_by_idx and i in new_by_idx
        and not (old_by_idx[i] is None and new_by_idx[i] is None)
    ]
    if not compared:
        return None
    changed = [i for i in compared if old_by_idx[i] != new_by_idx[i]]
    return {
        'n_compared': len(compared),
        'n_changed': len(changed),
        'changed_rate': len(changed) / len(compared),
        'changed_source_indices': changed,
        'note': note,
    }


def _metadata_label_source(label_source: str, n_from_dataset: int) -> str:
    """`metadata.label_source`, naming the merge when it actually happened —
    `label_source` alone (§4.6) claimed the report was scored purely against
    `grey_zone_policy`/`annotations` even on a run where some of those pairs were
    really `dataset_reference` labels merged in underneath them."""
    return f'{label_source}+dataset_reference' if n_from_dataset else label_source


def _build_provenance(
    policy: dict[str, Any] | None, pair_indices: list[int], labels: dict[str, Any],
) -> dict[str, int]:
    """How many of the SCORED pairs trace to each label source (§4.6 / MINOR 8).

    EVERY count here — `derived`, `human_confirmed`, `dataset_reference` on the
    policy path; `annotations`, `dataset_reference` on the UI-fallback path — is
    rescoped to `pair_indices`: the rows agreement was ACTUALLY computed over, not
    every label the policy carries. `grey_zone.label_provenance(policy)` counts
    policy-wide, so a label whose row was merged (or derived) but never re-judged —
    no `--with_dataset_labels`, or simply never selected — inflated a source's count
    with rows that were never actually part of `pairs`: "15 of 20 labels were
    derived" on a run that scored 1 pair. The caveat text's denominator (`n_pairs`)
    and this function's now agree by construction, because both are `pair_indices`.

    A pair's source is read from `labels` first — a row merged in from the dataset
    or from `annotations.json` carries its own `label_source` and is not present in
    the policy's `labels` list at all, so skipping that check would silently
    mis-source it as `'derived'` — and only then from the policy's own per-label
    `label_source`.
    """
    if policy is not None:
        # Mirrors grey_zone.label_provenance's own default: an unlabelled
        # `label_source` on a policy point is the conductor's derivation, not a
        # human confirmation.
        policy_source_by_idx = {
            lbl.get('source_index'): lbl.get('label_source', 'derived')
            for lbl in policy.get('labels', [])
        }
        provenance = {source: 0 for source in sorted(grey_zone.LABEL_SOURCES)}
        for idx in pair_indices:
            ann = labels.get(str(idx))
            source = ann.get('label_source') if isinstance(ann, dict) else None
            if source not in grey_zone.LABEL_SOURCES:
                source = policy_source_by_idx.get(idx, 'derived')
            provenance[source] = provenance.get(source, 0) + 1
        return provenance
    n_dataset = sum(
        1 for idx in pair_indices
        if isinstance(labels.get(str(idx)), dict) and labels[str(idx)].get('label_source') == 'dataset_reference'
    )
    return {'annotations': len(pair_indices) - n_dataset, 'dataset_reference': n_dataset}


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    num_samples: int | None = None,
    n_repeats: int | None = None,
    temperature: float | None = None,
    tol: float | None = None,
    all_rows: bool = False,
    with_low_flip: bool = False,
    with_dataset_labels: bool = False,
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
        with_dataset_labels: Also re-judge rows whose only label came from the
            dataset's own `reference` (merged in as `label_source=dataset_reference`,
            §3d) rather than a human answer. Off by default: those rows can
            multiply the retest cost far past what the human actually labelled,
            for a signal that is someone else's prior judgement, not the user's
            verdict on this rubric.
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
            individually keep their own band regardless of this. On a numeric judge
            with none of those AND no declared scale, refuses before any judging
            rather than silently falling back to an arbitrary absolute band (§1.2).
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
    # The retest sub-run's evaluator.json — built ONCE, here, so output_type has a
    # single resolution used by the merge below, the agreement scoring, and the
    # materialized retest/evaluator.json. Reading it back out of the sub-run later
    # and re-deriving output_type from that copy is the same chain computed twice.
    retest_evaluator_config = _new_evaluator_config(new_eval, source_eval)
    output_type = (retest_evaluator_config['output_type'] or 'boolean').strip().lower()
    categorical_labels = retest_evaluator_config['categorical_labels']
    variables = retest_evaluator_config['variables']

    # Run parameters come from the ORIGINAL run unless overridden. Instability is
    # estimated from N repetitions at a given temperature, and both bias the
    # estimate — retesting at a different N or temperature compares two different
    # measurements and calls the difference an improvement.
    original_meta = original_metrics.get('metadata', {})
    original_n = original_meta.get('n_repeats')
    original_temp = original_meta.get('temperature')
    resolved_n = int(n_repeats or original_n or cfg.get('n_repeats', _DEFAULT_N_REPEATS))
    resolved_temp = temperature if temperature is not None else original_temp
    if original_n is not None and resolved_n < int(original_n):
        logger.warning(
            f'⚠ Retesting at {resolved_n} repeats against a stability run of {original_n}. '
            'Fewer repeats under-estimate instability, so a "drop" here can be the sample '
            'size alone. Gate (a) is marked not comparable.'
        )
    if original_temp is not None and resolved_temp != original_temp:
        logger.warning(
            f'⚠ Retesting at temperature {resolved_temp} against a stability run at '
            f'{original_temp}. A different temperature changes how often the judge flips, '
            'not just what it says, so gate (a) is marked not comparable.'
        )
    comparable = _comparable_measurement(original_n, resolved_n, original_temp, resolved_temp)

    # 1) Materialize the retest sub-run against the NEW evaluator. Scope it to the
    #    rows that carry a human label: those are the only ones agreement can score,
    #    and re-judging the rest costs money for a number nothing reads.
    labels, label_source, policy = _load_labels(out_dir)
    labels_before_merge = labels

    # IMPORTANT 2 / §1.2: refuse a numeric judge with no way to size gate (b)'s
    # tolerance BEFORE any judging happens (money spent) — no --tol, no uniform
    # grey-zone policy band, no configured numeric_tol, and no declared scale on
    # the evaluator all bottom out at `lib.agreement.FALLBACK_TOL` (0.5), which
    # `metrics.py::_correctness` already refuses the same situation on
    # (`reason_omitted`) rather than compute against. Resolved once, here, and
    # reused below — the source name also keeps `tol_source` in the payload
    # truthful (it must never read `'uniform'`/etc. for a band nobody chose).
    resolved_tol, resolved_tol_source = _resolve_numeric_tol(
        tol, cfg, policy, retest_evaluator_config.get('scale')
    )
    if output_type in ('number', 'numeric') and resolved_tol_source == 'fallback':
        raise SystemExit(
            f'Numeric judge with no way to size the agreement tolerance for gate (b): no '
            f'--tol, no uniform grey-zone policy band, no configured numeric_tol, and no '
            f'declared scale on the evaluator. Scoring against the arbitrary fallback band '
            f'({_DEFAULT_NUMERIC_TOL}) would make the gate that exists to stop signal (a) '
            'being gamed vacuous — refusing before any judging happens rather than after. '
            'Pass --tol, set numeric_tol in config.toml, declare the evaluator\'s scale '
            '(fetch_evaluator.py --scale_min/--scale_max), or band the grey-zone points '
            'individually, then re-run this command.'
        )

    # Dataset ground truth fills rows the human never answered about (§3d), parsed
    # into THIS judge's verdict space first so an unreadable reference is skipped
    # and counted rather than crashing later (§1.1). Kept strictly separate in the
    # provenance count: a dataset label is someone's prior judgement, not the
    # user's verdict on this rubric, and merging the two would let a run report
    # human-confirmed agreement nobody confirmed. Merged rows are NOT re-judged by
    # default (§3.6) — pass --with_dataset_labels to include them.
    labels, n_from_dataset, _n_unreadable_dataset = _merge_dataset_labels(
        out_dir, labels, output_type, categorical_labels, variables
    )
    if n_from_dataset:
        logger.info(
            f'  + {n_from_dataset} label(s) from the dataset\'s own ground truth '
            '(label_source=dataset_reference; counted separately from human answers).'
        )
    wanted = _build_wanted(
        labels_before_merge, labels, all_rows, with_dataset_labels, with_low_flip,
        _low_flip_indices(out_dir),
    )
    selected = _select_rows(out_dir, wanted, num_samples)
    retest_dir, index_map = _materialize_subrun(out_dir, 'retest', retest_evaluator_config, selected)

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
        baseline_dir, baseline_map = _materialize_subrun(
            out_dir, 'retest_baseline', source_eval, selected
        )
        logger.info('  Re-running the ORIGINAL judge over the same rows (--baseline_rerun).')
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
    #     free, what the OLD one scored on the same labels. output_type AND
    #     resolved_tol were already resolved once, above (before the merge, and
    #     before any judging) — not re-derived here.
    pairs, pair_tols, pair_indices = _pair_with_labels(labels, new_by_idx)
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

    # The "before" side of (b) costs nothing: the original run already judged these
    # very rows. Without it a passing score can still be worse than what the judge
    # did before the rewrite, and nothing in the report would show it. Read first,
    # so the report can show both sides of the comparison.
    original_stability_path = out_dir / 'stability.json'
    old_by_idx: dict[int, Any] = {}
    if original_stability_path.exists():
        old_by_idx = _verdicts_by_index(runner.read_json(original_stability_path))
    # §4.4: the before-side must cover the SAME rows as the after-side, not every
    # labelled row in the original run — otherwise num_samples (or a partial
    # --with_low_flip retest) pairs a 1-row "after" against a 200-row "before" and
    # reports a comparison that never happened on either side.
    old_scoped = {i: v for i, v in old_by_idx.items() if i in scope}

    agreement_scores, agreement_passed = _evaluate_agreement(
        pairs, output_type, resolved_tol, *bars, tols=pair_tols
    )

    agreement_before: dict[str, Any] | None = None
    agreement_after_shared: dict[str, Any] | None = None
    before_after_scope_info: tuple[int, int, int] | None = None
    if old_scoped:
        before_pairs_raw, before_tols_raw, before_indices_raw = _pair_with_labels(labels, old_scoped)
        # The intersection of rows both sides can score — the only population
        # where a before/after comparison is like-for-like. A new judge that
        # answers rows the old one couldn't (the common case for a rewrite that
        # makes previously unanswerable rows answerable) must not inflate the
        # after-side of the regression check with those extra rows, and vice
        # versa for rows the old judge answered but the new one can't.
        shared = set(pair_indices) & set(before_indices_raw)
        agreement_before = _agreement_on_subset(
            before_pairs_raw, before_tols_raw, before_indices_raw, shared,
            output_type, resolved_tol, bars,
        )
        # When the after side covers rows the old judge couldn't answer,
        # agreement_scores (all after-rows) is over a different population than
        # agreement_before (shared rows only). Compute the after-side score on
        # the shared subset so _regressed_vs_before compares like-for-like.
        if shared and shared != set(pair_indices):
            agreement_after_shared = _agreement_on_subset(
                pairs, pair_tols, pair_indices, shared,
                output_type, resolved_tol, bars,
            )
            if agreement_after_shared is not None:
                before_after_scope_info = (len(before_indices_raw), len(pair_indices), len(shared))

    # Two regression views over rows nobody labelled. The narrow one is the stable
    # spot-check sample (~5 rows) the queue held back; the wide one is every
    # re-judged row without a label, which is the whole original dataset under
    # --all_rows. The narrow view was the only one that existed, so a rewrite could
    # move behaviour across the other 200 rows and the report would say "0 of 5
    # previously-stable rows changed" — true, and not what the reader took from it.
    labelled_indices = _labelled_indices(labels)
    regression = _regression_report(old_by_idx, new_by_idx, _low_flip_indices(out_dir) & scope)
    regression_wide = _regression_report(
        old_by_idx, new_by_idx, scope - labelled_indices, _UNLABELLED_NOTE
    )

    # §2.1: a passing accuracy bar can still be WORSE than the judge it replaces —
    # `before` is computed above for free, and until now nothing read it. A `None`
    # regression (no before-score) must not block success; only a measured one may.
    # Use the shared-row after score when the after side covers rows the old judge
    # couldn't — otherwise the headline over all after-rows is already like-for-like.
    after_for_regression = agreement_after_shared if agreement_after_shared is not None else agreement_scores
    regressed_vs_before = _regressed_vs_before(output_type, after_for_regression, agreement_before)
    if regressed_vs_before:
        before_value = agreement_before.get(_primary_metric(output_type))
        after_value = after_for_regression.get(_primary_metric(output_type))
        logger.warning(
            f'⚠ agreement regressed vs the original judge ({before_value} → {after_value}); '
            'success forced False'
        )
    success = bool(instability_dropped and agreement_passed and comparable and not regressed_vs_before)
    # §4.6: the provenance denominator is the SCORED pairs, never the merged
    # count — a dataset label merged but excluded from this retest (no
    # --with_dataset_labels) never entered `pairs`, so counting it here would
    # claim agreement was measured against a row it was never computed over.
    provenance = _build_provenance(policy, pair_indices, labels)
    # `len(scope)` is what was ACTUALLY re-judged; `all_rows` is only the request
    # for that. A run that happens to label every row covers the original dataset
    # regardless of whether --all_rows was passed to ask for it.
    covers_original_dataset = len(scope) >= n_total_rows
    payload = {
        'metadata': {
            'output_type': output_type,
            'source_evaluator_id': original_meta.get('evaluator_id'),
            'new_evaluator_id': new_eval['id'],
            'label_source': _metadata_label_source(label_source, n_from_dataset),
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
            # §2.2 / MINOR 7: without a baseline rerun, an UNCHANGED judge reads as
            # "dropped" almost every time (the rows were selected for extreme
            # observed instability, so re-measuring them regresses toward the mean
            # on its own) — this says outright whether THIS run isolated the
            # rewrite from that. Keyed on the OUTCOME (`baseline_mean is not None`),
            # not the flag: a `--baseline_rerun` that found no rows measurable on
            # both sides falls back to the original-run comparison a few lines up
            # (`compare_against = baseline_mean if baseline_mean is not None else
            # original_mean`) while still claiming the flag was passed, which is
            # not what this field is supposed to report.
            'selection_bias_controlled': baseline_mean is not None,
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
            'after_on_shared_rows': agreement_after_shared,
            'regressed_vs_before': regressed_vs_before,
        },
        'regression_on_stable_rows': regression,
        # How wide the regression check actually looked, so a clean result cannot be
        # read as covering more ground than it did.
        'regression_on_unlabelled_rows': regression_wide,
        'regression_scope': {
            'n_rows_rejudged': len(scope),
            'n_rows_in_original_run': n_total_rows,
            'covers_original_dataset': covers_original_dataset,
        },
        'success': success,
        # Read these out with the numbers, not instead of them. Each names a way
        # the two gates overstate the result, and none is visible in the scores.
        'caveats': _caveats(
            baseline_mean is not None, provenance, regression, n_lost_unmeasurable,
            covers_original=covers_original_dataset, n_rejudged=len(scope), n_original=n_total_rows,
            regression_before_after=(
                (agreement_before.get(_primary_metric(output_type)), after_for_regression.get(_primary_metric(output_type)))
                if regressed_vs_before else None
            ),
            regression_wide=regression_wide, n_pairs=len(pairs),
            before_after_scope=before_after_scope_info,
        ),
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
    if agreement_after_shared is not None and agreement_before:
        shared_key = _primary_metric(output_type)
        before_txt = (
            f' (before: {agreement_before.get(shared_key)} over {agreement_before["n_pairs"]} shared row(s), '
            f'after on same: {agreement_after_shared.get(shared_key)})'
        )
    elif agreement_before:
        before_txt = f' (before: {agreement_before})'
    else:
        before_txt = ' (no before-score available)'
    logger.info(
        f'  (b) agreement ({output_type}, n={len(pairs)}): {agreement_scores}{before_txt} → '
        f'{"PASS" if agreement_passed else "FAIL"}'
    )
    if regression:
        logger.info(
            f'  regression check: {regression["n_changed"]} of {regression["n_compared"]} '
            'previously-stable row(s) changed verdict.'
        )
    if regression_wide:
        logger.info(
            f'  regression check (all unlabelled): {regression_wide["n_changed"]} of '
            f'{regression_wide["n_compared"]} row(s) nobody labelled changed verdict '
            f'— re-judged {len(scope)} of {n_total_rows} original datapoint(s)'
            + ('.' if all_rows else '; pass --all_rows to cover the rest.')
        )
    for caveat in payload['caveats']:
        logger.warning(f'  ⚠ {caveat}')
    logger.info(f'✓ Wrote {out_dir / "retest_metrics.json"} — success={success}')
    print(out_dir)
    return str(out_dir)


def _caveats(
    has_baseline: bool, provenance: dict[str, int] | None, regression: dict[str, Any] | None,
    n_lost_unmeasurable: int = 0,
    covers_original: bool = True, n_rejudged: int = 0, n_original: int = 0,
    regression_before_after: tuple[Any, Any] | None = None,
    regression_wide: dict[str, Any] | None = None, n_pairs: int = 0,
    before_after_scope: tuple[int, int, int] | None = None,
) -> list[str]:
    """The limits of the two gates, in the words the conductor should use.

    Stated on every run, including the good ones — especially then. A number that
    is only valid under conditions nobody mentions gets quoted without them.
    """
    out: list[str] = []
    if regression_before_after is not None:
        before_v, after_v = regression_before_after
        out.append(
            f'Agreement regressed vs the original judge ({before_v} → {after_v}); success is '
            'forced False even though gate (a) and/or the accuracy bar individually passed.'
        )
    if before_after_scope is not None:
        n_before, n_after, n_shared = before_after_scope
        out.append(
            f'The before/after regression check compared {n_shared} shared row(s) '
            f'(old judge scored {n_before}, new judge scored {n_after}). Rows only one '
            'side could answer are outside that comparison.'
        )
    if not has_baseline:
        out.append(
            'These rows were picked for the highest observed instability, so re-measuring '
            'them drifts toward the middle on its own. Some of the drop is that, not the '
            'rewrite. Pass --baseline_rerun to re-run the old judge over the same rows. On '
            'this default path an unchanged judge reads as "dropped" almost every time (the '
            'rows were selected for extreme observed instability), so treat the dropped '
            'verdict as unreliable without --baseline_rerun.'
        )
    out.append(
        'The same examples produced the guidance for the rewrite and the labels that score '
        'it. There is no holdout, so treat the agreement number as an upper bound.'
    )
    if provenance and provenance.get('derived'):
        out.append(
            f'{provenance["derived"]} of {n_pairs} labels were derived by '
            'applying the rule rather than confirmed by the user, so agreement partly '
            'measures whether the new judge matches that reading of the rule.'
        )
    if provenance and provenance.get('dataset_reference'):
        out.append(
            f'{provenance["dataset_reference"]} of {n_pairs} labels came from '
            "the dataset's own ground truth, not from this conversation. That is someone's "
            'prior judgement — possibly stale, possibly written against a different version of '
            'the rubric — so agreement on those rows is not the user agreeing.'
        )
    if regression is None:
        out.append(
            'Behaviour outside the grey zone was not re-measured. Pass --with_low_flip to '
            'check the rows the old judge was completely steady on.'
        )
    if regression_wide is None:
        out.append(
            'The unlabelled-row regression check had no rows to compare — every re-judged row '
            'carried a label, so nothing here says whether behaviour moved outside the grey zone.'
        )
    if not covers_original:
        out.append(
            f'The regression check covered {n_rejudged} of the {n_original} datapoint(s) in the '
            'original run. The rest were never re-judged, so nothing here says whether the new '
            'evaluator changed its mind about them. Pass --all_rows to re-judge the original set '
            '(costs rows × repeats) if this evaluator is already scoring production traffic.'
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
