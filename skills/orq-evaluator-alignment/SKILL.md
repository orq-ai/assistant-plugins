---
name: orq-evaluator-alignment
description: >-
  Align, calibrate, or improve an existing binary Pass/Fail LLM-as-a-judge (orq
  evaluator) so its verdicts match human judgment. Use when the user wants to
  "align my evaluator", "improve my eval", "annotate an evaluator", "find
  ambiguous cases", or "build an annotation queue" — i.e. they have a boolean
  judge (evaluator = LLM-as-a-judge) that disagrees with human labels or is
  inconsistent. Measures judge self-consistency (flip-rate) via repeated runs,
  surfaces the most ambiguous datapoints for human annotation, rewrites the judge
  prompt from the labels, and creates the new evaluator only after the human
  approves. If the evaluator ID isn't given, ask for it after triggering. Do NOT
  use to build an evaluator from scratch (use orq-build-evaluator), to fix
  failures with prompt tweaks (use orq-optimize-prompt), or for non-boolean judges.
---

# Evaluator Alignment

You are the **conductor** of a human-in-the-loop pipeline (RES-930) that rewrites
a binary LLM-judge evaluator to better match human judgment. You run small,
independently-runnable scripts under `scripts/`; each writes one artifact into a
per-run working directory (`runs/<key>_<ts>_<model>_<N>dp/`). **The human stays in control of
every consequential action** — the prompt rewrite, creating the new evaluator,
and any retest. Never skip a gate.

Each script under `scripts/` is self-contained: it declares its own dependencies
via PEP 723 inline metadata, so `uv run scripts/<name>.py ...` builds an isolated,
cached environment on first run — no `uv sync`, no project venv, no repo needed.
Always invoke as `uv run scripts/<name>.py` (not `uv run python scripts/...`, which
bypasses the inline metadata).

## Constraints

- **Boolean Pass/Fail judges only** (V1). Step 1 fails fast on anything else.
- **Self-consistency is a ceiling, not proof.** A low flip-rate means the judge
  is *stable*, not *correct* — a judge can be consistently wrong. You surface
  ambiguity; the human supplies truth.
- **Known blind spot:** flip-ranking never surfaces consistently-wrong items
  (flip-rate ≈ 0). You MUST state this in your final summary, and you offer the
  low-flip sanity sample (config `low_flip_sample_size`) as the cheap mitigation.

## The flow

### 1. Confirm the evaluator (+ a first 200-trace scan)
Ask for the evaluator **id**. (To find it: open the evaluator in orq, click
**View code**, and copy the `id="01..."` shown there.) Then run **one** command:
```
uv run scripts/fetch_evaluator.py --evaluator_id <id>
```
This both fetches the evaluator **and** auto-chains a 200-trace scan, so a single
step confirms everything the user needs to greenlight the run:
- the **evaluator is right** — echo back the declared template variables and a
  short paraphrase of the judge prompt;
- the **candidate datapoint count** (`traces.jsonl`);
- the **real judge model** pinned onto `evaluator.json` (`judge_model`). It is
  resolved in priority order: an explicit `--judge_model` override → the
  evaluator's config model id looked up via `GET /v2/models` (registry UUID →
  slug) → the model observed on the production judge spans (step 2, plus
  `judge_models_observed`). Flag it if more than one model shows up (a
  mixed-model history can inflate the apparent flip-rate).

  **If the judge model comes out UNRESOLVED** (step 1 logs a warning and the run
  dir is named `…_model-unknown_…`): the config id wasn't in `/v2/models` *and*
  the spans don't record `gen_ai.request.model` — common, because evaluator
  spans store the judge's input/output but not always the resolved model. Rerun
  step 1 with an explicit slug (find it in the evaluator's model dropdown in
  orq, or `GET /v2/models`):
  ```
  uv run scripts/fetch_evaluator.py --evaluator_id <id> --judge_model mistral-large-latest
  ```
  Without a routable slug, step 4 (stability) cannot re-invoke the judge.

It prints the run directory — **use that `--run_dir` for every later step.** The
folder is created as `<key>_<ts>` and, once the trace scan resolves the judge
model and datapoint count, is renamed to `<key>_<ts>_<model>_<N>dp` so it is
self-describing (re-fetching traces with a wider window updates the `<model>`/
`<N>dp` in place). Always use the **printed** path, not the pre-scan name. If
the evaluator is not boolean the script stops before scanning; relay that V1 is
boolean-only.

**Then offer more data.** Matching is client-side (v3oql has no evaluator
filter), so the scan covers the most recent `--trace_limit` traces (default
**200**). If the user wants more datapoints, or the scan came back empty (a
sparse or aged evaluator can sit beyond the default window — the empty message
echoes the scan window), rerun just the trace fetch with a wider scan, and/or
widen `trace_start_date`/`trace_end_date` in `config.toml`:
```
uv run scripts/fetch_traces.py --run_dir <run_dir> --trace_limit 2000
```
(The evaluator is already saved, so this only re-pulls traces.) To fetch the
evaluator without the auto-scan, pass `--no-with_traces`.

### 2. Confirm experiment setup + workload  ⟵ GATE
Confirm **repetitions N** (default 8), **datapoint count**, and temperature with
the user. **Also confirm the provider:** step 1 resolves the judge *model* from
the traces/eval config correctly, but the *provider* is a known limitation — it
is **not** resolved. Trace resolution writes a **bare** model slug into
`evaluator.json["judge_model"]` (e.g. `gpt-5-mini`, `gpt-oss-120b`), and the
router needs a provider-prefixed slug to route it. There is **no `--model`/
`--provider` flag** — the judge model is read only from that field. So to use the
provider the user names, **edit `<run_dir>/evaluator.json` and set `judge_model`
to the fully-qualified router slug** before step 4, no code change needed. The
router requires the form **`<provider>/openai/<model>`** — a literal `openai/`
segment is **always** required, between the provider and the model, whatever the
provider is: e.g. `groq/openai/gpt-oss-120b`. Show the user the resulting slug and
confirm it's the provider they want to judge with.
Show the projected workload and **wait for explicit go-ahead**:
```
uv run scripts/estimate_cost.py --run_dir <run_dir>
```
This reports the **number of judge calls** and the **input/output token totals**
(no dollar figure — multiply by your judge model's per-Mtoken rate for a cost).

### 3. Run the stability experiment
```
uv run scripts/stability.py --run_dir <run_dir>
```
(Add `--num_samples 2` first for a smoke check.) Writes `stability.json` and
auto-runs metrics.

### 4. Report the flips
metrics.py wrote `metrics.json` with a `flip_report`. **Tell the user how much
the judge is flipping**: overall 1-Flip Consistency, Gwet AC1, how many
datapoints flipped, and where instability concentrates. This is the evidence the
user needs to choose an annotation count.

### 5. Ask how many to annotate  ⟵ GATE
*After* they have seen the flip report, ask how many top-ambiguous datapoints
they want to label (an informed choice, not a fixed number). Mention the
low-flip sanity sample. Then:
```
uv run scripts/build_queue.py --run_dir <run_dir> --count <N>
```

### 6. Annotate
```
uv run scripts/serve_annotation.py --run_dir <run_dir>
```
Tell the user to open the printed URL and label each item **True/False** (the
judge's own verdict space). Labels auto-save to `annotations.json`; they can
stop and resume. Wait for them to say they are done.

### 7. Recommend → aggregate
```
uv run scripts/recommend.py --run_dir <run_dir>
uv run scripts/aggregate.py --run_dir <run_dir>
```
One meta-prompt per annotation (agreements and disagreements alike), then
`aggregated.md` grouping them into changes-to-make and strengths-to-preserve.
Show the user the aggregated changes; they may edit `aggregated.md` before the
rewrite.

### 8. Propose → approve → create  ⟵ GATE
```
uv run scripts/rewrite_eval.py --run_dir <run_dir>
uv run scripts/create_eval.py --run_dir <run_dir>          # presents the diff
```
PO2 rewrites the prompt with a variable-preservation gate (it will not let
`{{...}}` variables change). The second command **presents** the aggregated
recommendations, the old→new diff, and the variable-check status — show this to
the user. **Only after they approve:**
```
uv run scripts/create_eval.py --run_dir <run_dir> --approve
```
(Pass `--edits <file>` to fold in inline human edits.) This creates a NEW boolean
evaluator with `source_evaluator_id` lineage; the original is never touched. If
the user rejects, stop — nothing is created.

### 9. Optional retest — confirm repeats first  ⟵ GATE
Re-judges the annotated datapoints with the new prompt and writes
`experiment_report.md` comparing old vs new agreement with the human labels.

**First get the variance-aware suggestion, then ask the user how many repeats**
(default **N=5**). The suggestion floors at the stability N (so the new-judge
verdict is as trustworthy as the 5-rep `old_judge` it is compared against) and
adds more repeats for datapoints that flipped a lot during the stability run —
high flips warrant higher repeats here too:
```
uv run scripts/run_experiment.py --run_dir <run_dir> --recommend_only
```
Show the user the suggested N and its basis (floor, mean/max flip-rate of the
retested rows), ask them to confirm or override, then run with their choice:
```
uv run scripts/run_experiment.py --run_dir <run_dir> --repeats <N>
```
(Going below the stability N is allowed but warns — the comparison stops being
apples-to-apples.)

## Final summary
When you finish, tell the user plainly:
- what changed in the prompt and why (the aggregated recommendations),
- whether the retest showed better alignment (and on how many items),
- **the blind-spot caveat**: alignment was measured on ambiguous/annotated items
  only; a consistently-wrong judge would not have surfaced. Suggest the low-flip
  sanity sample and periodic re-runs as mitigation.

## Configuration & backends
`config.toml` holds all defaults. The recommend/rewrite **backend** is selectable:
`claude_subagent` (default, shells out to `claude -p`), `anthropic_api`,
`orq_deployment`, or `fake` (tests). See `lib/model_backend.py` for the
nested-template-variable handling that keeps the embedded judge prompt's own
`{{query}}`/`{{output}}` tokens intact.

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
| `build_queue.py` | `--count` (-1 = all), `--low_flip_sample_size` (cfg 5) |
| `serve_annotation.py` | `--port` (8765) |
| `recommend.py` | — |
| `aggregate.py` | — |
| `rewrite_eval.py` | `--max_attempts` (3) |
| `create_eval.py` | `--approve` (False), `--edits <file>` (None), `--force` (False; bypass create-side guards, e.g. non-routable judge slug) |
| `run_experiment.py` | `--repeats` (5, floors up to stability N), `--temperature` (1.0), `--recommend_only` (False) |

## Run directory contract
Every artifact lives in `runs/<key>_<ts>_<model>_<N>dp/`: `evaluator.json`, `traces.jsonl`,
`stability.json`, `metrics.json`, `queue.json`, `annotations.json`,
`recommendations.json`, `aggregated.md`, `new_prompt.md`, `rewrite_status.json`,
`approval.json`, `new_evaluator.json`, `experiment_report.md`. Any step is
re-runnable in isolation against an existing run directory.
