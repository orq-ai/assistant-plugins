# Steps 0–1a: find the judge, choose the examples

## 0. Do they have a judge in orq?  ⟵ GATE
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

## 1. Check the judge
Once you have the ID, run **one** command:
```
uv run scripts/fetch_evaluator.py --evaluator_id <id>
```
This fetches the judge and **stops** — where the examples come from is step 1a, and
it is the user's call, not a default. Tell the user, in their words, what came back:
- **the judge is the right one** — say what it looks at (its template variables) and
  what it decides, in one sentence, and ask them to confirm;
- **which model is doing the judging** (`judge_model` on `evaluator.json`). Resolved
  in priority order: an explicit `--judge_model` override → the evaluator's config
  model id looked up via `GET /v2/models` (registry UUID → slug) → the model seen on
  the production judge spans (plus `judge_models_observed`). If more than one model
  shows up, mention it — a judge whose model changed over time looks more erratic
  than it is.

  **If the model comes back unresolved** (`evaluator.json`'s `judge_model` still
  equals `judge_model_id` — the opaque config id, unresolved against `/v2/models`):
  the config id wasn't in `/v2/models` *and* the spans don't record
  `gen_ai.request.model` — common, because evaluator spans store the judge's input
  and output but not always which model produced them. Ask the user which model the
  judge uses (it's in the evaluator's model dropdown in orq) and rerun:
  ```
  uv run scripts/fetch_evaluator.py --evaluator_id <id> --judge_model mistral-large-latest
  ```
  Without it, we can't re-run the judge at step 3, so this has to be settled here.

The command prints a run directory — **pass that `--run_dir` to every later step.**
It starts as `<key>_<ts>` and is renamed to `<key>_<ts>_<model>_<N>dp` once the model
and example count are known, so always use the **printed** path. If the judge's
output type isn't boolean, categorical, or number, it stops here — tell the user
those three are what's supported. orq's free-form `string` type is deliberately
among the refusals: instability over prose is exact-match entropy, which scores
nearly every row as maximally unstable and so cannot rank anything, and gate (b)
at step 8 has no way to compare two correct answers worded differently. It also hard-exits if `--scale_min`/
`--scale_max` are passed one without the other — the numeric scale override is
both-or-neither, so a partial pair is a user error rather than something to guess at.

## 1a. Ask where the examples should come from  ⟵ GATE
**Always ask. There is no default source.** The judge is fetched; nothing has been
scanned, pulled or generated yet. Four routes, and the right one depends on facts
only the user has — how long this judge has been live, whether it fires often, whether
the cases they care about have happened yet.

> *"I've got the judge. Now I need examples to test it on. I can scan your production
> traces for cases it's already scored, pull an orq dataset you've already got, take
> examples you bring me, or generate some. Which fits?"*

Lead with the trace scan and say why it's the one to beat: **it is the only source
that shows what the judge actually meets in production.** The other three test what
the rubric *says*. That's a real difference and worth one sentence, but it is not a
reason to pick for them — a judge that went live yesterday has nothing to scan, and
saying so up front beats an empty scan that reads like a dead end.

**They can combine sources**, and several will want to: scan production, then top up
with generated borderline cases. One ordering constraint — **the trace scan must go
first.** It rewrites `traces.jsonl` wholesale; the other three append. Running it
second deletes what they added, so it refuses when it finds rows from another source
(`--replace` to override deliberately).

1. **Scan production traces.** Reads the most recent traces and keeps the ones this
   judge has already scored.
   ```
   uv run scripts/fetch_traces.py --run_dir <run_dir>                    # 200 (default)
   uv run scripts/fetch_traces.py --run_dir <run_dir> --trace_limit 2000 # deeper
   ```
   It reports the count **next to the window it came from**, and whether it hit the
   cap: *"18 datapoints from the 200 most recent traces — and the scan filled up"*
   means there is history behind it, so offer the deeper scan. If it came back
   **under** the cap it already saw every trace in the window; a bigger limit
   re-scans the same traces, so widen `trace_start_date`/`trace_end_date` instead.
   Read this back before step 2 prices the run: more examples cost proportionally
   more, and after step 6 re-scanning is no longer free — it rewrites the file every
   label is keyed into, so it means redoing stability → metrics → build_queue and
   the labelling.
2. **Use a dataset already in orq.**
   ```
   uv run scripts/dataset_inputs.py list --config config.toml        # pick one
   uv run scripts/dataset_inputs.py pull --run_dir <run_dir> --dataset_id <id>
   ```
   It matches the dataset's columns to what the judge reads, deriving `output` from
   the last assistant turn and `query` from the last user turn when the exchange
   lives under `messages` rather than in `inputs`. This is also the route for
   `{{input.retrievals}}` and `{{input.system_instructions}}`: orq emits no span
   attribute for either, so a trace only carries them when the judge prompt
   interpolated them and the scanner recovers them from the rendered text — a
   dataset column (or `--map`) is the reliable source. If a field still can't be matched
   it prints **one inventory** — what the judge needs, what the dataset holds, what
   each field could map to — instead of one line per row. Read that back and ask the
   user to confirm the mapping; don't guess:
   ```
   uv run scripts/dataset_inputs.py pull --run_dir <run_dir> --dataset_id <id> \
       --map "output.response=messages.assistant.last"
   ```
   **Datasets often carry ground truth** (`expected_output`). That is the single
   most valuable thing a run can have: it turns step 4 from "is the judge steady?"
   into "is it *right*?", and it is the only way to see a judge that is stable and
   wrong. Say so when offering this option.
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

**Check what you ended up with before moving on.** Fewer than 10 usable examples is
too few to tell signal from noise — say so in one line (*"that's 4 examples, which
isn't enough to tell a real disagreement from a coin flip"*) and offer the remaining
routes rather than proceeding. This is a normal outcome for a judge that is new,
rarely triggered, or older than the scan window, not a failure.

**Come back here from step 4** if the judge turns out perfectly consistent on
everything — there is nothing to review, and more or different examples is the fix.

**Not an input source, but it belongs in the same conversation:** when there is plenty
of data and the judge simply never wavers, ask a **second model** to judge the same
examples and treat the disagreements as the interesting cases. This needs a completed
`stability.py` run first, so it is a step-4 remedy rather than a step-1a choice.
Confirm the second model with the user.
```
uv run scripts/cross_model.py --run_dir <run_dir> --model <provider/model>
```

**If the judge model came back unresolved** (`evaluator.json`'s `judge_model` still
equals `judge_model_id`) and they picked a non-trace source, ask for the model now —
production spans were the fallback that would have supplied it, and step 3 cannot
re-run a judge it cannot name.

**Say this in the final summary whenever options 2–4 contributed:** examples that
didn't come from production test what the rubric *says*, not what the judge actually
meets in the wild. The alignment is only as representative as the data behind it.

---

Next: step 2 — read [measure.md](measure.md).
