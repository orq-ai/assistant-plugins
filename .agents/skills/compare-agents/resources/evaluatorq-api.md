# evaluatorq API Reference

Quick reference for the evaluatorq library from [orqkit](https://github.com/orq-ai/orqkit). Available in both Python and TypeScript.

---

## Installation

| Language | Package | Install |
|----------|---------|---------|
| Python | `evaluatorq` | `pip install evaluatorq orq-ai-sdk` |
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

**Python:**
```python
from orq_ai_sdk import Orq
from evaluatorq import EvaluationResult

EVALUATOR_ID = "<EVALUATOR_ID>"

async def orq_eval_scorer(params):
    data: DataPoint = params["data"]
    output = params["output"]

    orq = Orq(api_key=ORQ_API_KEY, server_url=ORQ_BASE_URL)
    result = orq.evals.invoke(
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

**TypeScript:**
```typescript
import { Orq } from "@orq-ai/node";

const EVALUATOR_ID = "<EVALUATOR_ID>";

const orqEvalScorer = async ({ data, output }) => {
  const orq = new Orq({ apiKey: process.env.ORQ_API_KEY! });
  const result = await orq.evals.invoke({
    id: EVALUATOR_ID,
    requestBody: {
      query: data.inputs.query,
      output: output.response,
      reference: data.expected_output ?? "",
    },
  });

  return {
    value: result.value.value ? 1.0 : 0.0,
    explanation: result.value.explanation ?? "",
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
        parallelism=5,
    )
```

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
    data: DatasetIdInput | Sequence[DataPoint] | None = None,
    jobs: list[Job] | None = None,
    evaluators: list[Evaluator] | None = None,
    parallelism: int = 1,
    print_results: bool = True,
    description: str | None = None,
) -> EvaluatorqResult
```

> **Python supports `{"dataset_id": "..."}`** (snake_case) to fetch data from the orq.ai platform. Note: use `dataset_id`, NOT `datasetId`.

### TypeScript

```typescript
evaluatorq(name: string, options: {
  data: DataPoint[] | { datasetId: string };
  jobs: Job[];
  evaluators: Evaluator[];
  parallelism?: number;
}): Promise<void>
```

> **TypeScript supports `{ datasetId: "..." }`** to fetch data directly from the orq.ai platform instead of inlining datapoints.

---

## Built-in Evaluators (Python)

```python
from evaluatorq import string_contains_evaluator, exact_match_evaluator

evaluators=[
    string_contains_evaluator(case_insensitive=True, name="contains-check"),
    exact_match_evaluator(name="exact-match"),
    {"name": "custom", "scorer": my_scorer},
]
```

---

## Framework Wrappers (TypeScript)

```typescript
import { wrapLangGraphAgent } from "@orq-ai/evaluatorq/langchain";
import { wrapAISdkAgent } from "@orq-ai/evaluatorq/ai-sdk";

const langGraphJob = wrapLangGraphAgent("LangGraph", agent);
const vercelJob = wrapAISdkAgent("VercelAgent", agent);
```

---

## Environment

| Variable | Purpose | Default |
|----------|---------|---------|
| `ORQ_API_KEY` | orq.ai API key (required for platform integration) | — |
| `ORQ_BASE_URL` | orq.ai base URL | `https://api.orq.ai` |

When `ORQ_API_KEY` is set, evaluatorq automatically reports results to the orq.ai Experiment UI.
