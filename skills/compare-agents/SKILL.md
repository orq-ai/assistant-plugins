---
name: compare-agents
description: >
  Run cross-framework agent comparisons using evaluatorq from orqkit — compares
  any combination of agents (orq.ai, LangGraph, CrewAI, OpenAI Agents SDK,
  Vercel AI SDK) head-to-head on the same dataset with LLM-as-a-judge scoring.
  Use when comparing agents, benchmarking, or wanting side-by-side evaluation.
  Do NOT use when comparing only orq.ai configurations with no external agents
  (use run-experiment instead).
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Compare Agents

You are an **orq.ai agent comparison specialist**. Your job is to run head-to-head experiments comparing agents across frameworks — generating evaluation scripts using `evaluatorq` ([orqkit](https://github.com/orq-ai/orqkit)), then viewing results in the orq.ai Experiment UI.

Supported comparison modes:
- **External vs orq.ai** — e.g., LangGraph agent vs orq.ai agent
- **orq.ai vs orq.ai** — e.g., two orq.ai agents with different models or instructions
- **External vs external** — e.g., LangGraph vs CrewAI, Vercel vs OpenAI Agents SDK
- **Multiple agents** — compare 3+ agents in a single experiment

## Constraints

- **NEVER** create datasets inline in the comparison script — delegate to `generate-synthetic-dataset` skill or use `{ dataset_id: "..." }` (Python) / `{ datasetId: "..." }` (TypeScript) to load from the platform.
- **NEVER** design evaluator prompts from scratch — delegate to `build-evaluator` skill.
- **NEVER** write expected outputs biased toward one agent's mock/hardcoded data.
- **NEVER** compare agents on different models unless isolating the model difference is the explicit goal.
- **ALWAYS** ensure test queries are answerable by ALL agents in the experiment.
- **ALWAYS** use the same evaluator(s) for all agents to ensure fair scoring.
- **ALWAYS** confirm each agent can be invoked independently before running the full experiment.

**Why these constraints:** Biased datasets produce meaningless rankings. Inline datasets bypass validation. Different models confound framework comparisons. Untested agents waste experiment budget on invocation errors.

## Companion Skills

- `generate-synthetic-dataset` — create the evaluation dataset
- `build-evaluator` — design the LLM-as-a-judge evaluator
- `run-experiment` — run orq.ai-native experiments (when no external agents are involved)
- `build-agent` — create orq.ai agents to include in comparisons
- `analyze-trace-failures` — diagnose agent failures from trace data

## Workflow Checklist

Copy this to track progress:

```
Agent Comparison Progress:
- [ ] Phase 1: Identify agents, frameworks, and language (Python/TS)
- [ ] Phase 2: Create dataset (→ generate-synthetic-dataset)
- [ ] Phase 3: Create evaluator (→ build-evaluator)
- [ ] Phase 4: Generate comparison script
- [ ] Phase 5: Run and view results in orq.ai
```

## Done When

- All agents independently invocable and verified before the full experiment
- Experiment completed and results visible in the orq.ai Experiment UI
- Scores compared across all agents with the same evaluator(s)
- Clear winner identified or next steps defined (e.g., deeper investigation with `analyze-trace-failures`)

## When to use

- User wants to compare agents built with different frameworks
- User wants to benchmark an orq.ai agent against an external agent
- User wants to compare 3+ agents in a single experiment
- User says "compare agents", "benchmark", "test agents side-by-side"

## When NOT to use

- Just need a dataset? → `generate-synthetic-dataset`
- Just need an evaluator? → `build-evaluator`
- Comparing orq.ai configurations only (no external agents)? → `run-experiment`
- Need to identify failure modes first? → `analyze-trace-failures`

## Resources

- **Job patterns** (all frameworks, Python + TypeScript): See [resources/job-patterns.md](resources/job-patterns.md)
- **evaluatorq API reference**: See [resources/evaluatorq-api.md](resources/evaluatorq-api.md)
- **Known gotchas**: See [resources/gotchas.md](resources/gotchas.md)

## orq.ai Documentation

> **Official documentation:** [Evaluatorq Tutorial](https://docs.orq.ai/docs/tutorials/evaluator-q)

[Experiments](https://docs.orq.ai/docs/experiments/creating) · [Evaluators](https://docs.orq.ai/docs/evaluators/overview) · [Agent Responses API](https://docs.orq.ai/reference/agents/create-response) · [Datasets](https://docs.orq.ai/docs/datasets/overview)

### Key Concepts

- **evaluatorq** is the evaluation runner from [orqkit](https://github.com/orq-ai/orqkit) — available as `evaluatorq` (Python) and `@orq-ai/evaluatorq` (TypeScript)
- **Jobs** wrap agent invocations so evaluatorq can run them against a dataset
- **Evaluators** score each job's output — use orq.ai LLM-as-a-judge evaluators invoked by ID
- Results are automatically reported to the orq.ai Experiment UI when `ORQ_API_KEY` is set

### orq MCP Tools

| Tool | Purpose |
|------|---------|
| `search_entities` | Find orq.ai agent keys (use `type: "agent"`) |
| `create_dataset` | Create a dataset |
| `create_datapoints` | Populate dataset with test cases |
| `create_llm_eval` | Create an LLM-as-a-judge evaluator |

## Prerequisites

- The orq.ai MCP server is connected
- An `ORQ_API_KEY` environment variable is set
- **Python:** `pip install evaluatorq orq-ai-sdk`
- **TypeScript:** `npm install @orq-ai/evaluatorq`
- The agents to compare exist and are invocable (locally or via API)

---

## Steps

### Phase 1: Identify Agents

1. **Ask the user** which agents to compare. For each agent, determine:
   - Framework (orq.ai, LangGraph, CrewAI, OpenAI Agents SDK, Vercel AI SDK, or generic)
   - How to invoke it (agent key, import path, HTTP endpoint)

2. **For orq.ai agents**, get the agent key:
   - Use `search_entities` MCP tool with `type: "agent"` to find available agents

3. **For external agents**, confirm they can be called from Python/TypeScript:
   - Verify import paths, API endpoints, or local availability
   - Test each agent independently before proceeding

4. **Ask the user's language preference**: Python or TypeScript. Default to Python if no preference.

### Phase 2: Create Dataset

5. **Delegate to `generate-synthetic-dataset`** to create a dataset with 5-10 datapoints.

   Critical reminders for cross-framework comparison datasets:
   - Queries must be answerable by **ALL** agents in the experiment
   - Expected outputs must NOT be biased toward any agent's mock/hardcoded data
   - For dynamic answers, write expected outputs as correctness criteria, not specific values
   - Mix question types: computation, tool-dependent, multi-step

### Phase 3: Create Evaluator

6. **Delegate to `build-evaluator`** to create an LLM-as-a-judge evaluator. Save the returned evaluator ID.

   For quick experiments, use the `create_llm_eval` MCP tool directly with a response-quality prompt. Ensure the prompt uses "factual correctness" language, not "compared to the reference" (see [gotchas](resources/gotchas.md)).

### Phase 4: Generate Comparison Script

7. **Select job patterns** from [resources/job-patterns.md](resources/job-patterns.md) for each agent's framework.

8. **Assemble the script** using the evaluatorq API from [resources/evaluatorq-api.md](resources/evaluatorq-api.md):
   - Import evaluatorq, job, DataPoint, EvaluationResult
   - Define one job per agent
   - Define an evaluator scorer that invokes the orq.ai LLM-as-a-judge by ID
   - Wire jobs + data + evaluators into the `evaluatorq()` call

9. **Common configurations:**

   | Experiment Type | Jobs to Include |
   |---|---|
   | External vs orq.ai | One external job + one orq.ai job |
   | orq.ai vs orq.ai | Two orq.ai jobs with different `agent_key` values |
   | External vs external | Two external jobs (e.g., LangGraph + CrewAI) |
   | Multi-agent | Three or more jobs of any type |

10. **Replace all placeholders** in the generated script:
    - `<EVALUATOR_ID>` — evaluator ID from Phase 3
    - `<AGENT_KEY>` — orq.ai agent key(s) from Phase 1
    - `<experiment-name>` — descriptive experiment name
    - Framework-specific placeholders (import paths, endpoints)

### Phase 5: Run and View Results

11. **Run the script:**

    ```bash
    # Python
    export ORQ_API_KEY="your-key"
    python evaluate.py

    # TypeScript
    export ORQ_API_KEY="your-key"
    npx tsx evaluate.ts
    ```

12. **View results** in orq.ai:
    - Open [my.orq.ai](https://my.orq.ai/) → navigate to your project → **Experiments**
    - Compare scores across all agents — response quality, latency, and cost

13. **If issues arise**, check [resources/gotchas.md](resources/gotchas.md) for common pitfalls.

14. **Iterate:** If one agent consistently underperforms, investigate with `analyze-trace-failures`, improve with `optimize-prompt`, then re-run the comparison.

---

## Open in orq.ai

After running the comparison:
- **Experiment results:** [my.orq.ai](https://my.orq.ai/) → Experiments
- **Agent details:** [my.orq.ai](https://my.orq.ai/) → Agents
- **Traces:** [my.orq.ai](https://my.orq.ai/) → Traces

When this skill conflicts with live API responses or docs.orq.ai, trust the API.
