---
name: orq-evaluator-alignment
description: >-
  Align, calibrate, or improve an existing LLM-as-a-judge (orq evaluator) so its
  verdicts match human judgment — boolean, categorical, numeric, or free-form
  string judges (string: detect + annotate only, no rewrite/create yet). Use when
  the user wants to "align my evaluator", "improve my eval", "my judge keeps
  changing its mind", "find ambiguous cases", or "annotate an evaluator" — i.e.
  they have an LLM judge that disagrees with human labels or is inconsistent.
  Measures judge self-consistency as one 0..1 instability score via repeated runs,
  groups the least reliable examples by what makes them hard and asks a few
  questions instead of making the user label every row, rewrites the judge prompt
  from those answers, and creates the new evaluator only after the human approves.
  If the evaluator ID isn't given, ask for it after triggering. Do NOT use to
  build an evaluator from scratch (use orq-build-evaluator) or to fix failures
  with prompt tweaks (use orq-optimize-prompt).
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(uv run:*), AskUserQuestion
---

# Evaluator Alignment

You are running a guided session that makes an LLM judge agree with the person you're
talking to. You do the mechanics; **they make every decision that costs money or
changes something in their workspace** — rewriting the prompt, creating the new judge,
re-running the test. Never skip a gate.

**How to talk to the user.** They know their domain; they may know nothing about
judges, entropy, or flip rates, and they don't need to. Describe what is happening in
terms of what the judge *did* — "it gave a different answer 6 times out of 8 on this
one" — not in terms of the metric that measured it. Keep the internal vocabulary
("confuser", "grey zone", "instability band", "open-code", "conductor") out of what
you say to them; it's fine in the artifacts. At each step, one or two sentences on
what you're about to do and why, then do it. Offer options as short lists they can
answer in a few words.

Mechanically: you run small independent scripts under `scripts/`, each writing one
file into a per-run working directory (`runs/<key>_<ts>_<model>_<N>dp/`).

Each script under `scripts/` is self-contained: it declares its own dependencies
via PEP 723 inline metadata, so `uv run scripts/<name>.py ...` builds an isolated,
cached environment on first run — no `uv sync`, no project venv, no repo needed.
Always invoke as `uv run scripts/<name>.py` (not `uv run python scripts/...`, which
bypasses the inline metadata).

> **TLS-intercepting antivirus / corporate proxy:** the first run of each script
> reaches PyPI to build its env. Behind SSL-inspecting AV (e.g. Norton) or a
> corporate proxy that re-signs HTTPS, `uv` fails with `invalid peer certificate:
> UnknownIssuer`. Add `--system-certs` so `uv` trusts the OS certificate store:
> `uv run --system-certs scripts/<name>.py ...`. Only the first (uncached) build
> per script needs it.

## Constraints

- **Multi-type.** Measurement (stability → instability metrics → confuser ranking)
  supports **boolean, categorical, numeric, and free-form string** judges; the
  improve half (score → recommend → rewrite → create → retest) supports **boolean,
  categorical, and numeric** (RES-978 Part 1 + Part 2 / RES-980). Instability is one
  0..1 scale (boolean flip-rate, categorical label entropy, numeric score spread,
  string exact-match entropy `H/ln(N)`); the rewrite preserves the evaluator's
  verdict space (label set / numeric scale). Numeric rewriting is deliberately
  shallow — it nudges the scale's anchor descriptions, not a calibration model.
  **String supports detect + annotate only for now**: measurement, the confuser
  queue, and the free-text annotation widget all work, but `recommend`/`rewrite`/
  `create_eval` do **not** consume string yet (`create_eval` fails fast on it) — so
  string annotations are captured but don't auto-drive a rewrite. Step 1 accepts all
  four output types and fails fast on anything else.
- **Consistency is a ceiling, not proof.** A steady judge is *reproducible*, not
  *right* — it can be wrong the same way every time. You find the examples it's
  unsure about; only the user can say what the answer should be.
- **The blind spot, which you must say out loud:** this method finds examples the
  judge wavers on, so it structurally cannot find the ones it gets wrong with total
  confidence. State it in the final summary every time, and offer the stable
  spot-check sample (config `low_flip_sample_size`) as the cheap partial check.

## The flow

### 0. Do they have a judge in orq?  ⟵ GATE
**Ask this first, before anything else.** This skill improves a judge that already
exists in orq, so start by finding out whether there is one:

> *"Do you already have this judge set up in orq? If so, paste its ID — open the
> evaluator in orq, click **View code**, and copy the `id="01..."`."*

Three answers, three routes:

- **Yes, here's the ID** → go to step 1.
- **No, but I have the prompt** → offer to set it up for them: *"Paste the judge
  prompt and tell me what a pass looks like, and I'll create it in orq so we can
  measure it."* Create it with the **`orq-build-evaluator`** skill, confirm the new
  ID with them, then come back to step 1. Don't start measuring a prompt that has no
  evaluator behind it — every later step reads the evaluator record.
- **No, and no prompt yet** → this is the wrong skill. Say so plainly and point them
  at **`orq-build-evaluator`**, which starts from what they want to catch. Stop here.

### 1. Check the judge, and look for data
Once you have the ID, run **one** command:
```
uv run scripts/fetch_evaluator.py --evaluator_id <id>
```
It fetches the judge **and** scans the 200 most recent traces for examples it has
already scored. Tell the user, in their words, what came back:
- **the judge is the right one** — say what it looks at (its template variables) and
  what it decides, in one sentence, and ask them to confirm;
- **how many examples we have to work with** (`traces.jsonl`);
- **which model is doing the judging** (`judge_model` on `evaluator.json`). Resolved
  in priority order: an explicit `--judge_model` override → the evaluator's config
  model id looked up via `GET /v2/models` (registry UUID → slug) → the model seen on
  the production judge spans (plus `judge_models_observed`). If more than one model
  shows up, mention it — a judge whose model changed over time looks more erratic
  than it is.

  **If the model comes back unresolved** (the run dir is named `…_model-unknown_…`):
  the config id wasn't in `/v2/models` *and* the spans don't record
  `gen_ai.request.model` — common, because evaluator spans store the judge's input
  and output but not always which model produced them. Ask the user which model the
  judge uses (it's in the evaluator's model dropdown in orq) and rerun:
  ```
  uv run scripts/fetch_evaluator.py --evaluator_id <id> --judge_model mistral-large-latest
  ```
  Without it, we can't re-run the judge at step 3, so this has to be settled here.

The command prints a run directory — **pass that `--run_dir` to every later step.**
It starts as `<key>_<ts>` and is renamed to `<key>_<ts>_<model>_<N>dp` once the scan
knows the model and example count, so always use the **printed** path. If the judge's
output type isn't boolean, categorical, number, or string, it stops before scanning —
tell the user those four are what's supported, and that free-text judges can be
measured and annotated but not yet rewritten.

**If the scan found nothing (or too little), go to step 1a.** A scan covering 0–9
usable examples is the normal case for a judge that is new, rarely triggered, or
older than the scan window — it is not a failure, and it has a menu of fixes.

### 1a. Not enough examples — ask how they want to get data  ⟵ GATE
Reach this from **step 1** when the scan came back with fewer than 10 usable
examples, and again from **step 4** if the judge turns out to be perfectly consistent
on everything (nothing to review). Don't push on and don't pick for them. Say where
things stand in one line — *"the scan found 4 examples this judge has scored, which
isn't enough to tell signal from noise"* — then offer the choice:

> *"Four ways to get more: I can look further back through your traces, use a dataset
> you already have in orq, take examples you bring me, or make some up. Which fits?"*

1. **Look further back** (cheapest — try this first). The scan only covers the most
   recent 200 traces, and matching happens on our side, so an older or rarely-used
   judge simply sits outside the window.
   ```
   uv run scripts/fetch_traces.py --run_dir <run_dir> --trace_limit 2000
   ```
   Widen `trace_start_date`/`trace_end_date` in `config.toml` too if the judge is
   older than that. The evaluator is already saved, so this only re-pulls traces.
   **Only safe before anything is labelled** — it rewrites `traces.jsonl` wholesale,
   and every label is keyed by position in that file. After step 6, re-run
   stability → metrics → build_queue and redo the labels.
2. **Use a dataset already in orq.**
   ```
   uv run scripts/dataset_inputs.py list --config config.toml        # pick one
   uv run scripts/dataset_inputs.py pull --run_dir <run_dir> --dataset_id <id>
   ```
   It matches the dataset's columns to what the judge reads. If a field can't be
   matched it tells you which one — ask the user to map that field, don't guess.
3. **Take examples they bring.** For data that isn't in orq yet — a spreadsheet, a
   log export, examples pasted into chat. Write them to
   `<run_dir>/synthetic_datapoints.json` as a list of
   `{inputs, messages?, expected_output?, rationale?}`, then:
   ```
   uv run scripts/seed_inputs.py convert --run_dir <run_dir>   # → traces.jsonl
   ```
   Then ask whether to keep them in orq for next time:
   ```
   uv run scripts/seed_inputs.py save --run_dir <run_dir> --dataset_name "<name>"
   ```
4. **Make some up.** Use the **`orq-generate-synthetic-dataset`** skill to write
   borderline cases — **based on the real examples if there are any**, on the judge's
   own rubric if there are none. Same `convert` / `save` commands as option 3.

**One more, for when there is plenty of data but the judge never wavers** (step 4
shows a flat profile): ask a **second model** to judge the same examples, and treat
the ones the two models disagree on as the interesting cases. Needs a completed
`stability.py` run first, and confirm the second model with the user.
```
uv run scripts/cross_model.py --run_dir <run_dir> --model <provider/model>
```

After whichever they pick, **re-run stability → metrics** and read the report again.
If it's still starved, offer the remaining options rather than proceeding.

**Say this in the final summary whenever options 2–4 contributed:** examples that
didn't come from production test what the rubric *says*, not what the judge actually
meets in the wild. The alignment is only as representative as the data behind it.

### 2. Agree what we're about to run, and what it costs  ⟵ GATE
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

### 3. Run it
```
uv run scripts/stability.py --run_dir <run_dir>
```
(Try `--num_samples 2` first as a smoke check.) Writes `stability.json` and runs the
metrics automatically.

### 4. Tell them how consistent the judge is
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

(For yes/no judges the heavier agreement stats — 1-Flip Consistency, Gwet AC1,
Fleiss κ — are computed too. Offer them; don't lead with them.)

**If the judge was consistent on everything, go back to step 1a** — there is nothing
to review, and the second-model option is the one that fits.

### 5. Ask how many examples to go through together  ⟵ GATE
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

### 6. Work out *why* those examples are hard, then ask about it
Rather than labelling each example one at a time, group them by what makes them hard
and ask a few questions that each settle a whole group at once.

1. **Assemble** the payload and read it into context:
   ```
   uv run scripts/grey_zone.py assemble --run_dir <run_dir>
   ```
   `grey_zone_payload.json` gives each example's answer split, how wobbly it was, one
   of the judge's own explanations, and the (shortened) input it judged. *(Only one
   explanation per example is available — evaluatorq collapses the repetitions to
   one; a known v1 limit.)*

   **Don't `Read` `queue.json` or `stability.json` wholesale** — they carry every
   full input and every repetition, and reading them blows straight past the context
   budget. `grey_zone_payload.json` is the view you work from.

   **The one exception:** if answering a question genuinely needs a field the payload
   doesn't carry — a truncated passage, an exact figure, a row flagged
   `input_source: "fallback"` — look up **that one datapoint** by its `source_index`
   and read only its record. Say you're doing it and why. A single row is cheap; the
   whole file is not. Guessing because the rule said no is the worst of the three.

   **Then say what you actually got to see, before you analyse it.** The `budget`
   block tells you how many examples came through (`n_confusers`), how many the token
   budget dropped (`n_dropped_by_budget`), how many stable spot-check rows were held
   back (`n_low_flip_excluded`), and how many inputs were shortened (`n_truncated`,
   `total_chars_elided`; per example, `input_chars_shown` of `input_chars_original`).
   Say it in one plain line — *"all 3 came through in full, nothing shortened"*, or
   *"48 of 80; 12 had long inputs cut down, the worst showing 600 of 41,200
   characters"*.

   Two more that the elision numbers **cannot** tell you, so check them explicitly:
   - **`n_fallback_input` > 0** — those rows' inputs were reassembled from what the
     trace captured, because the judge template couldn't be inverted. They may be
     missing a field the judge had, and no character count will reveal it. Say so,
     and use the single-datapoint lookup below before drawing a conclusion from one.
   - **`n_no_rationale` > 0** — the judge gave no usable explanation for those,
     which happens most on an exact tie. That's the perverse case: the most evenly
     split example, where seeing both sides would help most, arrives with nothing to
     read. Don't quote the tie-break notice as if it were reasoning; say it's absent
     and reason from the input.
   - **`n_dropped_cross_model` > 0** — the queue lists second-model disagreers
     *after* the instability ranking, so the token budget drops those first. On a
     judge that rarely wavers they were the whole reason there was anything to look
     at. Raise `--max_tokens` and re-run rather than open-code without them.

   Each confuser also carries `reason`: `instability` (the judge disagreed with
   itself) or `cross_model` (it held steady and a second model disagreed). They are
   different kinds of hard — don't describe one as the other.

   This matters because a conclusion drawn from 1% of a transcript is confidently
   wrong in a way nothing downstream catches. Flag any group whose examples were
   heavily cut, and lean on the judge's own explanation for those. If they want to
   see more, raise `--max_chars` (more of each) or `--max_tokens` (more of them) and
   re-run — it's pure recomputation from `queue.json`, so it costs nothing.
2. **Group them by what makes them hard.** Not by topic — by the thing the current
   rubric doesn't settle (e.g. "sarcasm handled inconsistently", "abuse that's being
   quoted, not said", "no clear line for how severe counts as severe"). Step 4 found
   *which* examples it wobbled on; here you work out *why*.
3. **Ask one question per group — aim for 1–5 total.**  ⟵ GATE
   Ask about the rule, not the example, so one answer settles the whole group:
   - yes/no judge → which side of the line ("Should sarcasm aimed at a group count as abuse?")
   - category judge → where two labels divide ("When is something `spam` rather than `promotional`?")
   - score judge → the threshold ("Above what score would you call this severe?")

   Ask in chat, one at a time, and show an example or two so the question is
   concrete. What you want back is a short rule, not a verdict on each example.
4. **Write `grey_zone_policy.json`** from their answers. Per group record
   `{id, question, answer, rule, member_source_indices}`; then apply each rule to its
   examples to derive `{source_index, value, [tolerance], grey_zone_id, label_source}`
   — `true/false` for yes/no; one **already-declared** label for categories; for
   scores a target **plus a `tolerance` band** (asking for an exact number back is
   unrealistic). Carry the evaluator's `verdict_space` into the file; `apply` checks
   every value against it and refuses a label the judge cannot emit. **Never invent a
   new label or move the scale** — the rewritten judge has to answer in the same
   terms as the old one, or nothing is comparable.

   **Then read the derived labels back.**  ⟵ GATE
   You applied their rule; they didn't label these points. Those labels are what
   step 8 grades the new judge against, so show them the result in one short pass —
   *"applying that, I'd mark these three as pass and this one as fail; anything you'd
   flip?"* — and set `label_source: "human_confirmed"` on the ones they confirm,
   `"derived"` on the rest (the default). It's one message, and without it the whole
   validation is one model checking its own reading of the rule.
5. **Turn the answers into guidance** for the rewrite:
   ```
   uv run scripts/grey_zone.py apply --run_dir <run_dir>
   ```
   Checks the policy and writes `aggregated.md`. Go to step 7.
6. **Look at the steady ones too, briefly.** You promised this at step 5 and it is
   the only check on the blind spot the whole method has. One extra pass, held
   separately from the grey zones:
   ```
   uv run scripts/grey_zone.py assemble --run_dir <run_dir> --include_low_flip
   ```
   Read the `low_flip_sample: true` rows and ask the user whether the judge got them
   right — no grouping, no rules, just *"it was completely sure about these; do you
   agree?"* If any is wrong, say so plainly: the judge is confidently wrong
   somewhere, and nothing in the instability ranking will ever find that. **Re-run
   `assemble` without the flag afterwards** so `grey_zone_payload.json` goes back to
   being the grey-zone view. Don't fold these into the policy — they aren't grey
   zones, and step 8 re-judges them separately with `--with_low_flip`.

**If the examples don't group cleanly**, or the user would rather just look at them
one by one, open the scoring UI instead of doing the chat Q&A:
```
uv run scripts/serve_annotation.py --run_dir <run_dir>
```
They get the right control for the judge's type — Pass/Fail, one button per label, a
number on the scale, or a text box — plus an optional one-line "why". It saves as
they go and can be resumed. Then, **for boolean / categorical / numeric judges only**:
```
uv run scripts/recommend.py --run_dir <run_dir>
uv run scripts/aggregate.py --run_dir <run_dir>
```
**Stop here for a free-text (string) judge.** It can be measured and annotated but
not rewritten, and `recommend.py` refuses it rather than spending one model call per
annotation on guidance that `rewrite_eval` will then reject. Report the annotations
back to the user and end the session.

Both routes end at `aggregated.md`; steps 7 and 8 read whichever of
`grey_zone_policy.json` / `annotations.json` is **newer**, so if you switch to the UI
after starting the grey-zone route, the labels you just collected are the ones used.
Delete the abandoned artifact to keep it unambiguous.

### 7. Show the proposed rewrite, then create it only if they say yes  ⟵ GATE

**First, name the model that will do the rewriting.** Before running anything here,
tell the user which model writes the new prompt and offer the alternatives — one
short exchange, not a lecture:

> *"I'll use **deepseek-v4-pro** through your orq workspace to write the new prompt —
> that's the default, and it bills to orq like everything else in this run. It needs
> to be enabled on your workspace; I'll check before spending anything. If you'd
> rather use a different model, tell me which, or I can use a Claude subagent
> instead if you have the CLI set up."*

Three ways to go, all set in `config.toml`:
- **`backend = "orq_router"`** (default) with `backend_model = ""` → `deepseek/deepseek-v4-pro`.
- **a different model** → set `backend_model` to its **`refId`** from `GET /v2/models`,
  e.g. `groq/openai/gpt-oss-120b`. It must be the provider-qualified id, not the
  short display alias — the alias can route to the wrong provider or 404.
- **`backend = "claude_subagent"`** → shells out to `claude -p`, spending Claude
  credits instead of workspace credits. Needs the CLI installed and logged in.

Both scripts below preflight the model with one tiny call and stop with a plain
reason if it isn't reachable — a model can be listed in the registry and still
refuse for want of a provider key. If that happens, relay the reason and re-offer
the three options rather than retrying.

```
uv run scripts/rewrite_eval.py --run_dir <run_dir>
uv run scripts/create_eval.py --run_dir <run_dir>          # shows the diff, creates nothing
```
The rewrite is guarded: it can't change which fields the judge reads, drop a label, or
move the scale. The second command **shows** what would change — the guidance it drew
on, the before/after prompt diff, and whether those guards held. Walk the user through
it: what changed, and which of their answers drove it.

**Only after they say yes:**
```
uv run scripts/create_eval.py --run_dir <run_dir> --approve
```
(`--edits <file>` folds in their own wording.) This creates a **new** judge alongside
the old one — same answer type, named after the original with `-aligned-<timestamp>`,
placed in the same orq project as the source — and records which evaluator it came
from. **The original is never touched.** If they say no, stop; nothing is created.

### 8. Optional — check whether it actually worked  ⟵ GATE
Runs the same examples past the **new** judge and writes `retest_metrics.json`. It
uses the answers from `grey_zone_policy.json`, falling back to `annotations.json`.

**Two things both have to be true** for this to count as an improvement: the new
judge has to stop changing its mind, **and** its answers have to match what the user
said they should be. Steadiness alone is worthless — a judge that's wrong the same
way every time scores perfectly on it. If it got steadier but disagrees with the
user, report exactly that; don't call it a win.

**Agreement comes with a `before`.** The original run already judged these same
rows, so `retest_metrics.json` carries what the *old* judge scored against the same
labels, for free. Quote both. A new score of 0.78 that passes the 0.7 bar is a
regression if the old judge was at 0.85, and the pass/fail flag alone won't show it.

**Say what the numbers can't be.** `retest_metrics.json` carries a `caveats` list;
read it out rather than summarising it away. There are three, and they all point the
same way — the result is softer than it looks:
- these rows were chosen for being the *most* unstable, so re-measuring them drifts
  toward the middle on its own. `--baseline_rerun` re-runs the **old** judge over the
  same rows in the same pass, which is the only version of gate (a) that isolates the
  rewrite. It costs the same again — offer it, don't assume it;
- the same examples produced the rewrite guidance *and* the labels that score it.
  There's no holdout, so the agreement number is an upper bound;
- any label the user didn't confirm at step 6 is your reading of their rule, not
  their verdict. `metadata.label_provenance` counts them.

**Check what happened outside the grey zone.** `--with_low_flip` re-judges the
stable spot-check rows too and reports how many changed verdict. Nobody labelled
those, so a change isn't automatically wrong — but a rewrite that settles the grey
zone by unsettling everything else is the classic failure here, and this is the only
place it shows up.

**It re-judges only the examples you settled answers for** — those are the only ones
agreement can score, so re-running the rest would cost money for verdicts nothing
reads. The before/after instability comparison is recomputed over that same subset,
so the drop isn't an artifact of which rows were picked. Quote the cost accordingly:
**labelled examples × repeats**, not the whole dataset. (`--all_rows` re-judges
everything, if they want a run-wide re-measure.)

Repeats and temperature **default to whatever the step-3 run actually used**, read
back from `metrics.json`, so the comparison is like-for-like without anyone having to
remember. Overriding to a *lower* repeat count marks the comparison not comparable
(fewer samples under-estimate instability, which reads as a win), and gate (a) fails
rather than claiming one.
```
uv run scripts/retest.py --run_dir <run_dir>
uv run scripts/retest.py --run_dir <run_dir> --baseline_rerun --with_low_flip
```
(`--tol` is how close a score has to be to count as agreeing, default 0.5 on the raw
scale; `--num_samples` caps the rows, for a smoke test — it narrows both sides of the
comparison, so the before/after stays over the same rows.)

**Quote the cost before running it**, there's no estimator for this step: it's
`labelled rows × repeats` judge calls, doubled with `--baseline_rerun`, plus
`low_flip_sample_size × repeats` with `--with_low_flip`.

## Final summary
Tell them, in plain terms:
- **what changed in the judge and why** — tie it back to the answers they gave;
- **whether it actually got better**, on how many examples, and on both counts from
  step 8 (steadier, and agreeing with them);
- **what this did not check.** Everything here was measured on examples the judge was
  *unsure* about. A judge that is confidently wrong never wobbles, so it never showed
  up. Say this even when the numbers are good — especially then. Say what the stable
  spot-check examples showed (step 6.6) and whether the retest re-judged them, and
  suggest re-running periodically.
- **why the improvement number is an upper bound.** The same examples produced the
  guidance for the rewrite and the labels that scored it, with no holdout; the rows
  were picked for maximum instability, so some of the drop is regression to the mean
  unless `--baseline_rerun` was used; and any label the user didn't confirm is your
  application of their rule. `retest_metrics.json` `caveats` lists whichever of these
  apply — none of them is optional to mention.
- if any examples came from step 1a options 2–4, say that too: they test what the
  rubric says, not what production actually sends.

## Configuration & backends
`config.toml` holds all defaults. The model that writes the recommendation (step 6/8)
and the rewritten prompt (step 7) is selectable via `backend`:

| `backend` | What it uses | API key (env only) | Endpoint env var (default) |
|---|---|---|---|
| `orq_router` **(default)** | any workspace model over `/v3/router`; `backend_model` blank → `deepseek/deepseek-v4-pro` | `ORQ_API_KEY` — already required | `ORQ_BASE_URL` (`https://my.orq.ai`) |
| `claude_subagent` | `claude -p` | the `claude` CLI, installed and logged in | — |
| `anthropic_api` | Anthropic Messages API | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` (`https://api.anthropic.com`) |
| `orq_deployment` | an existing orq deployment | `ORQ_API_KEY` + `backend_deployment_key` | `ORQ_API_BASE_URL` (`https://api.orq.ai`) |
| `fake` | canned completions | none (tests) | — |

**All three inputs resolve the same way, most specific first:**

```
CLI flag  →  config.toml  →  environment variable  →  built-in default
```

so `--backend`, `--backend_model` and `--backend_base_url` on `recommend.py` /
`rewrite_eval.py` override the config for one run without editing a file:

```
uv run scripts/rewrite_eval.py --run_dir <run_dir> \
  --backend orq_router --backend_model groq/openai/gpt-oss-120b \
  --backend_base_url https://orq.internal.example.com
```

`backend_model` is optional — blank takes the chosen backend's own default, so
changing `backend` alone is a valid edit. For `orq_router` it must be the
provider-qualified `refId`. `orq_router` prices its calls from the registry's own
per-1K rates, so the reported cost is real rather than zero. The preflight message
names the resolved backend, model **and** endpoint, so tell the user which host is
about to receive their rubric before they approve a paid step.

**Self-hosted or proxied orq:** `ORQ_API_BASE_URL` moves every control-plane call
too (evaluators, traces, projects, datasets, create), so that one variable plus
`ORQ_BASE_URL` for the router covers the whole skill.

**API keys are environment-only.** `ORQ_API_KEY`, `ANTHROPIC_API_KEY`, or the
`claude` CLI's own login — read from the environment or a `.env` file, which every
script loads. There is deliberately no `--backend_api_key` flag: a key passed on the
command line lands in shell history and in every `ps` listing on the machine. If a
key is missing, the error names the variable it wanted.

See `lib/model_backend.py` for the nested-template-variable handling that keeps the
embedded judge prompt's own `{{query}}`/`{{output}}` tokens intact — a hazard for
`orq_deployment` only, since the string backends never re-template their input.

## Parameter reference
Every script is a `python-fire` CLI: pass any `main()` param as `--param value`.
**All** steps also accept `--config <path>` (default `config.toml`) and, except
step 1, `--run_dir <dir>` (required in practice). Flags default to `None` and
resolve to the config value shown; overriding a flag beats `config.toml`.

| Script | Overridable flags (default) |
|---|---|
| `fetch_evaluator.py` | `--evaluator_id` (req/config), `--with_traces` (True; `--no-with_traces` to skip), `--trace_limit` (200), `--judge_model` (slug override when the config id can't be resolved) |
| `fetch_traces.py` | `--trace_limit` (200) |
| `estimate_cost.py` | `--n_repeats` (cfg 8), `--num_samples` (cfg -1 = all) |
| `stability.py` | `--num_samples` (cfg -1), `--n_repeats` (cfg 8), `--max_concurrency` (cfg 8), `--temperature` (cfg 1), `--metrics` (True; `--no-metrics` to skip) |
| `metrics.py` | — (run_dir/config only) |
| `build_queue.py` | `--count` (-1 = all), `--low_flip_sample_size` (cfg 5). Also projects the step-6 context budget into `queue.json` `meta.grey_zone_projection` |
| `dataset_inputs.py list` | `--limit` (100) |
| `dataset_inputs.py pull` | `--dataset_id` (req), `--limit` (200) |
| `seed_inputs.py convert` | — (run_dir/config only) |
| `seed_inputs.py save` | `--dataset_name` (new) OR `--dataset_id` (append) |
| `cross_model.py` | `--model` (req; 2nd judge slug), `--num_samples`, `--n_repeats`, `--tol` (0.5) |
| `grey_zone.py assemble` | `--top_k` (cfg `grey_zone_top_k`; -1 = all, 0 = none), `--max_chars` (cfg `grey_zone_max_chars`; 600 — per-example input budget, fair-shared across `{{variables}}`), `--max_tokens` (cfg `grey_zone_max_tokens`; 60000 — payload ceiling, clamps to the fitting prefix), `--include_low_flip` (False — bring the stable spot-check rows into the payload too) |
| `grey_zone.py apply` | — (run_dir/config only) |
| `serve_annotation.py` | `--port` (8765) — the interactive UI fallback |
| `recommend.py` | `--backend`, `--backend_model`, `--backend_base_url` (each: flag → config → env → default) |
| `aggregate.py` | — |
| `rewrite_eval.py` | `--max_attempts` (3), `--backend`, `--backend_model`, `--backend_base_url` |
| `create_eval.py` | `--approve` (False), `--edits <file>` (None), `--force` (False; bypass create-side guards, e.g. non-routable judge slug) |
| `retest.py` | `--n_repeats` / `--temperature` (default: whatever the step-3 run used), `--num_samples` (cap rows, smoke test — narrows both sides of the comparison), `--tol` (0.5, numeric within-tolerance band), `--all_rows` (False — by default only the **labelled** rows are re-judged), `--with_low_flip` (False — also re-judge the stable spot-check rows as a regression check), `--baseline_rerun` (False — re-run the OLD judge over the same rows for a true A/B; doubles the cost) |

## Run directory contract
Every artifact lives in `runs/<key>_<ts>_<model>_<N>dp/`: `evaluator.json` (with
`output_type` + `categorical_labels`/`scale`), `traces.jsonl`, `stability.json`,
`metrics.json`, `queue.json` (each confuser carries its `verdict_space` + a
`reason` of instability/cross_model/low_flip), `synthetic_datapoints.json`
(conductor-authored seed, §11) + `cross_model.json` (second-model disagreers) when
a starved run was seeded, `grey_zone_payload.json` (the conductor's bounded confuser payload),
`grey_zone_policy.json` (grey zones + questions + answers + per-point policy
labels with their `label_source` — the default feedback artifact),
`annotations.json` (typed values — the UI fallback), `recommendations.json`,
`aggregated.md` + `aggregated.json`, `new_prompt.md`, `rewrite_status.json`,
`approval.json`, `new_evaluator.json`, `retest_metrics.json`, and the retest's own
sub-runs `retest/` (+ `retest_baseline/` with `--baseline_rerun`), each holding the
filtered `traces.jsonl` and the `index_map.json` that pairs its positional
`source_index` back to the parent's. Any step is re-runnable in isolation against an
existing run directory.

**`source_index` is a position in `traces.jsonl`, and that file is the run's spine.**
`stability.json` and `queue.json` record a `traces_fingerprint` of it; `fetch_traces`
**rewrites** the file (`dataset_inputs` and `seed_inputs` only append), so widening
the trace window after labelling renumbers every row underneath the labels. The
grey-zone assemble warns on a mismatch and the retest refuses outright. If you need
more data after labelling, re-run stability → metrics → build_queue and redo the
labels rather than pairing them against a file that moved.

## Companion Skills

- **orq-cli** — the same platform operations from a shell, for anything that must run again without an agent present (CI, cron, scripts, bulk): auth via `ORQ_API_KEY`, `--json` output. See its "MCP tools or the CLI?" table before choosing.
