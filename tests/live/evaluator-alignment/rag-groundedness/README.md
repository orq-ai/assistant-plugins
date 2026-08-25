# Test case — RAG groundedness

A live end-to-end exercise for the **evaluator-alignment** skill: one orq dataset of
24 datapoints plus three judge evaluators (boolean, categorical, numeric) that
all read the same datapoints, and an answer key that says what the skill *should*
surface.

It is not a unit test. The skill's own `tests/` directory covers the plumbing offline
with `backend = "fake"`; this exercises the real thing — real judge calls, real cost,
real workspace objects — and answers a question the unit tests cannot: *does the skill
find the ambiguity that is actually there, and does it admit what it cannot see?*

## Files

| File | What it is |
|---|---|
| `datapoints.json` | The 24 datapoints, in orq's `{inputs: {input, output}}` shape. `_case_id` is local bookkeeping and is stripped before upload. |
| `evaluators.json` | The three judge rubrics, all deliberately underspecified. |
| `create_testcase.py` | Provisions and tears down the dataset + evaluators. |
| `answer_key.json` | **Spoiler.** Reference policy, per-case truth, measured baseline, pass criteria. |
| `score_run.py` | Joins a finished run back onto the answer key and reports PC1 / PC3. |
| `created.json` | Written by `create_testcase.py`; the exact ids to tear down. |

## Setup

```bash
uv run create_testcase.py create --dry_run   # inspect
uv run create_testcase.py create             # provision
uv run create_testcase.py delete             # remove everything in created.json
```

`ORQ_API_KEY` is read from the environment or a `.env` on the path.

## Running the skill against it

From the skill root (`skills/orq-evaluator-alignment/`):

```bash
# 1. Confirm the evaluator. The trace scan returns 0 by construction —
#    a brand-new evaluator has no production traffic.
uv run scripts/fetch_evaluator.py --evaluator_id <id> --judge_model google/gemini-2.5-flash-lite

# 2. Feed the datapoints in via the step-4a recovery menu, option 2.
uv run scripts/dataset_inputs.py pull --run_dir <run_dir> --dataset_id <dataset_id>

# 3. Measure, then continue through the normal flow.
uv run scripts/stability.py --run_dir <run_dir> --n_repeats 8
```

**For the numeric evaluator, pass the scale at step 1:**

```bash
uv run scripts/fetch_evaluator.py --evaluator_id <numeric id> \
  --judge_model google/gemini-2.5-flash-lite --scale_min 1 --scale_max 5
```

The `scale: [1, 5]` sent at creation is not returned by `GET /v2/evaluators`, so step 1
resolves `scale=None` and every numeric row is reported `unmeasurable`. The scale is
override-only by design — supply it here or in `config.toml`.

Then score it:

```bash
# still from the skill root, like the steps above — `<run_dir>` is relative to it
uv run ../../tests/live/evaluator-alignment/rag-groundedness/score_run.py --run_dir <run_dir>
```

### The judge model is the main knob

It decides which half of the skill you are testing. Pin it by editing `judge_model`
in `<run_dir>/evaluator.json` (SKILL.md step 2), or pass `--judge_model` at step 1.

- **`google/gemini-2.5-flash-lite`** — the main path. Mean instability **0.125**, six
  unstable rows, five of them grey-zone cases and no anchor above zero. The confuser
  queue, grey-zone clustering, rewrite and retest all have something to work on.
- **`google/gemini-2.5-flash`** — the blind-spot variant. Mean instability **0.031**,
  23 of 24 rows perfectly stable. A flat profile is the *correct* result here and
  should route the conductor back to the step-4a menu.

## What the datapoints are for

**12 grey-zone cases**, four clusters of three, each a distinct reason the rubric fails
to decide: an answer entailed only by inference (GZ1), a true-but-unsupported addition
(GZ2), a loose numeric paraphrase (GZ3), and openly flagged speculation (GZ4). These
are what step 6's open-coding is supposed to rediscover.

**8 anchors** — four verbatim restatements, four flat contradictions. They give the
ranking something to sort against and populate the `low_flip_sample_size` draw.

**4 traps** — engineered to be *stable and wrong*: support buried at the end of a long
context, a style-matched fabrication, a correct refusal, and a misattributed figure that
does appear in the context. Instability ranking cannot surface these by construction.

## The finding this test case exists to produce

Run the boolean evaluator on `gemini-2.5-flash` and the self-consistency stats say the
judge is excellent — 1-Flip Consistency **0.978**, Fleiss κ **0.947**, Gwet AC1
**0.961**, mean instability **0.031**. The answer key says it is wrong on five of 24,
including two anchors that are word-for-word restatements of their context. Its own
rationale for one of them:

> "The job title mentioned in the answer (on-call engineer) does not match the job title
> in the context (on-call engineer)."

That is SKILL.md's documented blind spot, reproduced on demand: **stability is a
ceiling, not proof.** A conductor that reports "the judge is highly self-consistent"
and stops has failed the test, however good its numbers look.

## Known deviations from the original design

Measured, not assumed — see `answer_key.json → measured_baseline`.

- **GZ1 does not destabilise either judge.** Both decide it confidently, and both get
  the syllogism and the arithmetic case wrong while getting the transitivity case right
  — an incoherent split held with full confidence. GZ1 behaves as two extra traps, not
  as a confuser cluster. Its absence from the confuser queue is expected; its absence
  from the final summary's blind-spot caveat is a failure.
- **Anchors A2 and A3 are stably wrong on both judges** despite being verbatim
  restatements. Kept deliberately: the blind spot is not confined to adversarial inputs.
- Roughly **10 of 192 repetitions fail transiently** per run. Normal, not a signal.

## Bugs this test case found in the skill

Both were real and both are now fixed (see CHANGELOG 2.4.1); they are recorded here
because they are the return on building this test case, and because a regression in
either is invisible to `tests/`.

1. **`build_create_datapoints_body` sent the wrong shape.** It wrapped rows as
   `{"datapoints": [...]}`; `POST /v2/datasets/{id}/datapoints` wants a bare array and
   rejects the object with `400 invalid_request_body` ("expected array, received
   object"). This broke `seed_inputs.py save`, the dataset save-back path, entirely.
   Fixed — the builder now returns the list unchanged.
2. **`seed.unresolved_variables` did not mirror `judge.make_replacements`.** Its
   docstring claimed it did. `make_replacements` resolves
   `reference | expected | expected_output`; `unresolved_variables` had no branch for
   them, so an evaluator declaring `{{log.reference}}` rendered fine at judge time while
   **100% of its dataset rows were silently skipped** at pull time. Fixed — the
   reference family is handled, and the two functions are now documented as a pair.

Still open: neither function handles `context`, which is why this test case folds the
retrieved context into the input variable rather than declaring one.
