---
name: orq-evaluator-alignment
description: >-
  Align, calibrate, or improve an existing LLM-as-a-judge (orq evaluator) so its
  verdicts match human judgment — boolean, categorical, or numeric judges. Use
  when the user wants to "align my evaluator", "improve my eval", "annotate an
  evaluator", "find ambiguous cases", or "build an annotation queue" — i.e. they
  have an LLM judge that disagrees with human labels or is inconsistent. Measures
  judge self-consistency as one 0..1 instability score (boolean flip-rate,
  categorical label entropy, or numeric score spread) via repeated runs, surfaces
  the most unstable datapoints for human annotation, rewrites the judge prompt
  from the labels, and creates the new evaluator only after the human approves. If
  the evaluator ID isn't given, ask for it after triggering. Do NOT use to build
  an evaluator from scratch (use orq-build-evaluator) or to fix failures with
  prompt tweaks (use orq-optimize-prompt).
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(uv run:*), AskUserQuestion
---

# Evaluator Alignment

You are the **conductor** of a human-in-the-loop pipeline (RES-930) that rewrites
an LLM-judge evaluator to better match human judgment. You run small,
independently-runnable scripts under `scripts/`; each writes one artifact into a
per-run working directory (`runs/<key>_<ts>_<model>_<N>dp/`). **The human stays in control of
every consequential action** — the prompt rewrite, creating the new evaluator,
and any retest. Never skip a gate.

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

- **Fully multi-type, end to end.** Every step — measure (stability → instability
  metrics → confuser ranking) and improve (score → recommend → rewrite → create →
  retest) — supports **boolean, categorical, and numeric** judges (RES-978 Part 1
  + Part 2 / RES-980). Instability is one 0..1 scale (boolean flip-rate,
  categorical label entropy, numeric score spread); the rewrite preserves the
  evaluator's verdict space (label set / numeric scale). Numeric rewriting is
  deliberately shallow — it nudges the scale's anchor descriptions, not a
  calibration model. Step 1 accepts all three output types and fails fast on
  anything else.
- **Self-consistency is a ceiling, not proof.** Low instability means the judge
  is *stable*, not *correct* — a judge can be consistently wrong. You surface
  ambiguity; the human supplies truth.
- **Known blind spot:** instability-ranking never surfaces consistently-wrong
  items (instability ≈ 0). You MUST state this in your final summary, and you
  offer the low-instability sanity sample (config `low_flip_sample_size`) as the
  cheap mitigation.

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
  mixed-model history can inflate the apparent instability).

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
the evaluator's output type isn't boolean, categorical, or number, the script
stops before scanning; relay which three types are supported.

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
router requires the form **`<provider>/<model>`** — a single provider prefix, then
the model: e.g. `anthropic/claude-haiku-4-5`, `google/gemini-2.5-flash`,
`groq/gpt-oss-120b` (the same form the agent config and the MCP `create_llm_eval`
tool accept). In slugs like `openai/gpt-oss-120b` the `openai/` is the *provider*,
not a fixed segment — do **not** insert a literal `openai/` between the provider and
the model; `anthropic/openai/claude-haiku-4-5` returns a 404. Show the user the
resulting slug and confirm it's the provider they want to judge with.
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

### 4. Report the instability
metrics.py wrote `metrics.json` with a lean `report`. **Tell the user how
self-consistent the judge is** on the unified 0..1 instability scale (boolean
flip-rate, categorical label entropy, or numeric score spread): the mean
instability, the band histogram (stable / noisy / unreliable), and the
most-unstable datapoints with their type-native detail. (For boolean judges the
heavier agreement stats — 1-Flip Consistency, Gwet AC1, Fleiss κ — are computed
too and surfaced on request.) This is the evidence the user needs to choose an
annotation count.

### 5. Ask how many to annotate  ⟵ GATE
*After* they have seen the instability report, ask how many top-ambiguous datapoints
they want to label (an informed choice, not a fixed number). Mention the
low-flip sanity sample. Then:
```
uv run scripts/build_queue.py --run_dir <run_dir> --count <N>
```

### 6. Annotate
```
uv run scripts/serve_annotation.py --run_dir <run_dir>
```
Tell the user to open the printed URL and **score each item with its type-native
input** — boolean → Pass/Fail; categorical → one button per declared label;
numeric → a number on the evaluator's scale — plus an optional one-line "why"
(the cheapest lever for a good rewrite). Labels auto-save to `annotations.json`
as typed values; they can stop and resume. Wait for them to say they are done.

### 7. Recommend → aggregate
```
uv run scripts/recommend.py --run_dir <run_dir>
uv run scripts/aggregate.py --run_dir <run_dir>
```
Compares each human label to the judge's central verdict, **type-aware** (boolean
flips / categorical confusion-pairs / numeric signed-errors), then `aggregated.md`
groups them into changes-to-make and strengths-to-preserve. Show the user the
aggregated changes; they may edit `aggregated.md` before the rewrite.

### 8. Propose → approve → create  ⟵ GATE
```
uv run scripts/rewrite_eval.py --run_dir <run_dir>
uv run scripts/create_eval.py --run_dir <run_dir>          # presents the diff
```
The single multi-type meta prompt rewrites the rubric with a **preservation gate**
— it will not let `{{...}}` variables change, drop a categorical label, or move
the numeric scale. The second command **presents** the aggregated recommendations,
the old→new diff, and the preservation-check status — show this to the user.
**Only after they approve:**
```
uv run scripts/create_eval.py --run_dir <run_dir> --approve
```
(Pass `--edits <file>` to fold in inline human edits.) This creates a NEW evaluator
**of the same output type** (boolean / categorical with its full label set /
numeric) with `source_evaluator_id` lineage; the original is never touched. If the
user rejects, stop — nothing is created.

### 9. Optional retest — confirm repeats first  ⟵ GATE
Re-judges the scored datapoints with the NEW evaluator and writes
`retest_metrics.json`. **Success needs both**: instability **drops** on the
confusers, AND the new evaluator's verdicts **agree with the human labels**
(boolean TPR/TNR · categorical accuracy · numeric MAE / within-tolerance). A drop
alone is gameable by a consistently-wrong-but-stable judge — agreement keeps it
honest.

**Confirm the repeat count with the user first** (default from `config.toml`; use
at least the stability N so the new verdict is as trustworthy as the run it is
compared against):
```
uv run scripts/retest.py --run_dir <run_dir> --n_repeats <N>
```
(`--tol` sets the numeric within-tolerance band, default 0.5 on the raw scale;
`--num_samples` limits how many confusers to retest. Going below the stability N
is allowed but the comparison stops being apples-to-apples.)

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
| `retest.py` | `--n_repeats` (cfg, use ≥ stability N), `--num_samples` (cfg, confusers to retest), `--tol` (0.5, numeric within-tolerance band) |

## Run directory contract
Every artifact lives in `runs/<key>_<ts>_<model>_<N>dp/`: `evaluator.json` (with
`output_type` + `categorical_labels`/`scale`), `traces.jsonl`, `stability.json`,
`metrics.json`, `queue.json` (each confuser carries its `verdict_space`),
`annotations.json` (typed values), `recommendations.json`, `aggregated.md` +
`aggregated.json`, `new_prompt.md`, `rewrite_status.json`, `approval.json`,
`new_evaluator.json`, `retest_metrics.json`. Any step is re-runnable in isolation
against an existing run directory.

## Companion Skills

- **orq-cli** — the same platform operations from a shell, for anything that must run again without an agent present (CI, cron, scripts, bulk): auth via `ORQ_API_KEY`, `--json` output. See its "MCP tools or the CLI?" table before choosing.
