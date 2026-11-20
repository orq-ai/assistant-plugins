---
name: orq-cost-savings-report
description: Use when a customer or teammate asks where an orq.ai workspace could save on LLM spend or token cost, wants a cost-savings report from their traces, asks whether a cheaper model or the Smart Router would have handled their traffic, or asks which agents miss prompt caching.
---

# orq cost savings report

## Overview

Estimates what a workspace would have saved over its recent traces from two changes: routing each request through the Smart Router's `jev-general-v1` decision, and turning on prompt caching where multi-turn threads miss it. A script does every calculation from a trace sample and scales it to the window with a confidence interval and coverage; the agent only writes the narrative. Pools and model switches come from the intelligence/price frontier (no pool member is dominated by another), in four scopes: same or any provider, enabled or all available models, so the report lists which disabled models to enable. Models where most asks are hard get an under-powered section.

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
uv run <this skill's directory>/scripts/cost_savings_report.py analyze --workspace <key> --out <dir> [--sample 100] [--counterfactual-test] [--yes]
# fill <dir>/narrative.md from <dir>/narrative-slots.md
uv run <this skill's directory>/scripts/cost_savings_report.py render --out <dir> --narrative <dir>/narrative.md [--redact]
```

`--sample` defaults to 100, also the current maximum. `--counterfactual-test` stops after the pilot and lists the recommended and the used model per ask; nothing is replayed. Before the paid decision calls `analyze` prints their count and estimated cost. Run without a terminal, it stops with check `confirmation` in `report.json`; rerun with `--yes` once the user agrees. Reads are cached under `<dir>/cache`, so the rerun does not fetch them again.

## Workflow

1. Confirm the workspace and output path.
2. Run `analyze` without `--yes`. It stops at `confirmation` with the decision cost; tell the user the cost and rerun with `--yes` when they agree.
3. If `report.json` has `status: stopped` on any other check, report that check and its numbers; do not work around it. `decision model enabled` means the workspace must enable `typesafe/jev-latest` first.
4. Read `report.json` and write each slot in `narrative-slots.md`: state the headline with its 90% CI and coverage, never alone, and keep routing and caching savings separate.
5. Run `render` and share the HTML.

## Common mistakes

| Mistake | Fix |
|---|---|
| Quoting a savings figure without its CI and coverage | Always give all three |
| Adding routing and caching savings | They overlap; show side by side |
| Adding router and switch savings, or two scopes | They are alternatives; quote the headline scope and show the rest beside it |
| Treating the counterfactual as a quality guarantee | It shows which model the router would pick, not that the answer would be as good |
| Estimating savings by hand when `analyze` stops | Report the failing check; the numbers come only from `report.json` |
| Passing `--yes` before the user has seen the decision cost | Run once without it and relay the cost |
