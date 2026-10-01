# Step 8: check whether the new judge actually worked

## 8. Optional — check whether it actually worked  ⟵ GATE
Runs the same examples past the **new** judge and writes `retest_metrics.json`. It
uses the answers from `grey_zone_policy.json`, falling back to `annotations.json`.

**Ask before including dataset-only labels.**  ⟵ GATE
If any labelled row's *only* label is `label_source: dataset_reference` — the
dataset's own `expected_output`, merged in because the human never answered about
that row — it sits out of the retest by default: someone else's prior judgement
isn't the user's verdict on this rubric, and including it silently would let
"agreement" quietly cover rows nobody actually confirmed. Mirror the `--all_rows`
ask:

> *"3 of the labelled rows are only labelled from the dataset, not something you
> confirmed directly — include those in the retest too, or leave them out?"*

```
uv run scripts/retest.py --run_dir <run_dir> --with_dataset_labels
```

**Two things both have to be true** for this to count as an improvement: the new
judge has to stop changing its mind, **and** its answers have to match what the user
said they should be. Steadiness alone is worthless — a judge that's wrong the same
way every time scores perfectly on it. If it got steadier but disagrees with the
user, report exactly that; don't call it a win.

**Lead with the rare answer, not the overall rate.** When one label is scarce — a
handful of fails among mostly passes is the usual shape — accuracy is dominated by
the common one: 27 passes and 3 fails, judged "pass" every time, is 90% accurate and
catches none of the failures. The gates already refuse that (TNR/TPR fail), but the
sentence you say must too. Quote how many of the rare label it caught (*"it now
catches 2 of the 3 you marked fail"*) before any overall figure, and read
`agreement.balanced_accuracy` and `agreement.cohen_kappa` alongside accuracy: a kappa
near 0 means the judge is doing no better than always giving the common answer,
whatever the accuracy says.

**Agreement comes with a `before`.** The original run already judged these same
rows, so `retest_metrics.json` carries what the *old* judge scored against the same
labels, for free. Quote both — and note `agreement.regressed_vs_before` already does
the arithmetic: a new score of 0.78 that clears the 0.7 bar still forces
`success: false` if the old judge was at 0.85, so the pass/fail flag on its own is no
longer blind to a regression the way it used to be.

**For a panel run, inspect every model's agreement.** The retest reuses the original
panel models and repetition count. `agreement.panel_before` and
`agreement.panel_after` score each model against the human labels, with failed votes
omitted and `source_indices` showing each model's scoring set. Compare models only
on the same rows. Lead with balanced accuracy and rare-label recall when the labels
are skewed; overall accuracy can let two weak models outvote one useful one. Numeric
panel scores use each label's own tolerance band when the policy supplied one.

**Say what the numbers can't be.** `retest_metrics.json` carries a `caveats` list;
read it out rather than summarising it away. They all point the same way — the result
is softer than it looks:
- these rows were chosen for being the *most* unstable, so re-measuring them drifts
  toward the middle on its own. `--baseline_rerun` re-runs the **old** judge over the
  same rows in the same pass, which is the only version of gate (a) that isolates the
  rewrite — `instability.selection_bias_controlled` in `retest_metrics.json` says
  outright whether *this* run did that, rather than leaving it implied by whether the
  flag was passed. It costs the same again — offer it, don't assume it;
- the same examples produced the rewrite guidance *and* the labels that score it.
  There's no holdout, so the agreement number is an upper bound;
- any label the user didn't confirm at step 6 is your reading of their rule, not
  their verdict. `metadata.label_provenance` counts them;
- a row the new judge can no longer *measure* (off-contract on more than half its
  repetitions) is outside gate (a) entirely. Both means are taken over the rows both
  judges could measure — `instability.n_rows_compared` — and
  `instability.n_lost_unmeasurable` counts the rest. A non-zero count is the one that
  matters: dropping the hardest rows out of the average reads exactly like a drop.

**Check what happened outside the grey zone — and ask how wide to check.**  ⟵ GATE
The examples you worked on are *supposed* to change. The question a new evaluator
raises is what happened to everything else, and by default the answer covers only the
labelled rows plus, with `--with_low_flip`, the ~5 stable spot-check rows the queue
held back. That is a narrow check reported in confident words, so say what it covers
and offer the wide one:

> *"That's the 12 examples we worked on plus 5 steady ones — all fine. Want me to
> re-run the new judge over all 200 original datapoints and show you what moved? It's
> 200 × 8 calls, so it costs about the same as the first run. Worth it if this
> evaluator is already scoring live traffic."*

```
uv run scripts/retest.py --run_dir <run_dir> --with_low_flip              # ~5 rows
uv run scripts/retest.py --run_dir <run_dir> --all_rows --with_low_flip   # the lot
```

`retest_metrics.json` carries both views — `regression_on_stable_rows` (the spot-check
sample) and `regression_on_unlabelled_rows` (every re-judged row nobody labelled) —
plus `regression_scope`, which records how many of the original datapoints were
actually re-judged. **Quote that scope with the number.** "0 of 5 previously-stable
rows changed" is true and reads as "nothing regressed", which it does not mean; a
caveat says so whenever the check was narrower than the original run.

A changed verdict here isn't automatically wrong — nobody labelled these rows — but a
rewrite that settles the grey zone by unsettling everything else is the classic
failure of this whole process, and this is the only place it surfaces.

**It re-judges only the examples you settled answers for** — those are the only ones
agreement can score, so re-running the rest would cost money for verdicts nothing
reads. The before/after instability comparison is recomputed over that same subset,
so the drop isn't an artifact of which rows were picked. Quote the cost accordingly:
**labelled examples × repeats × models** for a panel run, not the whole dataset.
(`--all_rows` re-judges
everything, if they want a run-wide re-measure.)

Repeats and temperature **default to whatever the step-3 run actually used**, read
back from `metrics.json`, so the comparison is like-for-like without anyone having to
remember. Overriding to a *lower* repeat count, or to a *different* temperature,
marks the comparison not comparable — fewer samples under-estimate instability, and a
different temperature changes how often the judge flips in the first place, so either
reads as a win it didn't earn — and gate (a) fails rather than claiming one.
```
uv run scripts/retest.py --run_dir <run_dir>
uv run scripts/retest.py --run_dir <run_dir> --baseline_rerun --with_low_flip
```
(`--tol` is how close a score has to be to count as agreeing; it resolves, in order, a
uniform grey-zone policy band → `numeric_tol` → `numeric_tol_fraction` × the declared
scale. On a numeric judge with none of those AND no declared scale, the retest now
**refuses before any judging** rather than silently falling back to an absolute 0.5 —
that band is arbitrary (half of a 0–1 scale, 0.5% of a 0–100 one) and gate (b) exists
precisely so gate (a) can't be gamed; pass `--tol`, set `numeric_tol`, or declare the
scale (`fetch_evaluator.py --scale_min/--scale_max`) and re-run — see [configuration.md](configuration.md).
`--num_samples` caps the rows, for a smoke test — it narrows both sides of the
comparison, so the before/after stays over the same rows.)

**Quote the cost before running it**, there's no estimator for this step: it's
`labelled rows × repeats × models` judge calls for the new evaluator, plus
`labelled rows × repeats` for `--baseline_rerun` (the old judge only). Include
`low_flip_sample_size × repeats × models` with `--with_low_flip`.

## What to do with the result  ⟵ GATE

Two outcomes need a different next move than "rewrite again".

**Steadier, still wrong on the same rows: test the model, not the prompt.** If gate
(a) passed but gate (b) failed, and the rows the judge still gets wrong are ones it is
*steady* on — same wrong answer every repetition — the rule is already in the prompt
and the model isn't applying it. If a panel was run, check `agreement.panel_after`
first: a model that catches the rare failures on the **same labelled rows** is the
candidate to use for the aligned evaluator. Another rewrite rarely fixes that, and each round
spends the user's labels on a question they already answered. If no panel model
shows the difference, confirm the model and cost, then re-run the same retest with a
stronger judge model on a **copy** of the run so the real result stays put:

```
cp -R <run_dir> <run_dir>_modelcheck
# edit <run_dir>_modelcheck/new_evaluator.json: set judge_model to the stronger <provider>/<model>
uv run scripts/retest.py --run_dir <run_dir>_modelcheck
```

Confirm the model and the cost (same as the retest) first. Then:
- **the stronger model gets them right** → the fix is the judge's model, not its
  prompt. Say so, and point them at changing the model on the new evaluator in orq.
- **it gets them wrong too** → stop iterating. Keep the new judge in shadow (scoring
  alongside, not gating anything), tell the user which rows no model reproduced
  their answer on, and say plainly that this is the limit of what an LLM judge does
  with this rubric today. More repetitions or another rewrite will not supply it.

**More unstable after the rewrite, with no verdict flipped: that can be progress.**
Encoding one of their answers sometimes exposes the next question underneath it — a
rule that is clear in principle but has a fuzzy threshold ("claims must be correct"
raises "does *unproven* count as *incorrect*?"). Instability goes up, gate (a) fails,
and yet nothing the user settled may have got worse. Check two fields in
`retest_metrics.json`: `agreement.regressed_vs_before` (did agreement with their
labels drop?) and `regression_on_stable_rows` / `regression_on_unlabelled_rows` (did
verdicts move where nobody asked?). Both clean → don't revert: say the rule held and
surfaced a sharper question, and offer to go back to step 6 with the newly unstable
examples. Either one dirty → that's a real regression; report it as one.

---

Next: the final summary in [SKILL.md](../SKILL.md#final-summary).
