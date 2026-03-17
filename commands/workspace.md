---
description: Show a workspace overview — agents, deployments, prompts, datasets, and experiments
argument-hint: [section]
allowed-tools: AskUserQuestion, orq*
---

# Workspace

Show a quick overview of the user's orq.ai workspace. Optionally filter to a specific section.

## Instructions

### 1. Parse arguments

`$ARGUMENTS` is optional. If provided, it narrows the output to a specific section:
- `agents` — show only agents
- `deployments` — show only deployments
- `prompts` — show only prompts
- `datasets` — show only datasets
- `experiments` — show only experiments

If empty, show all sections.

### 2. Fetch data

Use the `search_entities` MCP tool to fetch each section. Run all applicable calls in parallel.

- **Agents:** `search_entities` with `type: "agent"`
- **Deployments:** `search_entities` with `type: "deployment"`
- **Prompts:** `search_entities` with `type: "prompt"`
- **Datasets:** `search_entities` with `type: "dataset"`
- **Experiments:** `search_entities` with `type: "experiment"`

Fetch only the sections needed based on arguments.

### 3. Display the overview

Present a clean summary, not raw JSON. Format as a workspace dashboard:

```
Workspace Overview
==================

Agents (3)
  - customer-support-bot (gpt-4o) — 2 tools, 1 KB
  - onboarding-agent (claude-sonnet) — 3 tools
  - internal-qa (gpt-4o-mini)

Deployments (5)
  - summarizer v2.1 — active
  - classifier v1.0 — active
  ...

Prompts (8)
  - support-response — 4 versions, latest: v4
  - extract-entities — 2 versions, latest: v2
  ...

Datasets (2)
  - eval-customer-queries — 150 datapoints
  - edge-cases — 42 datapoints

Experiments (1)
  - prompt-comparison-mar — completed, 3 evaluators
```

Adapt the fields based on what the API actually returns. Show the most useful attributes: name, model, version count, status, size. Keep it scannable.

- If a section is empty, show `(none)` instead of omitting it.
- Cap each section at 10 items. If there are more, show the count and say "... and N more".

### 4. Error handling

- **Auth errors** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **MCP errors** — "Could not reach the orq.ai MCP server. Make sure it's configured: `claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header 'Authorization: Bearer ${ORQ_API_KEY}'`"
- **Partial failure** — If some calls fail but others succeed, show what you have and note which sections failed.
