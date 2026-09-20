# evaluatorq API Reference

Quick reference for the evaluatorq library. Python package: [evaluatorq](https://github.com/orq-ai/evaluatorq). TypeScript package: [@orq-ai/evaluatorq](https://github.com/orq-ai/orqkit) (`packages/evaluatorq`).

Probed 2026-09-20 against **Python `evaluatorq` 1.39.0**, **`@orq-ai/evaluatorq` 1.3.2** and **`orq-ai-sdk` / `@orq-ai/node` 4.15.6**. The two language packages are **not** at parity — see [Python vs TypeScript](#python-vs-typescript).

For judges and juries (`llm_jury`, `llm_jury_pairwise`), input shapes, experiment replay and reasoning-effort tuning, see the **`evaluatorq` skill** and its resources; this file is the shared job/scorer surface `orq-compare-agents` needs.

---

## Installation

| Language | Package | Install |
|----------|---------|---------|
| Python | `evaluatorq` | `pip install evaluatorq orq-ai-sdk` (add the `eq` CLI with `pip install 'evaluatorq[redteam]'`) |
| TypeScript | `@orq-ai/evaluatorq` | `npm install @orq-ai/evaluatorq` |

---

## Core API

### Job Definition

**Python:**
```python
from evaluatorq import job, DataPoint

@job("AgentName")
async def my_job(data: DataPoint, row: int):
    # Call your agent with data.inputs["query"]
    return {
        "agent": "AgentName",
        "query": data.inputs["query"],
        "response": "agent output here",
    }
```

**TypeScript:**
```typescript
import { job } from "@orq-ai/evaluatorq";

const myJob = job("AgentName", async (data) => {
  // Call your agent with data.inputs.query
  return {
    agent: "AgentName",
    query: data.inputs.query,
    response: "agent output here",
  };
});
```

### Evaluator Scorer

**Python:**
```python
from evaluatorq import EvaluationResult

async def my_scorer(params):
    data: DataPoint = params["data"]
    output = params["output"]
    # Score the output
    return EvaluationResult(
        value=0.85,
        explanation="Factually correct",
    )
```

**TypeScript:**
```typescript
const myScorer = async ({ data, output }) => ({
  value: 0.85,
  explanation: "Factually correct",
});
```

### Invoking an orq.ai Evaluator from a Scorer

To use an orq.ai LLM-as-a-judge evaluator inside a scorer, call `orq.evals.invoke()` with the evaluator's **ID** (not key).

> **Important:** The SDK method is `orq.evals.invoke()`, NOT `orq.evaluators.invoke()`. The `evaluators` namespace does not exist on the SDK client.

The response is **flat**: `result.value` is the verdict (bool / number / label), with `result.explanation`, `result.passed`, `result.status`, `result.type`, `result.categories`, `result.confidence`, `result.evaluator_id`, `result.trace_id`, `result.span_id` beside it. There is no `result.value.value`.

**Python:**
```python
import os

from orq_ai_sdk import Orq
from evaluatorq import EvaluationResult

EVALUATOR_ID = "<EVALUATOR_ID>"

async def orq_eval_scorer(params):
    data: DataPoint = params["data"]
    output = params["output"]

    orq = Orq(api_key=os.environ["ORQ_API_KEY"],
              server_url=os.environ.get("ORQ_BASE_URL", "https://my.orq.ai"))
    # invoke_async — never the blocking invoke() inside an async scorer
    result = await orq.evals.invoke_async(
        id=EVALUATOR_ID,
        query=data.inputs["query"],
        output=output["response"],
        reference=data.expected_output or "",
    )

    return EvaluationResult(
        value=1.0 if result.value else 0.0,      # flat: result.value, NOT result.value.value
        explanation=result.explanation or "",
    )
```

**TypeScript:**
```typescript
import { Orq } from "@orq-ai/node";

const EVALUATOR_ID = "<EVALUATOR_ID>";

const orqEvalScorer = async ({ data, output }) => {
  const orq = new Orq({ apiKey: process.env.ORQ_API_KEY! });
  const result = await orq.evals.invoke({
    id: EVALUATOR_ID,
    invokeEvaluatorRequest: {          // the request key is invokeEvaluatorRequest, not requestBody
      query: data.inputs.query,
      output: output.response,
      reference: data.expected_output ?? "",
    },
  });

  return {
    value: result.value ? 1.0 : 0.0,   // flat, same as Python
    explanation: result.explanation ?? "",
  };
};
```

### Running evaluatorq

**Python:**
```python
from evaluatorq import evaluatorq, DataPoint

async def main():
    await evaluatorq(
        "experiment-name",
        data=[
            DataPoint(
                inputs={"query": "What is 2+2?"},
                expected_output="4",
            ),
        ],
        jobs=[job_a, job_b],
        evaluators=[
            {"name": "quality", "scorer": my_scorer},
        ],
        datapoint_parallelism=5,  # default 10; `parallelism` is the deprecated alias
    )
```

> **`jobs` is a list, and every job runs against every data point.** That is the comparison
> mechanism — one `evaluatorq()` call with N jobs, never a loop of N calls with one job each.
> A single call yields one experiment and one evaluator × job results table; a loop yields N
> unrelated experiments. `datapoint_parallelism` counts tasks and nests — N datapoints at once,
> and within each a fresh budget of the same size for its jobs and then its evaluators — so ten
> datapoints × ten evaluators is a hundred concurrent tasks. `llm_parallelism` bounds in-flight
> LLM **requests** for the whole run instead; use it against a provider concurrency limit.

**TypeScript:**
```typescript
import { evaluatorq } from "@orq-ai/evaluatorq";

await evaluatorq("experiment-name", {
  data: [{ inputs: { query: "What is 2+2?" } }],
  jobs: [jobA, jobB],
  evaluators: [{ name: "quality", scorer: myScorer }],
});
```

---

## Function Signature

### Python

```python
async def evaluatorq(
    name: str,
    params: EvaluatorParams | dict | None = None,
    *,
    data: DatasetIdInput | ExperimentInput | Sequence[Awaitable[DataPoint] | DataPointInput] | None = None,
    jobs: list[Job] | None = None,
    evaluators: list[Evaluator] | None = None,
    datapoint_parallelism: int = 10,     # `parallelism` still accepted, deprecated
    llm_parallelism: int | None = None,  # None = unbounded
    print_results: bool = True,
    description: str | None = None,
    path: str | None = None,             # e.g. "Project/Folder" on the orq dashboard
    inference: bool = True,              # False = score recorded outputs, skip the jobs
) -> EvaluatorqResult
```

> **Platform data:** `DatasetIdInput(dataset_id="...", include_messages=False)` — snake_case `dataset_id`, NOT `datasetId`. The bare dict `{"dataset_id": "..."}` still parses. `ExperimentInput(experiment_id=..., run_id=None)` replays a past experiment run and requires `inference=False`.

### TypeScript

```typescript
evaluatorq(name: string, options: {
  data: (DataPoint | Promise<DataPoint>)[] | { datasetId: string; includeMessages?: boolean };
  jobs: Job[];
  evaluators?: Evaluator[];
  parallelism?: number;    // default 1 — SEQUENTIAL, unlike Python's 10
  print?: boolean;         // not print_results
  description?: string;
  path?: string;
}): Promise<EvaluatorqResult>   // = DataPointResult[]
```

> **TypeScript supports `{ datasetId: "..." }`** to fetch data directly from the orq.ai platform instead of inlining datapoints. There is no `inference`, no `ExperimentInput`, and no `llmParallelism`. A TS scorer's `ScorerParameter` is `{ data, output }` only — no `row`.

### Python vs TypeScript

| Capability | Python 1.39.0 | TS 1.3.2 |
|---|---|---|
| `llm_jury()` / `llm_jury_pairwise()` / presets | yes | **no** |
| Experiment replay (`ExperimentInput`, `inference=False`) | yes | **no** |
| `llm_parallelism` / `llm_slot()` | yes | **no** |
| `parallelism` default | `10` (as `datapoint_parallelism`) | `1`, i.e. sequential |
| Dataset input | `DatasetIdInput(dataset_id=, include_messages=)` | `{ datasetId, includeMessages? }` |
| Framework wrappers | LangChain/LangGraph, OpenAI Agents, CrewAI, Pydantic AI, OpenResponses, Vercel | LangChain/LangGraph, AI SDK, OpenResponses, simulation |

Write judge- and jury-based evaluations in Python. Use TypeScript for custom scorers in a TS stack, and verify a field against the installed package before promising it.

---

## Built-in Evaluators (Python)

```python
from evaluatorq import exact_match_evaluator, llm_jury, string_contains_evaluator

evaluators=[
    string_contains_evaluator(case_insensitive=True, name="contains-check"),
    exact_match_evaluator(name="exact-match"),
    {"name": "custom", "scorer": my_scorer},
    llm_jury(name="quality", criteria="The answer is correct.", preset="Balanced Trio"),
]
```

`llm_jury()` is the built-in LLM judge / jury — see the `evaluatorq` skill's [judges and juries](../../evaluatorq/resources/judges-and-juries.md) for panel configuration and how to read a verdict. It has no TypeScript equivalent.

---

## Framework Wrappers (TypeScript)

```typescript
import { wrapLangGraphAgent } from "@orq-ai/evaluatorq/langchain";  // wrapLangChainAgent is the same function
import { wrapAISdkAgent } from "@orq-ai/evaluatorq/ai-sdk";

const langGraphJob = wrapLangGraphAgent(agent, { name: "LangGraph" });
const vercelJob = wrapAISdkAgent(agent, { name: "VercelAgent" });
```

**The agent comes first, the name goes in the options object** — `wrapLangGraphAgent("LangGraph", agent)` is wrong. Options are `{ name, promptKey (default `"prompt"`), instructions }`, plus `tools` on the LangChain wrapper. Subpath exports: `/langchain`, `/ai-sdk`, `/openresponses`, `/simulation` (`wrapSimulationAgent`, JSONL dataset helpers).

---

## Environment

| Variable | Purpose | Default |
|----------|---------|---------|
| `ORQ_API_KEY` | orq.ai API key (required for platform integration) | — |
| `ORQ_BASE_URL` | orq.ai base URL (used verbatim — no host rewriting) | `https://my.orq.ai` |

When `ORQ_API_KEY` is set, evaluatorq automatically reports results to the orq.ai Experiment UI.
