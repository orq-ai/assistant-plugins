# Steps 2–5: price the run, measure consistency, pick the review set

## 2. Agree what we're about to run, and what it costs  ⟵ GATE
The next step asks the judge the same question several times over to see whether it
answers the same way. Explain it that way, then settle three things with the user:
**how many times to repeat each example** (default 8), **how many examples**, and the
**temperature**.

**Also confirm which model will judge.** We resolve the model name but *not* the
provider, and the router needs both. `evaluator.json["judge_model"]` often holds a
bare name (`gpt-5-mini`, `gpt-oss-120b`); to pin the provider, **edit
`<run_dir>/evaluator.json` and set `judge_model`** to `<provider>/<model>` — there is
no `--model` flag, that field is the only input. Examples:
`anthropic/claude-haiku-4-5`, `google/gemini-2.5-flash`, `groq/gpt-oss-120b`. In
`openai/gpt-oss-120b` the `openai/` **is** the provider, not a fixed prefix — never
stack one on another (`anthropic/openai/claude-haiku-4-5` is a 404). Show the user the
final slug and check it's the provider they meant.

Then show the size of the job and **wait for an explicit yes**:
```
uv run scripts/estimate_cost.py --run_dir <run_dir>
```
It reports how many judge calls that is and the token totals. There's no dollar
figure — multiply by the model's per-Mtoken rate if they want one.

## 3. Run it
```
uv run scripts/stability.py --run_dir <run_dir>
```
(Try `--num_samples 2` first as a smoke check.) Writes `stability.json` and runs the
metrics automatically.

## 4. Tell them how consistent the judge is
`metrics.py` wrote `metrics.json`. Report it **as behaviour, not as statistics**:
how often the judge gave the same answer when asked the same question repeatedly,
how many examples it was solid on versus wobbly on, and which specific examples it
changed its mind about most. Lead with a sentence anyone can act on — *"on 40
examples, the judge gave a different answer on 6 of them when asked eight times"* —
and keep the underlying scale (a 0–1 instability score per example) as backup detail
for anyone who asks.

Say plainly what this does **not** tell them: consistency is not correctness. A judge
that is wrong the same way every time scores perfectly here. That caveat belongs in
this message, not only in the final summary.

**Unless `metrics.json`'s `correctness` block has `n_labelled > 0`** — then it *does*
tell them, for the rows it covers, and burying that would waste the most valuable
number in the run. The block itself is present whenever the examples carried ground
truth, but a present block isn't the same as a populated one: when the evaluator
declares a reference-family variable (`reference` was judge input, not ground truth),
or it's numeric with no derivable scale, it comes back with
`n_labelled: 0` and a `reason_omitted` naming which — read that out, don't report an
accuracy number. When it *is* populated, lead with the accuracy and, specifically, the
accuracy on rows the judge was **stable** on — but check `by_band.stable`'s coverage
first: only once it covers at least 10 rows and at least 90% of that band does the
line earn *"the consistently-wrong blind spot, measured"*, and only then say *"and on
the 20 it was completely steady about, it was right on all 20 — so this isn't
consistent-but-wrong, it's consistent-and-right."* Below that floor `metrics.py`
captions the same line "partial view — do not conclude 'consistent-and-right' from
this", and say exactly that instead of the stronger claim. If the accuracy itself
reads *"steady on 20, right on 12"* — whatever the coverage — say that just as
plainly: the judge is reliably wrong, and no amount of rewriting for consistency will
help.

**Check how the labels split before quoting accuracy.** If one answer is rare — 3 fails
in 30, say — a judge that always gives the common answer scores 90% and catches
nothing. The correctness block carries `balanced_accuracy` and `cohen_kappa` next to
`accuracy`; when they disagree (high accuracy, kappa near 0), lead with the rare
answer: *"you marked 3 as fail; it caught none of them."* That is the number the user
needs, and accuracy hides it.

`wrong_vs_reference` rows enter the queue as their own class — stable, and disagreeing
with the label. Before any of them drives a rewrite, **ask the user to confirm the
label**: a dataset label is someone's prior judgement, possibly stale, possibly from a
different version of the rubric. Never auto-approve a rewrite on dataset labels alone.

(For yes/no judges the heavier agreement stats — 1-Flip Consistency, Gwet AC1,
Fleiss κ — are computed too. Offer them; don't lead with them.)

**If the judge was consistent on everything, go back to step 1a** — there is nothing
to review, and the second-model option is the one that fits.

## 5. Ask how many examples to go through together  ⟵ GATE
*After* they've seen the step-4 report, ask how many of the examples the judge
changed its mind on they want to look at with you:

> *"How many of the ones it kept changing its mind on should we go through together?
> Give me a number, or 'all' — if it's more than fits in one pass I'll tell you which
> ones made the cut."*

Never say "confuser queue", "top-ambiguous", or "grey zone" to the user. It's an
informed choice, not a fixed number. Mention in half a sentence that you'll also pull
a few examples it was *completely* consistent on, as a spot-check that it isn't
confidently wrong — those are held separately and don't count against their number.
```
uv run scripts/build_queue.py --run_dir <run_dir> --count <N>
```
**The number they give is the number they get.** The queue also holds
`low_flip_sample_size` (default 5) stable spot-check rows, and those are *excluded*
from step 6 — so "3" means three examples in the discussion, not eight.

`build_queue` also projects what step 6 will cost in context ("~95k tokens of 60k;
the top 48 will enter"). If it reports a drop, say so **now** — this is the moment
they chose coverage, so correct it here rather than re-asking later.

---

Next: step 6 — read [grey-zone.md](grey-zone.md).
