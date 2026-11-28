---
name: orq-find-cost-savings
description: Use when a customer or teammate asks where an orq.ai workspace could save on LLM spend or token cost, wants a cost-savings report from their traces, asks whether a cheaper model or the Smart Router would have handled their traffic, or asks which agents miss prompt caching.
---

# orq find cost savings

## Overview

Estimates what a workspace would have saved over its recent traces from two changes: routing each request through the Smart Router's `jev-general-v1` decision, and turning on prompt caching where multi-turn threads miss it. A script does every calculation from a trace sample and scales it to the window with a confidence interval and coverage; the agent only writes the narrative. Pools and model changes come from the intelligence/price frontier (no pool member is dominated by another), in four scopes: same or any provider, enabled or all available models, so the report lists which disabled models to enable.

Full spec, formulas and field paths: [resources/design.md](resources/design.md).

## When to use

- "Where can we save on AI spend?" for one workspace.
- "Would the Smart Router have been cheaper for our traffic?"
- "Which of our agents should turn on prompt caching?"

Not for: creating a Smart Router (use `orq-cli`), debugging one trace (use `orq-analyze-traces`), billing disputes.

## Prerequisites

- `orq` CLI logged in (`orq status`), on the workspace to analyse.
- Permissions: `traces.list`, `reporting.view`, `classify`.
- `uv`. The script declares its dependencies inline.
- Decision calls use the `orq-ai-sdk` with the credentials the CLI reads with: the CLI login's token (`orq auth token`), else `ORQ_API_KEY`.

## Run

```
uv run <this skill's directory>/scripts/cost_savings_report.py analyze --workspace <key> --out <dir> [--window 30] [--sample N] [--filters '<json>'] [--counterfactual-test] [--yes] [--keep-cache] [--json]
# fill <dir>/narrative.md from <dir>/narrative-slots.md
uv run <this skill's directory>/scripts/cost_savings_report.py render --out <dir> --narrative <dir>/narrative.md [--redact]
```

`--window` is the number of days of traces to analyse, ending now (default 30). `--sample` caps how many traces are drawn (at most 500); without it the cap follows the workspace's plan from `orq workspaces get`: free 15, growth 100, team, pro and custom 500. Traces are drawn in proportion to their cost, in rounds of 25, and drawing stops early once the Smart Router estimate's 90% range is within 25% of the estimate or 1% of spend; `report.json` `sampling` says how many were drawn and why drawing stopped. `--filters` takes a JSON list of `{field, op, values}` (the `orq traces search --filters` grammar, for example one project, one agent or a metadata key) and scopes the whole run to it: the filter is ANDed with every trace search the script makes, and spend, token totals and the cells to draw from come from the matching trace rows instead of telemetry, because telemetry rejects most of those fields and its trace-level metrics measure a different total. A filtered run reads every matching trace in the window, so narrow the filter or `--window` when it stops with check `filtered scan`; a filter the server rejects stops it with check `filters` and the server's message. Calls per trace and the harness table are not available with `--filters`. `report.json` `params.filters` and the page header's Scope line record it. Traces that used more than one model are left out of the draw, and the router estimate covers the rest of the spend; `report.json` `routing.not_analysed` and the page say how much is left out. Change model is priced on all of the window's traffic from token totals (telemetry, or the filtered rows), so it needs no decision calls and has no sampling range; a free plan still gets it. A model without token totals is not covered by either lever and is listed with the reason `no token totals`. `--counterfactual-test` stops after the pilot and lists the recommended and the used model per ask; nothing is replayed. Before the paid decision calls `analyze` prints their count, estimated cost and wait; the count is a hard cap (sampling ends with `sampling.stop_reason` `decision budget` and the report is still written), and the note says sampling usually stops much earlier once the range is tight. Decision calls are paced to 30% of the workspace's rate limit (`subscription.metadata.rateLimit`, else `features.rate_limit`, else 20 a minute, in which case the default sample is capped at 100 and the note says so), and a 429 waits its `Retry-After`. A free plan also has 50 decision calls a day, and the message says how many of the 50 the run uses. Each decision call sends one sampled prompt text to the decision model, and the calls appear as traces in the workspace. Run without a terminal, it stops with check `confirmation` in `report.json`; rerun with `--yes` once the user agrees. Reads are cached under `<dir>/cache/reads` so the rerun after `confirmation` does not fetch them again. They hold prompt texts, so `analyze` deletes them at the end of every run (a declined confirmation and Ctrl-C included) unless `--keep-cache` is given; only the stop at `confirmation` outside a terminal keeps them. Decision probabilities (no prompt text) are kept under `<dir>/cache/decisions` until a run ends ok, so a rerun does not pay for them again. A run that keeps hitting the rate limit stops with check `decision rate limit`; rerun later or with a smaller `--sample`. `report.json` opens with `summary`, which holds every number the page shows: spend, each lever's percent and monthly saving with its 90% range, its coverage (the share of total spend its figure analyses: covered models, single-model traces only; the page shows it under each headline figure) and its split into possible now (enabled models) and after enabling, the recommended lever (null when neither lever saves anything), the routers and replacements to set up (the Smart Router plan also changes models that have no pool to route over, because its figure counts those changes), one recommendation per model and lever (action `router`, `switch`, `keep` or `not_analysed`, router key, targets with their share of calls, models to enable, saving possible now and after enabling, sample size), every scope with each lever's models to enable, and the spend not analysed. `render` only formats these numbers. `--json` prints `status`, `stop` and `summary` (plus `counterfactual_test` on a `--counterfactual-test` run) on stdout and skips the narrative slots: use it when a caller such as an orq agent wants the result without the HTML report. `report.json` stores no ask text. `render --redact` shows each thread id in the caching table as `(hidden)`; use it when the page leaves the workspace's team.

## Workflow

1. Confirm the workspace and output path, and ask which period to analyse: 2 weeks, 1 month (default), 3 months, or another number of days. Pass it as `--window <days>`. A longer period than the workspace keeps traces for is capped at its retention (`orq workspaces get <key>` -> `workspace.features.retention_period`); `report.json` records `requested`, `retention` and the `days` used, so tell the user when it was capped.
2. Run `analyze` without `--yes`. It stops at `confirmation` with the decision cost and wait; tell the user the cost, the wait, and that the sampled prompt texts go to the decision model and show up as traces, then rerun with `--yes` when they agree.
3. If `report.json` has `status: stopped` on any other check, report that check and its numbers; do not work around it. `decision model enabled` means the workspace must enable `typesafe/jev-latest` first.
4. Read `report.json` and write each slot in `narrative-slots.md`: the `summary` slot is one line, at most 12 words, with no model names: the lever named in `summary.recommended` (`router` is Smart Router, `switch` is change model) with its percent of spend and monthly dollars (for example `Save 38% ($150/mo) with Smart Router.`). When `summary.recommended` is null, neither lever saves money: say so instead (for example `No saving found with Smart Router or change model.`). The spend bar shows how much is possible now, the per-model rows carry the names, and the appendix holds coverage and the CI. The `routing` slot is one line per model in plain words (no "dominated", no raw index numbers), with no action list: the page renders the per-model plan. Quote percent of spend first, then monthly dollars (`per_month`), in the other slots, with no statistics in prose, and keep routing and caching savings separate. Call the two levers "Smart Router" and "change model"; never "switch", which reads like a router.
5. Always run `render`: `report.html` is the deliverable, so the task is not done until it exists. Share its path. The one exception is a run with `--json`, where the caller asked for the data only: skip steps 4 and 5 and return `summary`. A `--counterfactual-test` run also has no `summary` or page: skip steps 4 and 5 and report `counterfactual_test` from `report.json`. The `caching` slot is used only when caching saves $1 or more in the threads scanned (not a monthly figure); otherwise `render` does not require it.

## Common mistakes

| Mistake | Fix |
|---|---|
| Quoting dollars without the percent of spend, or a figure without its coverage | Percent first, then dollars per month; the page shows coverage and the CI, so prose repeats them only in the caveats slot |
| Adding routing and caching savings | They overlap; show side by side |
| Adding Smart Router and change-model savings, or two scopes | They are alternatives; quote the headline scope and show the rest beside it |
| Treating the counterfactual as a quality guarantee | It shows which model the router would pick, not that the answer would be as good |
| Estimating savings by hand when `analyze` stops | Report the failing check; the numbers come only from `report.json` |
| Passing `--yes` before the user has seen the decision cost | Run once without it and relay the cost |
| Answering from `report.json` without running `render` | Always render and share `report.html`, unless the caller asked for `--json` |
