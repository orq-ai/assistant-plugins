# Run directory contract

Every artifact lives in one run directory, born `runs/<key>_<ts>/` at step 1 and
renamed to `runs/<key>_<ts>_<model>_<N>dp/` only once a trace scan (step 1a option 1)
has resolved the judge model and datapoint count — a run built entirely from a
dataset, bring-your-own, or generated examples never gets that suffix, so always pass
the **printed** path rather than assuming the renamed form. It holds: `evaluator.json`
(with `output_type` + `categorical_labels`/`scale`), `traces.jsonl`, `scan.json` (what
the trace scan covered, and whether it hit its cap), `hollow_debug.json` (a sample of
the raw span shape, written only when the hollow ratio trips the abort — the
extraction shape-gap diagnostic), `stability.json` (rows carry `reference` when the
source supplied ground truth, plus every model's repetitions and errors in a panel
run), `metrics.json` (+ a `correctness` block when labels were present and a `panel`
block when several models judged), `queue.json` (each confuser carries its
`verdict_space` + a `reason` of
instability/panel_disagreement/cross_model/wrong_vs_reference/low_flip),
`dataset_inventory.json` + `input_mapping.json` (what a dataset held and how its
fields were mapped, written only when rows were skipped or `--map` was used),
`synthetic_datapoints.json`
(conductor-authored seed, §11) + `cross_model.json` (second-model disagreers) when
a starved run was seeded, `grey_zone_payload.json` (the conductor's bounded confuser payload),
`grey_zone_policy.json` (grey zones + questions + answers + per-point policy
labels with their `label_source` — the default feedback artifact),
`annotations.json` (typed values — the UI fallback), `recommendations.json`,
`aggregated.md` + `aggregated.json`, `new_prompt.md`, `rewrite_status.json`,
`approval.json` (records `forced_checks` — which create-side guards `--force`
overrode, empty when none did), `new_evaluator.json`, `retest_metrics.json`,
and the retest's own
sub-runs `retest/` (+ `retest_baseline/` with `--baseline_rerun`), each holding the
filtered `traces.jsonl` and the `index_map.json` that pairs its positional
`source_index` back to the parent's. Any step is re-runnable in isolation against an
existing run directory.

In a panel run, `metrics.json.per_row` separates model disagreement from within-model
wobble, and `retest_metrics.json.agreement` reports each model against the human
labels. The evaluator being changed remains the single audited judge.

**`source_index` is a position in `traces.jsonl`, and that file is the run's spine.**
`stability.json` and `queue.json` record a `traces_fingerprint` of it; `fetch_traces`
**rewrites** the file (`dataset_inputs` and `seed_inputs` only append), so widening
the trace window after labelling renumbers every row underneath the labels. The
grey-zone assemble warns on a mismatch and the retest refuses outright. If you need
more data after labelling, re-run stability → metrics → build_queue and redo the
labels rather than pairing them against a file that moved.
