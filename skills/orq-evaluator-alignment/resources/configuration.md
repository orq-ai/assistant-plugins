# Configuration, backends and parameter reference

## Configuration & backends
`config.toml` holds all defaults, including the step-8 gates:

| Key | Default | What it does |
|---|---|---|
| `retest_min_accuracy` | 0.7 | boolean + categorical: minimum exact-match rate for gate (b) |
| `retest_min_tpr` / `retest_min_tnr` | 0.7 | boolean only; each is skipped when the labels hold no positives / no negatives |
| `retest_min_within_tol` | 0.7 | numeric: minimum share of points inside the band |
| `numeric_tol` | blank → derived | the numeric agreement band in the judge's own units. **One key**, shared by the retest gate, the recommend step's disagreement extraction, and the cross-model probe |
| `numeric_tol_fraction` | 0.1 | fraction of the declared scale range used when `numeric_tol` is blank; falls back to an absolute 0.5 when the evaluator declares no scale — `retest.py` refuses before judging instead of silently using that fallback (`cross_model.py`'s probe still falls back to it) |
| `panel_models` | `[]` | extra provider-qualified model slugs; the audited judge is always first, so two extras make a three-model panel. `--panel_models` overrides this list for estimation and stability. |
| `panel_repeats` | 3 | repetitions per model when the panel is nonempty; `--n_repeats` overrides it. A one-model run still uses `n_repeats` (8). |

Leave `numeric_tol` blank unless you have a reason not to. An absolute band means
something different on every judge — 0.5 is half of a 0–1 groundedness scale and
0.5% of a 0–100 one — which is the same argument that makes instability normalize by
the declared range. A grey-zone policy that bands each point individually overrides
both: those per-point bands are scored as given, and these values only cover points
without one.

The model that writes the recommendation (step 6/8) and the rewritten prompt (step 7)
is selectable via `backend`:

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
| `fetch_evaluator.py` | `--evaluator_id` (req/config), `--with_traces` (**False** — the input source is step 1a's question; pass it to scan in the same command), `--trace_limit` (200, with `--with_traces`), `--judge_model` (slug override when the config id can't be resolved), `--scale_min` / `--scale_max` (numeric evaluators only; override-only, both-or-neither — leave blank and numeric rows report `unmeasurable` until set) |
| `fetch_traces.py` | `--trace_limit` (200), `--replace` (False — required to overwrite rows another input source appended, since the scan rewrites `traces.jsonl` wholesale), `--force` / `--dedup` |
| `estimate_cost.py` | `--panel_models` (extra comma-separated slugs, cfg `[]`), `--n_repeats` (cfg 8 with one model; `panel_repeats` 3 with a panel), `--num_samples` (cfg -1 = all) |
| `stability.py` | `--panel_models` (same list as the cost gate), `--num_samples` (cfg -1), `--n_repeats` (cfg 8 with one model; 3 with a panel), `--max_concurrency` (cfg 8), `--temperature` (cfg 1), `--metrics` (True; skip with `--metrics False` — **not** `--no-metrics`, which fire rejects *after* the run completes, making a finished run look failed), `--include_degraded` (False — keep degraded/hollow rows instead of skipping them) |
| `metrics.py` | — (run_dir/config only) |
| `build_queue.py` | `--count` (-1 = all), `--low_flip_sample_size` (cfg 5). Also projects the step-6 context budget into `queue.json` `meta.grey_zone_projection` |
| `dataset_inputs.py list` | `--limit` (100) |
| `dataset_inputs.py pull` | `--dataset_id` (req), `--limit` (200), `--map "<var>=<source>"` (repeatable; sources: `inputs.<key>`, `messages.<role>.last\|first`, `messages.all`, `expected_output`) |
| `seed_inputs.py convert` | — (run_dir/config only) |
| `seed_inputs.py save` | `--dataset_name` (new) OR `--dataset_id` (append) |
| `cross_model.py` | `--model` (req; 2nd judge slug), `--num_samples`, `--n_repeats`, `--tol` (resolves `numeric_tol` → `numeric_tol_fraction` × the declared scale → 0.5 fallback; no grey-zone policy exists yet at this stage) |
| `grey_zone.py assemble` | `--top_k` (cfg `grey_zone_top_k`; -1 = all, 0 = none), `--max_chars` (cfg `grey_zone_max_chars`; 600 — per-example input budget, fair-shared across `{{variables}}`), `--max_tokens` (cfg `grey_zone_max_tokens`; 60000 — payload ceiling, clamps to the fitting prefix), `--include_low_flip` (False — bring the stable spot-check rows into the payload too) |
| `grey_zone.py apply` | — (run_dir/config only) |
| `serve_annotation.py` | `--port` (8765) — the interactive UI fallback |
| `recommend.py` | `--backend`, `--backend_model`, `--backend_base_url` (each: flag → config → env → default) |
| `aggregate.py` | — |
| `rewrite_eval.py` | `--max_attempts` (3), `--backend`, `--backend_model`, `--backend_base_url` |
| `create_eval.py` | `--approve` (False), `--edits <file>` (None), `--force` (False; bypasses the variable-set AND verdict-space/preservation checks — unsafe; there is no judge-slug guard) |
| `retest.py` | `--n_repeats` / `--temperature` (default: whatever the step-3 run used — a lower `--n_repeats` or a different `--temperature` marks the comparison not comparable), `--num_samples` (cap rows, smoke test — narrows both sides of the comparison), `--tol` (numeric within-tolerance band; resolves policy band → `numeric_tol` → `numeric_tol_fraction` × scale; refuses before any judging if none of those and no declared scale, see Configuration), `--all_rows` (False — by default only the **labelled** rows are re-judged), `--with_dataset_labels` (False — also re-judge rows whose only label is `dataset_reference`), `--with_low_flip` (False — also re-judge the stable spot-check rows as a regression check), `--baseline_rerun` (False — re-run the OLD judge over the same rows for a true A/B; adds one-model calls, so doubles cost only without a panel) |
