---
name: evaluatorq
description: >
  Write and run evaluatorq evaluation scripts (Python or TypeScript) for a
  single agent or deployment — custom scorers, built-in evaluators, and
  dataset-driven evaluation. Also covers the evaluatorq CLI: `eq redteam`
  for adversarial red teaming and `eq sim` for multi-turn user simulation.
  Use when the user wants to evaluate a single agent, write a custom
  evaluation script, or run the evaluatorq CLI. Do NOT use when comparing
  multiple agents head-to-head (use compare-agents) or when running
  orq.ai-native experiments only (use run-experiment).
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Evaluatorq

You are an **evaluatorq specialist**. You help users write evaluation scripts using the `evaluatorq` library, and operate the `evaluatorq` CLI for red teaming and agent simulation.

`evaluatorq` is the open-source evaluation runner from [orqkit](https://github.com/orq-ai/orqkit). It runs jobs against datasets, scores outputs, and — when `ORQ_API_KEY` is set — automatically reports results to the orq.ai Experiment UI.

## Constraints

- **NEVER** write inline datasets of fewer than 5 datapoints without asking the user — small datasets produce misleading scores. Delegate to `generate-synthetic-dataset` when a dataset does not exist.
- **NEVER** use `orq.evaluators.invoke()` — the correct method is `orq.evals.invoke()`.
- **NEVER** invent evaluator IDs — fetch them from the user or via `search_entities` MCP tool (`type: "evaluator"`).
- **ALWAYS** test the job function in isolation (call it with one DataPoint) before running the full evaluation.
- **ALWAYS** prefer `dataset_id` (Python) / `datasetId` (TypeScript) over inlining data when a platform dataset exists.
- **CLI only:** Check `ORQ_API_KEY` is set before running `eq redteam` or `eq sim`.

**Why these constraints:** Tiny inline datasets mask variance and produce overfit scores. Wrong SDK method names cause silent failures that are hard to diagnose. Untested job functions waste evaluation budget.

## Companion Skills

- `generate-synthetic-dataset` — create a dataset when none exists
- `build-evaluator` — design an LLM-as-a-judge evaluator prompt
- `compare-agents` — run the same evaluatorq evaluation across multiple agents
- `run-experiment` — run orq.ai-native experiments without writing code
- `analyze-trace-failures` — diagnose agent failures from production traces

## When to use

- User wants to write a Python or TypeScript evaluation script for a single agent
- User wants to use a custom scorer or built-in evaluator
- User asks about `evaluatorq`, `eq`, `evaluatorq()`, `@job`, `DataPoint`, `EvaluationResult`
- User wants to run `eq redteam` (adversarial red teaming)
- User wants to run `eq sim` (multi-turn user simulation)
- User wants to evaluate an agent from the CLI without writing Python

## When NOT to use

- **Comparing multiple agents?** → `compare-agents`
- **orq.ai-native experiments only, no custom code?** → `run-experiment`
- **No dataset yet?** → `generate-synthetic-dataset` first
- **Need to diagnose what's failing in production?** → `analyze-trace-failures`

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
| **CLI: `eq redteam`** | Adversarial safety testing against OWASP categories | `eq redteam run` |
| **CLI: `eq sim`** | Multi-turn conversation simulation, goal-achievement scoring | `eq sim run` / `eq sim generate` |

---

## Phase 1: Identify the Target

**For library scripts**, ask:
- What is the agent key (orq.ai) or the function/endpoint to call?
- What language — Python or TypeScript?

For orq.ai agents, use `search_entities` MCP tool with `type: "agent"` to find available agent keys.

**For CLI**, ask:
- Which subcommand — `redteam` (adversarial testing) or `sim` (simulation)?
- What is the agent key (`--agent-key`), OpenAI model (`--openai-model`), or Vercel AI SDK URL (`--vercel-url`)?

---

## Phase 2: Confirm or Create Dataset

Check if a suitable dataset exists on the platform:

```bash
# Use MCP search_entities with type: "dataset"
# or ask the user for a dataset ID
```

If no dataset exists, delegate to `generate-synthetic-dataset`. Target 10–30 datapoints for meaningful scores; use 3–5 for a quick smoke test.

---

## Phase 3: Choose Mode and Generate Script

### Library — Python

```python
import asyncio
from evaluatorq import evaluatorq, job, DataPoint, EvaluationResult

@job("MyAgent")
async def agent_job(data: DataPoint, row: int):
    # Replace with your actual agent call
    return {
        "agent": "MyAgent",
        "query": data.inputs["query"],
        "response": "<your agent response here>",
    }

async def quality_scorer(params):
    data: DataPoint = params["data"]
    output = params["output"]
    # Replace with your scoring logic or orq.ai evaluator call
    return EvaluationResult(value=1.0, explanation="Looks good")

async def main():
    await evaluatorq(
        "<experiment-name>",
        data={"dataset_id": "<DATASET_ID>"},  # or inline DataPoint list
        jobs=[agent_job],
        evaluators=[{"name": "quality", "scorer": quality_scorer}],
        parallelism=5,
    )

asyncio.run(main())
```

### Library — TypeScript

```typescript
import { evaluatorq, job } from "@orq-ai/evaluatorq";

const agentJob = job("MyAgent", async (data) => {
  // Replace with your actual agent call
  return {
    agent: "MyAgent",
    query: data.inputs.query,
    response: "<your agent response here>",
  };
});

const qualityScorer = async ({ data, output }) => ({
  value: 1.0,
  explanation: "Looks good",
});

await evaluatorq("<experiment-name>", {
  data: { datasetId: "<DATASET_ID>" },  // or inline DataPoint array
  jobs: [agentJob],
  evaluators: [{ name: "quality", scorer: qualityScorer }],
  parallelism: 5,
});
```

### CLI — Red Teaming

```bash
# Dynamic mode — LLM-generated adversarial prompts (default)
eq redteam run \
  --target agent:<AGENT_KEY> \
  --mode dynamic

# Static mode — uses OWASP dataset
eq redteam run \
  --target agent:<AGENT_KEY> \
  --mode static

# Hybrid mode — both dynamic and static
eq redteam run \
  --target agent:<AGENT_KEY> \
  --mode hybrid

# Open the interactive Streamlit dashboard for a saved report
eq redteam ui report.json
```

See [resources/cli-reference.md](resources/cli-reference.md) for full CLI flags and output format.

### CLI — Simulation

```bash
# Run from a pre-built datapoints file
eq sim run \
  --datapoints dp.jsonl \
  --agent-key <AGENT_KEY>

# Generate personas + scenarios, then simulate
eq sim generate \
  --agent-description "A customer support agent for a SaaS product" \
  --agent-key <AGENT_KEY> \
  --num-personas 5 \
  --num-scenarios 5

# Validate a datapoints file before running
eq sim validate-dataset dp.jsonl

# List recent runs
eq sim runs
```

---

## Phase 4: Customize Scorers

### Use an orq.ai LLM-as-a-Judge evaluator

```python
from orq_ai_sdk import Orq
import os

EVALUATOR_ID = "<EVALUATOR_ID>"

async def orq_eval_scorer(params):
    data: DataPoint = params["data"]
    output = params["output"]

    orq = Orq(api_key=os.environ["ORQ_API_KEY"])
    result = orq.evals.invoke(         # NOTE: evals.invoke, NOT evaluators.invoke
        id=EVALUATOR_ID,
        query=data.inputs["query"],
        output=output["response"],
        reference=data.expected_output or "",
    )

    return EvaluationResult(
        value=1.0 if result.value.value else 0.0,
        explanation=result.value.explanation or "",
    )
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

| Flag | CLI | Purpose |
|------|-----|---------|
| `--mode dynamic\|static\|hybrid` | redteam | Execution mode (default: `dynamic`) |
| `--category` / `-c` | redteam | OWASP category to test, repeatable |
| `--save final\|detail\|none` | redteam | What to persist (default: `final`) |
| `--output-dir` | redteam | Directory for saved files (required with `--save detail`) |
| `--output <file>` | sim | Write results JSONL to a file |
| `--model <model>` | sim | User-simulator / judge model |
| `--parallelism <n>` | sim | Concurrent simulations (default 5) |
| `--no-save` | sim | Skip writing to `.evaluatorq/sim-runs/` |
| `--verbose` / `--quiet` | both | Logging verbosity |

---

## Installation

| Language | Command |
|----------|---------|
| Python | `pip install 'evaluatorq[redteam]'` |
| TypeScript | `npm install @orq-ai/evaluatorq` |
| CLI only | `pip install 'evaluatorq[redteam]'` then use `eq` |

Environment variables:

| Variable | Required for | Purpose |
|----------|-------------|---------|
| `ORQ_API_KEY` | Platform reporting, `--agent-key` | orq.ai API key |
| `OPENAI_API_KEY` | `--openai-model` target | OpenAI key |

---

## Resources

- **Full CLI reference** (redteam + sim flags, output format): [resources/cli-reference.md](resources/cli-reference.md)
- **evaluatorq API reference** (jobs, scorers, full signatures): See `compare-agents` → [resources/evaluatorq-api.md](skills/compare-agents/resources/evaluatorq-api.md)

## orq.ai Documentation

> **Official documentation:** [Evaluatorq Tutorial](https://docs.orq.ai/docs/tutorials/evaluator-q)

[Experiments](https://docs.orq.ai/docs/experiments/creating) · [Evaluators](https://docs.orq.ai/docs/evaluators/overview) · [Datasets](https://docs.orq.ai/docs/datasets/overview)

When this skill conflicts with live API responses or docs.orq.ai, trust the API.
