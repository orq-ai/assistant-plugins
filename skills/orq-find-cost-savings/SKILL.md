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

## Run

```
uv run <this skill's directory>/scripts/cost_savings_report.py analyze --workspace <key> --out <dir> [--window 30] [--sample 100] [--counterfactual-test] [--yes] [--json]
# fill <dir>/narrative.md from <dir>/narrative-slots.md
uv run <this skill's directory>/scripts/cost_savings_report.py render --out <dir> --narrative <dir>/narrative.md [--redact]
```

`--window` is the number of days of traces to analyse, ending now (default 30). `--sample` defaults to 100, also the current maximum. `--counterfactual-test` stops after the pilot and lists the recommended and the used model per ask; nothing is replayed. Before the paid decision calls `analyze` prints their count and estimated cost. Run without a terminal, it stops with check `confirmation` in `report.json`; rerun with `--yes` once the user agrees. Reads are cached under `<dir>/cache`, so the rerun does not fetch them again. `report.json` opens with `summary`, which holds every number the page shows: spend, each lever's percent and monthly saving with its 90% range and its split into today and after enabling, the recommended lever, the routers or replacements to set up, one recommendation per model and lever (action, router key, targets with their share of calls, models to enable, saving today and after enabling, sample size), every scope, and the spend not analysed. `render` only formats these numbers. `--json` prints `status`, `stop` and `summary` (plus `counterfactual_test` on a `--counterfactual-test` run) on stdout and skips the narrative slots: use it when a caller such as an orq agent wants the result without the HTML report.

## Workflow

1. Confirm the workspace and output path, and ask which period to analyse: 2 weeks, 1 month (default), 3 months, or another number of days. Pass it as `--window <days>`. A longer period than the workspace keeps traces for is capped at its retention (`orq workspaces get <key>` -> `workspace.features.retention_period`); `report.json` records `requested`, `retention` and the `days` used, so tell the user when it was capped.
2. Run `analyze` without `--yes`. It stops at `confirmation` with the decision cost; tell the user the cost and rerun with `--yes` when they agree.
3. If `report.json` has `status: stopped` on any other check, report that check and its numbers; do not work around it. `decision model enabled` means the workspace must enable `typesafe/jev-latest` first.
4. Read `report.json` and write each slot in `narrative-slots.md`: the `summary` slot is one line, at most 12 words, with no model names: the lever named in `summary.recommended` (`router` is Smart Router, `switch` is change model) with its percent of spend and monthly dollars (for example `Save 38% ($150/mo) with Smart Router.`); the spend bar shows how much is possible today, the per-model rows carry the names, and the appendix holds coverage and the CI. The `routing` slot is one line per model in plain words (no "dominated", no raw index numbers), with no action list: the page renders the per-model plan. Quote percent of spend first, then monthly dollars (`per_month`), in the other slots, with no statistics in prose, and keep routing and caching savings separate. Call the two levers "Smart Router" and "change model"; never "switch", which reads like a router.
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
