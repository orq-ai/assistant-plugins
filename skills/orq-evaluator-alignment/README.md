# Evaluator Alignment skill

A standalone, human-in-the-loop skill that realigns an existing **LLM-judge
evaluator** (boolean, categorical, numeric, or free-form string) to human
judgment. Given an orq evaluator id and its production traces, it measures the
judge's self-consistency, finds the examples it is least sure about, works out
*why* those examples are hard, asks the user a handful of questions that each
settle a whole group of them, turns the answers into a rewritten judge prompt,
and — only after the human approves — creates a new evaluator.

See [`SKILL.md`](SKILL.md) for the conductor flow; this file is the maintainer's
map of the scripts underneath it.

Sits alongside `orq-build-evaluator` and `orq-optimize-prompt` in the orq skills
family. Every step script is self-contained via PEP 723 inline dependencies, so
`uv run scripts/<name>.py` builds its own environment — no repo or `uv sync`
required.

## The shape of it

The expensive part of aligning a judge is human attention, so the flow spends it
as late and as narrowly as possible:

1. **Measure, don't ask.** Re-judge each datapoint N times and score the judge's
   self-inconsistency on one 0..1 *instability* scale, whatever its output type —
   boolean flip-rate, categorical label entropy, numeric score spread, string
   exact-match entropy. Instability ranks the queue; nobody labels anything yet.
   When the examples arrived with ground truth (a dataset's `expected_output`),
   the same pass also scores **correctness** — and reports accuracy *by band*,
   because accuracy on the rows the judge was steady about is the one measurement
   that can catch a judge that is consistently wrong.
2. **Group, then ask once per group.** The grey-zone stage clusters the unstable
   examples by what makes them hard and puts a bounded payload in the conductor's
   context, so the user answers ~5 questions instead of labelling ~20 rows
   (RES-980). The per-row annotation UI remains as a fallback for the case the
   ticket itself names: when nothing clusters.
3. **Rewrite from the answers, and prove it moved.** The rewrite preserves the
   verdict space, and the retest has to clear *two* bars — the judge got steadier
   **and** it now agrees with the user. Steadiness alone is worthless; a judge
   that is wrong the same way every time scores perfectly on it.
4. **Say what the result cannot be.** Agreement is reported against what the
   *original* judge scored on the same labels, and `retest_metrics.json` carries a
   `caveats` list the conductor reads out: the retested rows were chosen for
   maximum instability (so some of the drop is regression to the mean unless
   `--baseline_rerun` re-runs the old judge over them), the same examples produced
   the rewrite guidance and the labels scoring it, and any label the user did not
   confirm is the conductor's application of their rule.

## Pipeline at a glance

Step numbers are the conductor steps in `SKILL.md`. Every artifact lives in one
run directory, born `runs/<key>_<ts>/` at step 1 and renamed to
`runs/<key>_<ts>_<model>_<N>dp/` only once a trace scan (step 1a option 1) has
resolved the judge model and datapoint count — a run built entirely from a
dataset, bring-your-own, or generated examples never gets that suffix. Any step
is re-runnable in isolation against an existing run directory.

| Step | Script | Reads | Writes |
|---|---|---|---|
| 0 | _(gate — no script)_ | — | does the user even have a judge in orq yet? |
| 1 | `fetch_evaluator.py` | evaluator id | `evaluator.json` — the judge only; the input source is the user's call |
| 1a | `fetch_traces.py` | `evaluator.json` | `traces.jsonl` + `scan.json` — **input source 1**, production traces (overwrites; run it first) |
| 1a | `dataset_inputs.py list\|pull` | orq dataset | **input source 2** — appends to `traces.jsonl` |
| 1a | `seed_inputs.py convert\|save` | datapoints | **input sources 3 + 4** (bring your own / generated) — appends; `save` writes back to orq |
| 2 | `estimate_cost.py` | `traces.jsonl` | _(prints call + token projection; gate)_ |
| 3 | `stability.py` | `traces.jsonl`, `evaluator.json` | `stability.json` (carries `reference` when the source had ground truth) |
| 4 | `metrics.py` | `stability.json` | `metrics.json` (auto-run by `stability.py`) — instability, plus a `correctness` block when rows carried labels |
| 4 | `cross_model.py` | `stability.json` (+ `traces.jsonl`) | `cross_model.json` — second-judge disagreers; a step-4 remedy for a judge that never wavers, not an input source |
| 5 | `build_queue.py` | `metrics.json` | `queue.json` — confusers by `reason`: instability / cross_model / wrong_vs_reference |
| 6 | `grey_zone.py assemble` | `queue.json` | `grey_zone_payload.json` — the bounded confuser payload |
| 6 | `grey_zone.py apply` | `grey_zone_policy.json` (already carries the per-point labels) | `aggregated.md` — rewrite guidance |
| 6 | `serve_annotation.py` | `queue.json` | `annotations.json` — the per-row UI fallback |
| 6 | `recommend.py` | labels, `stability.json`, `evaluator.json` | `recommendations.json` |
| 6 | `aggregate.py` | `recommendations.json` | `aggregated.md` + `aggregated.json` |
| 7 | `rewrite_eval.py` | `aggregated.md`, `evaluator.json` | `new_prompt.md`, `rewrite_status.json` |
| 7 | `create_eval.py` | `new_prompt.md`, `evaluator.json` | `approval.json`, `new_evaluator.json` |
| 8 | `retest.py` | `new_prompt.md`, `grey_zone_policy.json` | `retest_metrics.json`; free-text judges round-trip via `string_pairs.json` → `string_verdicts.json` |

## Quick start

```bash
cd skills/orq-evaluator-alignment

uv run scripts/fetch_evaluator.py --evaluator_id <id>          # the judge only; prints the run dir
RUN=runs/<key>_<ts>                                             # printed path — not renamed yet
# Then pick an input source (ask the user — there is no default). Traces:
uv run scripts/fetch_traces.py     --run_dir $RUN                      # 200 most recent
uv run scripts/fetch_traces.py     --run_dir $RUN --trace_limit 2000   # deeper
# fetch_traces renames the dir to embed the model + datapoint count once both are
# known — re-capture the PRINTED path before the next command:
RUN=runs/<key>_<ts>_<model>_<N>dp
# …or an orq dataset / your own examples / generated ones — see step 1a. Those
# append and never rename the dir (it stays runs/<key>_<ts>/), so run the trace
# scan first if you are combining sources.
uv run scripts/estimate_cost.py    --run_dir $RUN              # cost gate
uv run scripts/stability.py        --run_dir $RUN --num_samples 2      # smoke
uv run scripts/stability.py        --run_dir $RUN              # full run (+metrics)
uv run scripts/build_queue.py      --run_dir $RUN --count 25
uv run scripts/grey_zone.py assemble --run_dir $RUN            # read the payload into context
#   … conductor asks the user its questions, reads the derived labels back,
#   writes grey_zone_policy.json …
uv run scripts/grey_zone.py apply  --run_dir $RUN              # answers -> aggregated.md
#   … fallback route only, when the examples don't group: serve_annotation.py,
#   then recommend.py + aggregate.py, which write the same aggregated.md …
uv run scripts/rewrite_eval.py     --run_dir $RUN
uv run scripts/create_eval.py      --run_dir $RUN              # presents the diff
uv run scripts/create_eval.py      --run_dir $RUN --approve    # after human OK
uv run scripts/retest.py           --run_dir $RUN              # optional: did it move?
```

`config.toml` holds all defaults (repetitions, temperature, backend, sample
sizes). CLI flags override per run. `SKILL.md`'s parameter reference lists every
flag.

## Backends (recommend + rewrite)

`config.backend` selects the model that writes the recommendation (step 6) and
the rewritten prompt (step 7):

| `backend` | What it uses | API key (env only) | Endpoint env var (default) |
|---|---|---|---|
| `orq_router` **(default)** | any workspace model over `/v3/router`; blank `backend_model` → `deepseek/deepseek-v4-pro` | `ORQ_API_KEY` — already required | `ORQ_BASE_URL` (`https://my.orq.ai`) |
| `claude_subagent` | `claude -p ... --output-format json` | the `claude` CLI, installed **and logged in** | — |
| `anthropic_api` | Anthropic Messages API | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` (`https://api.anthropic.com`) |
| `orq_deployment` | an existing orq deployment | `ORQ_API_KEY` + `backend_deployment_key` | `ORQ_API_BASE_URL` (`https://api.orq.ai`) |
| `fake` | canned completions | none (tests) | — |

Backend, model and endpoint each resolve **flag → `config.toml` → env var →
built-in default**, so nothing here needs a code edit:

```bash
uv run scripts/rewrite_eval.py --run_dir $RUN \
  --backend orq_router --backend_model groq/openai/gpt-oss-120b \
  --backend_base_url https://orq.internal.example.com
uv run scripts/rewrite_eval.py -- --help    # all three, with their fallbacks
```

For a self-hosted orq, `ORQ_API_BASE_URL` also moves every control-plane call
(evaluators, traces, projects, datasets, create), so that variable plus
`ORQ_BASE_URL` for the router covers the whole skill. **API keys are
environment-only** — from the environment or a `.env`, never a flag, since a key
on the command line lands in shell history and in `ps`. The step-7 preflight
prints the resolved backend, model and endpoint before anything is spent.

`orq_router` is the default because the rewrite is a text transform, not an
agentic task, and the previous default silently assumed a second tool was
installed and authenticated. `backend_model` is optional per backend — blank
takes that backend's own default, so switching `backend` alone is a valid edit
rather than a way to hand a Claude model id to the router. For `orq_router` it
must be the provider-qualified `refId` from `GET /v2/models`, not the shorter
display alias. Both scripts preflight with one tiny call and distinguish
403 / 404 / 429, rather than failing partway through a paid run.

The meta-prompt and PO2 prompt embed the audited judge prompt, which carries its
own `{{query}}`/`{{output}}` tokens. The string backends keep those literal;
`orq_deployment` self-references them so the templating engine renders them
unchanged. See `lib/model_backend.py`.

## Shared vocabulary

`lib/content.py` holds the two rules the trace scanner and the judge have to
agree on — the `{{variable}}` → row-field suffix table, used in both directions,
and the parts-aware message flattener. It is stdlib-only on purpose:
`lib/judge.py` imports evaluatorq at module scope, and `scripts/fetch_traces.py`
must stay importable without it.

Anything that maps a template variable to a field, or reads text out of a
message, goes through it. Every past attempt to keep a private copy "in sync by
hand" has drifted — `lib/seed.py`'s copy lost the `reference` family and silently
skipped every row of an evaluator declaring `{{log.reference}}`, and the trace
scanner's copy dropped `{{...expected_output}}` so the retest compared against a
blank ground truth.

## Tests

```bash
cd skills/orq-evaluator-alignment
uv run --no-project --with-requirements tests/requirements.txt python -m pytest tests/ -q
```

This is the exact command the `skill-tests` job in `skills-ci` runs. The scripts
declare their dependencies inline (PEP 723), which pytest never reads because it
imports the modules directly — hence `tests/requirements.txt`, which is also the
file that opts a skill into that CI job.

`test_pipeline.py` runs the full pipeline (stability → metrics → build_queue →
annotation-load → recommend → aggregate → rewrite) on a synthetic fixture with
the judge monkeypatched and the `fake` backend — no network. The rest are unit
suites per module. `tests/fixtures/responses_api_trace.json` is a **captured
production trace**, kept so the Responses-API span shape is pinned against the
real thing rather than a hand-rolled guess.

`testcases/rag-groundedness/` is different in kind: a live end-to-end test case
that provisions a real orq dataset and four judges, exercises the real API, and
grades a finished run against an answer key. It answers what unit tests cannot —
does the skill find the ambiguity that is there, and does it admit what it cannot
see. It reproduces the consistently-wrong blind spot below on demand.

> **Windows note.** Only in a fully extra'd research monorepo: an autoloaded
> third-party pytest plugin there imports the SSL stack, which aborts the process
> on this host (the OpenSSL Applink crash). If you hit that, run with
> `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. A plain skill install pulls none of that.

## Scope & limitations

- **Multi-type.** Measurement (stability → instability → confuser ranking)
  supports **boolean, categorical, numeric, and free-form string** judges on one
  0..1 instability scale. The **improve** half (grey zone → recommend → rewrite →
  create → retest) supports **boolean, categorical, and numeric** (RES-978 /
  RES-980); the rewrite preserves the verdict space (label set / numeric scale)
  and numeric rewriting is deliberately shallow (anchor-nudge, not calibration).
  **String runs the whole flow with one difference at step 8** — agreement is
  scored by *reading* the answers rather than by `==`, because two correct
  free-text answers worded differently would otherwise score as a disagreement
  and reject every rewrite. The retest writes `string_pairs.json`, the conductor
  (or the user) decides match/no-match per example against the grey-zone rule, and
  the run resumes from `string_verdicts.json`. `recommend.py` — the fallback path
  only — still refuses string for that same `==` reason. Step 1 accepts all four
  types and fails fast on anything else.
- **Self-consistency ≠ validity.** Instability localises where the judge is
  unstable; it cannot prove the judge is correct.
- **Consistently-wrong blind spot.** Instability-ranking never surfaces items the
  judge gets wrong *consistently* (instability ≈ 0). The `low_flip_sample_size`
  config adds a random low-instability sanity sample as the cheap mitigation, and
  every final report states the limitation. The live test case exists partly to
  keep this honest: on `gemini-2.5-flash` it scores Gwet AC1 0.961 and mean
  instability 0.031 on a judge that is wrong on 5 of 24.
- **One explanation per example.** evaluatorq's jury layer collapses the N
  repetitions to a single representative rationale, so the grey-zone payload
  cannot show per-repetition reasoning. On an exact tie there is no rationale at
  all; the payload flags that (`reasoning_available`) rather than passing the
  tie-break notice off as the judge's reasoning.
- **Local annotation store.** Annotations persist to disk (ADR-14 `human_review`
  shape). orq-native human-review-column persistence lands with RES-843.
- **Step 8 is a retest, not a scheduler.** The resumable run directory is the
  hook a future cadence would re-enter.
