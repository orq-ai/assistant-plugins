# Evaluator Alignment skill (RES-930)

A standalone, human-in-the-loop Claude Code skill that realigns an existing
**binary LLM-judge evaluator** to human judgment. Given an orq evaluator id and
its production traces, it measures the judge's self-consistency, surfaces the
most ambiguous datapoints for human annotation, turns those labels into a
rewritten judge prompt (via PO2), and — only after the human approves — creates
a new evaluator. See [`SKILL.md`](SKILL.md) for the conductor flow.

Sits alongside `orq-build-evaluator` and `orq-optimize-prompt` in the orq skills
family. Every step script is self-contained via PEP 723 inline dependencies, so
`uv run scripts/<name>.py` builds its own environment — no repo or `uv sync`
required.

## Pipeline at a glance

| Step | Script | Reads | Writes |
|---|---|---|---|
| 1 | `fetch_evaluator.py` | evaluator id | `evaluator.json` (auto-chains `fetch_traces`) |
| 2 | `fetch_traces.py` | `evaluator.json` | `traces.jsonl` (rerun for more/wider scan) |
| 3 | `estimate_cost.py` | `traces.jsonl` | _(prints call + token projection; gate)_ |
| 4 | `stability.py` | `traces.jsonl`, `evaluator.json` | `stability.json` |
| 5 | `metrics.py` | `stability.json` | `metrics.json` |
| 6 | `build_queue.py` | `metrics.json` | `queue.json` |
| 7 | `serve_annotation.py` | `queue.json` | `annotations.json` |
| 8a | `recommend.py` | `annotations.json`, `stability.json`, `evaluator.json` | `recommendations.json` |
| 8b | `aggregate.py` | `recommendations.json` | `aggregated.md` |
| 9a | `rewrite_eval.py` | `aggregated.md`, `evaluator.json` | `new_prompt.md`, `rewrite_status.json` |
| 9b | `create_eval.py` | `new_prompt.md`, `evaluator.json` | `approval.json`, `new_evaluator.json` |
| 10 | `run_experiment.py` | `new_prompt.md`, `annotations.json` | `experiment_report.md` |

Every artifact lives in one run directory `runs/<key>_<ts>/`; any step is
re-runnable in isolation against an existing run directory.

## Quick start

```bash
cd skills/orq-evaluator-alignment

uv run scripts/fetch_evaluator.py --evaluator_id <24-hex-id>   # fetches eval + 200 traces; prints the run dir
RUN=runs/<key>_<ts>
uv run scripts/fetch_traces.py     --run_dir $RUN --trace_limit 2000   # optional: pull more / wider scan
uv run scripts/estimate_cost.py    --run_dir $RUN              # cost gate
uv run scripts/stability.py        --run_dir $RUN --num_samples 2   # smoke
uv run scripts/stability.py        --run_dir $RUN              # full run (+metrics)
uv run scripts/build_queue.py      --run_dir $RUN --count 25
uv run scripts/serve_annotation.py --run_dir $RUN             # open the printed URL
uv run scripts/recommend.py        --run_dir $RUN
uv run scripts/aggregate.py        --run_dir $RUN
uv run scripts/rewrite_eval.py     --run_dir $RUN
uv run scripts/create_eval.py      --run_dir $RUN             # presents the diff
uv run scripts/create_eval.py      --run_dir $RUN --approve   # after human OK
uv run scripts/run_experiment.py   --run_dir $RUN --recommend_only  # suggest N
uv run scripts/run_experiment.py   --run_dir $RUN --repeats 5    # optional retest (confirm N)
```

`config.toml` holds all defaults (repetitions, temperature, backend, sample
sizes). CLI flags override per run.

## Backends (recommend + rewrite)

`config.backend` selects how steps 8/9 call a model:

- `claude_subagent` (default) — shells out to `claude -p ... --output-format json`;
  per-call cost from the CLI's `total_cost_usd`.
- `anthropic_api` — direct Anthropic Messages API.
- `orq_deployment` — a workspace deployment via the orq SDK.
- `fake` — deterministic canned completions (tests only).

The meta-prompt and PO2 prompt embed the audited judge prompt, which carries its
own `{{query}}`/`{{output}}` tokens. The string backends keep those literal;
`orq_deployment` self-references them so the templating engine renders them
unchanged. See `lib/model_backend.py`.

## Tests

```bash
cd skills/orq-evaluator-alignment
uv run pytest tests/test_pipeline.py -q -p no:langsmith_plugin
```

> **Windows note.** The `-p no:langsmith_plugin` flag is required here: the
> autoloaded langsmith pytest plugin imports the SSL stack, which aborts the
> process on this host (the OpenSSL Applink crash, project memory). Disabling
> that one plugin avoids it. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` also works.

The test runs the full pipeline (stability → metrics → build_queue →
annotation-load → recommend → aggregate → rewrite) on a 3-row synthetic fixture
with the judge monkeypatched and the `fake` backend — no network.

## V1 scope & limitations

- **Boolean Pass/Fail judges only.** Step 1 fails fast otherwise.
- **Self-consistency ≠ validity.** Flip-rate localises where the judge is
  unstable; it cannot prove the judge is correct.
- **Consistently-wrong blind spot.** Flip-ranking never surfaces items the judge
  gets wrong *consistently* (flip-rate ≈ 0). The `low_flip_sample_size` config
  adds a random low-flip sanity sample as the cheap mitigation, and every final
  report states the limitation.
- **Local annotation store.** Annotations persist to disk (ADR-14 `human_review`
  shape). orq-native human-review-column persistence lands with RES-843.
- **Step 10 is a retest, not a scheduler.** The resumable run directory is the
  hook a future cadence would re-enter.
