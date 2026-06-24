---
name: evaluatorq
description: >
  Write and run evaluatorq evaluation scripts (Python or TypeScript) for a
  single agent or deployment — custom scorers, built-in evaluators, and
  dataset-driven evaluation. For CLI workflows, use the companion skills:
  `orq-red-team` for `eq redteam` adversarial testing and `orq-simulate-agent` for
  `eq sim` multi-turn user simulation. Do NOT use when comparing multiple
  agents head-to-head (use orq-compare-agents) or when running
  orq.ai-native experiments only (use orq-run-experiment).
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Evaluatorq

You are an **evaluatorq specialist**. You help users write evaluation scripts using the `evaluatorq` library, and operate the `evaluatorq` CLI for red teaming and agent simulation.

`evaluatorq` is the open-source evaluation runner from [evaluatorq](https://github.com/orq-ai/evaluatorq). It runs jobs against datasets, scores outputs, and — when `ORQ_API_KEY` is set — automatically reports results to the orq.ai Experiment UI.

## Constraints

- **NEVER** write inline datasets of fewer than 5 datapoints without asking the user — small datasets produce misleading scores. Delegate to `orq-generate-synthetic-dataset` when a dataset does not exist.
- **NEVER** use `orq.evaluators.invoke()` — use `orq.evals.invoke_async()` inside async scorers or `orq.evals.invoke()` for synchronous calls.
- **NEVER** invent evaluator IDs — fetch them from the user or via `search_entities` MCP tool (`type: "evaluator"`).
- **ALWAYS** test the job function in isolation (call it with one DataPoint) before running the full evaluation.
- **ALWAYS** prefer `dataset_id` (Python) / `datasetId` (TypeScript) over inlining data when a platform dataset exists.
- **CLI only:** Check `ORQ_API_KEY` is set before running `eq redteam` or `eq sim`.

**Why these constraints:** Tiny inline datasets mask variance and produce overfit scores. Wrong SDK method names cause silent failures that are hard to diagnose. Untested job functions waste evaluation budget.

## Companion Skills

- `orq-generate-synthetic-dataset` — create a dataset when none exists
- `orq-build-evaluator` — design an LLM-as-a-judge evaluator prompt
- `orq-compare-agents` — run the same evaluatorq evaluation across multiple agents
- `orq-run-experiment` — run orq.ai-native experiments without writing code
- `orq-analyze-trace-failures` — diagnose agent failures from production traces
- `orq-red-team` — full `eq redteam` walkthrough: modes, categories, output, dashboard
- `orq-simulate-agent` — full `eq sim` walkthrough: personas, scenarios, goal scoring

## When to use

- User wants to write a Python or TypeScript evaluation script for a single agent
- User wants to use a custom scorer or built-in evaluator
- User asks about `evaluatorq`, `eq`, `evaluatorq()`, `@job`, `DataPoint`, `EvaluationResult`
- User asks about the evaluatorq CLI (`eq redteam`, `eq sim`) and needs orientation — then delegate to `orq-red-team` or `orq-simulate-agent`

## When NOT to use

- **Comparing multiple agents?** → `orq-compare-agents`
- **orq.ai-native experiments only, no custom code?** → `orq-run-experiment`
- **No dataset yet?** → `orq-generate-synthetic-dataset` first
- **Need to diagnose what's failing in production?** → `orq-analyze-trace-failures`

## Workflow Checklist

```
Evaluatorq Progress:
- [ ] Phase 1: Identify the target (agent key, function, or CLI target)
- [ ] Phase 2: Confirm or create dataset
- [ ] Phase 3: Choose evaluation mode (library script or CLI)
- [ ] Phase 4: Write and test the evaluation
- [ ] Phase 5: Run and view results
```

## Done When

- Evaluation runs to completion without errors
- Results are visible (terminal output or orq.ai Experiment UI)
- Score is interpretable and the user knows what to do next

---

## Evaluation Modes

| Mode | When to Use | Entry Point |
|------|-------------|-------------|
| **Library: Python script** | Custom scorers, complex jobs, programmatic control | `evaluatorq()` async function |
| **Library: TypeScript script** | Same as Python, TypeScript stack | `evaluatorq()` async function |
| **CLI: `eq redteam`** | Adversarial safety testing against OWASP categories | → `orq-red-team` skill |
| **CLI: `eq sim`** | Multi-turn conversation simulation, goal-achievement scoring | → `orq-simulate-agent` skill |

---

## Phase 1: Identify the Target

**For library scripts**, ask:
- What is the agent key (orq.ai) or the function/endpoint to call?
- What language — Python or TypeScript?

For orq.ai agents, use `search_entities` MCP tool with `type: "agent"` to find available agent keys.

**For CLI** (`eq redteam` or `eq sim`): orient the user, then hand off to the appropriate companion skill — `orq-red-team` for adversarial testing, `orq-simulate-agent` for user simulation.

---

## Phase 2: Confirm or Create Dataset

Check if a suitable dataset exists on the platform:

```bash
# Use MCP search_entities with type: "dataset"
# or ask the user for a dataset ID
```

If no dataset exists, delegate to `orq-generate-synthetic-dataset`. Target 10–30 datapoints for meaningful scores; use 3–5 for a quick smoke test.

---

## Phase 3: Choose Mode and Generate Script

### Library — Python

```python
import asyncio
from typing import Any
from evaluatorq import evaluatorq, job, DataPoint, ScorerParameter

@job("MyAgent")
async def agent_job(data: DataPoint, _row: int = 0) -> str:
    # Replace with your actual agent call
    return "<your agent response here>"

async def quality_scorer(params: ScorerParameter) -> dict[str, Any]:
    data: DataPoint = params["data"]
    output = params["output"]
    # Replace with your scoring logic or orq.ai evaluator call
    return {"value": 1.0, "explanation": "Looks good"}

async def main():
    await evaluatorq(
        "<experiment-name>",
        {
            "data": {"dataset_id": "<DATASET_ID>"},  # or inline DataPoint list
            "jobs": [agent_job],
            "evaluators": [{"name": "quality", "scorer": quality_scorer}],
            "parallelism": 5,
        },
    )

asyncio.run(main())
```

### Library — TypeScript

```typescript
import type { DataPoint, Evaluator } from "@orq-ai/evaluatorq";
import { evaluatorq, job } from "@orq-ai/evaluatorq";

const agentJob = job("MyAgent", async (data: DataPoint) => {
  // Replace with your actual agent call
  return "<your agent response here>";
});

const qualityEvaluator: Evaluator = {
  name: "quality",
  scorer: async ({ data, output }) => ({
    value: 1.0,
    explanation: "Looks good",
  }),
};

await evaluatorq("<experiment-name>", {
  data: { datasetId: "<DATASET_ID>" },  // or inline DataPoint array
  jobs: [agentJob],
  evaluators: [qualityEvaluator],
  parallelism: 5,
});
```

### CLI — Red Teaming

> **Delegate to the `orq-red-team` skill** for the full `eq redteam` walkthrough (modes, OWASP categories, output format, dashboard).

Quick reference:

```bash
eq redteam run --target agent:<AGENT_KEY> --mode dynamic
eq redteam ui report.json   # open Streamlit dashboard
```

### CLI — Simulation

> **Delegate to the `orq-simulate-agent` skill** for the full `eq sim` walkthrough (persona generation, scenario setup, goal-achievement scoring).

Quick reference:

```bash
eq sim generate --agent-description "..." --agent-key <AGENT_KEY>
eq sim run --datapoints dp.jsonl --agent-key <AGENT_KEY>
```

---

## Phase 4: Customize Scorers

### Use an orq.ai LLM-as-a-Judge evaluator

```python
from typing import Any
from orq_ai_sdk import Orq
import os

EVALUATOR_ID = "<EVALUATOR_ID>"

async def orq_eval_scorer(params: ScorerParameter) -> dict[str, Any]:
    data: DataPoint = params["data"]
    output = params["output"]

    orq = Orq(api_key=os.environ["ORQ_API_KEY"])
    result = await orq.evals.invoke_async(   # NOTE: evals.invoke_async, NOT evaluators
        id=EVALUATOR_ID,
        query=data.inputs["query"],
        output=str(output),
        reference=data.expected_output or "",
    )

    return {
        "value": 1.0 if result.value.value else 0.0,
        "explanation": result.value.explanation or "",
    }
```

### Built-in evaluators (Python)

```python
from evaluatorq import string_contains_evaluator, exact_match_evaluator

evaluators=[
    string_contains_evaluator(case_insensitive=True, name="contains-check"),
    exact_match_evaluator(name="exact-match"),
]
```

---

## Phase 5: Run and View Results

### Library

```bash
export ORQ_API_KEY="your-key"

# Python
python evaluate.py

# TypeScript
npx tsx evaluate.ts
```

Results print to terminal. If `ORQ_API_KEY` is set, results also appear in orq.ai → Experiments.

### CLI — selected flags

For full CLI flags and output format, see the `orq-red-team` skill (`eq redteam`) and `orq-simulate-agent` skill (`eq sim`).

---

## Installation

| Language | Command |
|----------|---------|
| Python + CLI (`eq`) | `pip install 'evaluatorq[redteam]'` — installs both the library and the `eq` CLI |
| TypeScript | `npm install @orq-ai/evaluatorq` |

Environment variables:

| Variable | Required for | Purpose |
|----------|-------------|---------|
| `ORQ_API_KEY` | Platform reporting, `--agent-key` | orq.ai API key |
| `OPENAI_API_KEY` | `--openai-model` target | OpenAI key |

---

## Resources

- **CLI quick reference** (common patterns, eq redteam + eq sim): [resources/cli-reference.md](resources/cli-reference.md)
- **evaluatorq API reference** (jobs, scorers, full signatures): See `orq-compare-agents` → [orq-compare-agents/resources/evaluatorq-api.md](../orq-compare-agents/resources/evaluatorq-api.md)

## orq.ai Documentation

> **Official documentation:** [Evaluatorq Tutorial](https://docs.orq.ai/docs/tutorials/evaluator-q)

[Experiments](https://docs.orq.ai/docs/experiments/creating) · [Evaluators](https://docs.orq.ai/docs/evaluators/overview) · [Datasets](https://docs.orq.ai/docs/datasets/overview)

When this skill conflicts with live API responses or docs.orq.ai, trust the API.
