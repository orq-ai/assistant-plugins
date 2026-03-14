---
description: Show a workspace overview — agents, deployments, prompts, datasets, and experiments
argument-hint: [section]
allowed-tools: Bash, AskUserQuestion, orq*
---

# Workspace

Show a quick overview of the user's orq.ai workspace. Optionally filter to a specific section.

## Instructions

### 1. Validate environment

Check that `$ORQ_API_KEY` is set:

```bash
if [ -z "$ORQ_API_KEY" ]; then echo "ERROR: ORQ_API_KEY is not set"; exit 1; fi
```

If missing, tell the user to set it and stop.

### 2. Parse arguments

`$ARGUMENTS` is optional. If provided, it narrows the output to a specific section:
- `agents` — show only agents
- `deployments` — show only deployments
- `prompts` — show only prompts
- `datasets` — show only datasets
- `experiments` — show only experiments

If empty, show all sections.

### 3. Fetch data

Try MCP tools first, fall back to `curl` if unavailable. Fetch only the sections needed based on arguments.

**Agents:**
```bash
curl -s -H "Authorization: Bearer $ORQ_API_KEY" "https://api.orq.ai/v2/agents"
```

**Deployments:**
```bash
curl -s -H "Authorization: Bearer $ORQ_API_KEY" "https://api.orq.ai/v2/deployments"
```

**Prompts:**
```bash
curl -s -H "Authorization: Bearer $ORQ_API_KEY" "https://api.orq.ai/v2/prompts"
```

**Datasets:**
```bash
curl -s -H "Authorization: Bearer $ORQ_API_KEY" "https://api.orq.ai/v2/datasets"
```

**Experiments:**
```bash
curl -s -H "Authorization: Bearer $ORQ_API_KEY" "https://api.orq.ai/v2/experiments"
```

Run all applicable requests in parallel using multiple Bash calls.

### 4. Display the overview

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

### 5. Error handling

- **401/403** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **Network error** — "Could not reach the orq.ai API. Check your internet connection."
- **Partial failure** — If some endpoints fail but others succeed, show what you have and note which sections failed.
