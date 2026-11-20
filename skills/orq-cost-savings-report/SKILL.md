---
name: orq-cost-savings-report
description: Use when a customer or teammate asks where an orq.ai workspace could save on LLM spend or token cost, wants a cost-savings report from their traces, asks whether a cheaper model or the Smart Router would have handled their traffic, or asks which agents miss prompt caching. Draft spec; the report script is not built yet.
---

# orq cost savings report

**Status: draft. `scripts/cost_savings_report.py` does not exist yet, so this skill cannot produce a savings report.** Do not estimate savings by hand. What you can do today is show spend by model:

```
orq telemetry query --source TELEMETRY_SOURCE_TRACES --mode scalar \
  --from <rfc3339> --to <rfc3339> --group-by model \
  --compute '[{"metric":"genai.cost","op":"sum"}]' --limit 5000 -o json
```

## Overview

Estimates what a workspace would have saved over its recent traces from two changes: routing each request through the Smart Router's `jev-general-v1` decision, and turning on prompt caching where multi-turn threads miss it. A script does every calculation from a trace sample and scales it to the window with a confidence interval and coverage; the agent only writes the narrative. Customers already on cheap models get an under-powered section instead of routing savings.

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

## Run (once built)

```
uv run <this skill's directory>/scripts/cost_savings_report.py analyze --workspace <key> --out <dir> [--sample 100] [--counterfactual-test]
# fill <dir>/narrative.md from <dir>/narrative-slots.md
uv run <this skill's directory>/scripts/cost_savings_report.py render --out <dir> --narrative <dir>/narrative.md [--redact]
```

`--sample` defaults to 100, also the current maximum. `--counterfactual-test` stops after the pilot and lists the recommended and the used model per ask; nothing is replayed. `analyze` prints a runtime and classify cost estimate and asks before paid calls.

## Workflow

1. Confirm the workspace and output path.
2. Run `analyze`. If it stops on a pilot check, report the failing check from `report.json`; do not work around it.
3. Read `report.json` and write each slot in `narrative-slots.md`: state the headline with its 90% CI and coverage, never alone, and keep routing and caching savings separate.
4. Run `render` and share the HTML.

## Common mistakes

| Mistake | Fix |
|---|---|
| Quoting a savings figure without its CI and coverage | Always give all three |
| Adding routing and caching savings | They overlap; show side by side |
| Treating the counterfactual as a quality guarantee | It shows which model the router would pick, not that the answer would be as good |
| Estimating savings by hand while the script is missing | Show spend by model and say the report is not available yet |
