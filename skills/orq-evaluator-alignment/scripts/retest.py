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
"""Step 10 — retest / validation of the human-aligned evaluator (RES-978 §2.5).

Part 1 could only tell whether a judge was *stable*, not whether it was *right*:
a judge that is consistently wrong but never flips scores a perfect instability of
0. So the retest gates a rewrite on BOTH signals, over the same confusers:

  (a) instability DROPPED  — new evaluator's mean instability < the original run's
      (read from the original metrics.json), and
  (b) verdicts AGREE with the human labels — `lib.agreement` between the new
      evaluator's majority verdict and the human `value` per confuser
      (boolean: TPR/TNR; categorical: accuracy; numeric: MAE / within-tolerance).

Report success = (a) AND (b). (a) alone is gameable; (b) keeps it honest.

Orchestration: build a `retest/` sub-run whose `evaluator.json` is the NEW
evaluator and whose `traces.jsonl` is the original run's confusers, then re-run
the EXISTING `stability.py` + `metrics.py` mains over it (imported, never
modified). Writes `retest_metrics.json` in the parent run dir.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/retest.py --run_dir runs/<key>_<ts>
    uv run scripts/retest.py --run_dir runs/<key>_<ts> --num_samples 2   # smoke
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401
from lib import grey_zone, runner

load_dotenv()

# The two signals default to strict thresholds; overridable via config/CLI. `(a)`
# is a strict drop (new < old). `(b)`'s bar is type-native and lives in the config
# keys read below (accuracy for bool/categorical, within-tolerance for numeric).
_DEFAULT_MIN_ACCURACY = 0.7
_DEFAULT_MIN_TPR = 0.7
_DEFAULT_MIN_TNR = 0.7
_DEFAULT_MIN_WITHIN_TOL = 0.7
_DEFAULT_NUMERIC_TOL = 0.5


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


def _build_retest_dir(out_dir: Path) -> Path:
    """Materialize `retest/` under the parent run dir: new evaluator.json + the
    original confusers as traces.jsonl. Idempotent — overwrites on rerun."""
    new_eval = runner.read_json(out_dir / 'new_evaluator.json')
    source_eval = runner.read_json(out_dir / 'evaluator.json')
    confusers = runner.read_jsonl(out_dir / 'traces.jsonl')
    if not confusers:
        raise SystemExit(f'No confusers in {out_dir / "traces.jsonl"} — nothing to retest.')

    retest_dir = out_dir / 'retest'
    retest_dir.mkdir(parents=True, exist_ok=True)
    runner.write_json(retest_dir / 'evaluator.json', _new_evaluator_config(new_eval, source_eval))
    runner.write_jsonl(retest_dir / 'traces.jsonl', confusers)
    return retest_dir


def _load_labels(out_dir: Path) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    """Load the human labels, grey-zone policy first (RES-980 default), then the
    UI-fallback annotations. Returns `(labels, source, policy)`:

    - `grey_zone_policy.json` present → its per-point policy labels (validated),
      source `'grey_zone_policy'`, and the raw policy (so the numeric tolerance
      band can be read from it).
    - else `annotations.json` (the interactive UI fallback) → source
      `'annotations'`, policy `None`.

    Both yield `{str(source_index): {'value': ..., 'tolerance'?: ...}}` so the
    pairing loop is identical.
    """
    gz_path = out_dir / 'grey_zone_policy.json'
    if gz_path.exists():
        policy = runner.read_json(gz_path)
        grey_zone.validate_policy(policy)
        return grey_zone.policy_labels(policy), 'grey_zone_policy', policy
    ann_path = out_dir / 'annotations.json'
    if ann_path.exists():
        return runner.read_json(ann_path), 'annotations', None
    raise SystemExit(
        f'No human labels in {out_dir}: need grey_zone_policy.json (grey-zone flow) '
        'or annotations.json (UI fallback). Run the grey-zone stage or serve_annotation.py first.'
    )


def _resolve_numeric_tol(cli_tol: float | None, cfg: dict[str, Any], policy: dict[str, Any] | None) -> float:
    """Numeric within-tolerance band, in priority order: explicit `--tol` → a
    *uniform* per-point band from the grey-zone policy → config default.

    Per-point *varying* bands are a deliberate v1 deferral (§6, numeric-calibration
    is the flagged high-risk surface); when the policy pins one band for every
    point we honour it, otherwise we fall back to the single configured tolerance.
    """
    if cli_tol is not None:
        return float(cli_tol)
    if policy is not None:
        bands = {lbl.get('tolerance') for lbl in policy.get('labels', []) if lbl.get('tolerance') is not None}
        if len(bands) == 1:
            return float(next(iter(bands)))
    return float(cfg.get('numeric_tol', _DEFAULT_NUMERIC_TOL))


def _human_pairs(out_dir: Path, retest_dir: Path) -> tuple[list[tuple[Any, Any]], str, dict[str, Any] | None]:
    """Zip each human label to the NEW evaluator's majority verdict on that
    confuser, by `source_index`. Returns `(pairs, output_type, policy)`.

    The human `value` is ground truth; the judge side is the jury's `aggregate_value`
    from the retest stability run (the majority bool/str/float per type). Rows with
    no usable judge verdict (None) or no human label are skipped — they cannot
    inform agreement. `policy` is the grey-zone policy (or None on the annotations
    fallback) so the caller can resolve a policy-pinned numeric tolerance.
    """
    labels, _source, policy = _load_labels(out_dir)
    stability = runner.read_json(retest_dir / 'stability.json')
    ev = runner.read_json(retest_dir / 'evaluator.json')
    output_type = (ev.get('output_type') or 'boolean').strip().lower()

    judge_by_idx = {r['source_index']: r.get('aggregate_value') for r in stability.get('rows', [])}
    pairs: list[tuple[Any, Any]] = []
    for key, ann in labels.items():
        if not isinstance(ann, dict) or 'value' not in ann or ann.get('value') is None:
            continue
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        judge_value = judge_by_idx.get(idx)
        if judge_value is None:
            continue
        pairs.append((ann['value'], judge_value))
    return pairs, output_type, policy


def _evaluate_agreement(
    pairs: list[tuple[Any, Any]], output_type: str, tol: float,
    min_accuracy: float, min_tpr: float, min_tnr: float, min_within_tol: float,
) -> tuple[dict[str, Any], bool]:
    """Compute signal (b) and its pass/fail against the type-native bar."""
    from lib import agreement as agreement_lib

    scores = agreement_lib.agreement(output_type, pairs, tol=tol)
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


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    num_samples: int | None = None,
    n_repeats: int | None = None,
    tol: float | None = None,
) -> str:
    """Retest the new evaluator over the confusers and score both signals.

    Args:
        run_dir: Run directory (defaults to most recent) — must hold
            new_evaluator.json, evaluator.json, traces.jsonl, annotations.json,
            and metrics.json (the original instability).
        config: TOML config path.
        num_samples: Cap confusers re-judged (smoke testing).
        n_repeats: Override repeats for the retest stability run.
        tol: Numeric within-tolerance tolerance (raw scale). Default 0.5.
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
    original_mean = original_metrics.get('scores', {}).get('mean_instability')

    # 1) Materialize the retest sub-run against the NEW evaluator + the confusers.
    retest_dir = _build_retest_dir(out_dir)

    # 2) Re-run the EXISTING stability + metrics mains over it (imported, unmodified).
    #    Heavy import guarded inside the function to keep this module import-safe.
    from stability import main as stability_main  # noqa: PLC0415

    stability_main(
        run_dir=str(retest_dir),
        config=config,
        num_samples=num_samples,
        n_repeats=n_repeats,
        metrics=True,  # stability.main chains metrics.main when metrics=True
    )
    retest_metrics = runner.read_json(retest_dir / 'metrics.json')
    retest_mean = retest_metrics.get('scores', {}).get('mean_instability')

    # 3a) Signal (a): instability dropped vs the original run.
    if original_mean is None or retest_mean is None:
        instability_dropped = False
        drop = None
    else:
        drop = original_mean - retest_mean
        instability_dropped = retest_mean < original_mean

    # 3b) Signal (b): the new evaluator agrees with the human labels.
    pairs, output_type, policy = _human_pairs(out_dir, retest_dir)
    if output_type == 'string':
        # String is detect + annotate only (no rewrite/retest); agreement() has no
        # string branch, so reaching here would raise a raw ValueError inside
        # asyncio.run. Fail with a clean, actionable message instead.
        raise SystemExit(
            'retest does not support string evaluators — the string type is '
            'detect + annotate only. Stop the alignment after annotation.'
        )
    resolved_tol = _resolve_numeric_tol(tol, cfg, policy)
    if not pairs:
        raise SystemExit(
            'No (human, judge) pairs to score agreement — need labelled annotations '
            'with a usable retest verdict on the same confusers.'
        )
    agreement_scores, agreement_passed = _evaluate_agreement(
        pairs,
        output_type,
        resolved_tol,
        float(cfg.get('retest_min_accuracy', _DEFAULT_MIN_ACCURACY)),
        float(cfg.get('retest_min_tpr', _DEFAULT_MIN_TPR)),
        float(cfg.get('retest_min_tnr', _DEFAULT_MIN_TNR)),
        float(cfg.get('retest_min_within_tol', _DEFAULT_MIN_WITHIN_TOL)),
    )

    success = bool(instability_dropped and agreement_passed)
    payload = {
        'metadata': {
            'output_type': output_type,
            'source_evaluator_id': original_metrics.get('metadata', {}).get('evaluator_id'),
            'new_evaluator_id': runner.read_json(out_dir / 'new_evaluator.json')['id'],
            'n_pairs': len(pairs),
            'timestamp': runner.utc_timestamp(),
        },
        'instability': {
            'original_mean': original_mean,
            'retest_mean': retest_mean,
            'drop': drop,
            'dropped': instability_dropped,
        },
        'agreement': {
            **agreement_scores,
            'passed': agreement_passed,
        },
        'success': success,
    }
    runner.write_json(out_dir / 'retest_metrics.json', payload)

    logger.info('── Retest validation ──')
    logger.info(
        f'  (a) instability: {original_mean} → {retest_mean} '
        f'(drop={drop}) → {"DROPPED" if instability_dropped else "NOT dropped"}'
    )
    logger.info(f'  (b) agreement ({output_type}, n={len(pairs)}): {agreement_scores} → '
                f'{"PASS" if agreement_passed else "FAIL"}')
    logger.info(f'✓ Wrote {out_dir / "retest_metrics.json"} — success={success}')
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
