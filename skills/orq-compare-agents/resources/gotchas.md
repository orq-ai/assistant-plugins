# Known Gotchas

Common pitfalls when running cross-framework agent comparisons with evaluatorq.

---

## orq.ai SDK

### Agent calls use general Responses

Use `POST /v3/router/responses`, SDK `client.responses.create(model="agent/<key>", input=query)`, or `orq responses create --model "agent/<key>" --input "..." -o json`. The legacy agent-specific invoke/stream/Responses APIs are deprecated and must only be used when explicitly requested, even if a model fails on one of them.

### Message and output format

Use Responses `input` (text or message items), rather than legacy A2A `message.parts`. Read message `content` blocks of type `output_text`, or `response.output_text` with the OpenAI SDK; output may also contain tool and reasoning items. Continue with `previous_response_id=response.id` (keep `store: true`), rather than a task ID. Failed, incomplete or background responses are not completed quality results — check status first.

### Code tools: `additionalProperties: false`

When creating custom code tools for orq agents, the parameter schema **must** include `"additionalProperties": false` or the model will reject the tool call with a 400 error.

---

## orq.ai SDK — Evaluator Invocation

### `orq.evaluators.invoke()` does not exist

The SDK client has no `evaluators` namespace. To invoke an evaluator programmatically, use `orq.evals.invoke()`:

```python
# Wrong — AttributeError: 'Evaluators' object has no attribute 'invoke'
result = orq.evaluators.invoke(key="my-eval", inputs={...})

# Correct — use evals.invoke() with the evaluator ID
result = orq.evals.invoke(
    id="<EVALUATOR_ID>",
    query="input text",
    output="output text",
    reference="reference text",
)
```

Note: `evals.invoke()` takes the evaluator **ID** (not key), and uses `query`/`output`/`reference` as top-level parameters (not an `inputs` dict).

### Response structure is flat

`evals.invoke()` returns a flat `EvaluationResult` (orq-ai-sdk 4.15.6, verified 2026-09-20 with a live invoke returning `{"type": "number", "value": 10, "evaluator_id": …, "status": "passed", "passed": true, "explanation": …, "categories": []}`). Older skills and scripts read it as nested; that is wrong on the current SDK:

```python
result = orq.evals.invoke(id="...", query="...", output="...", reference="...")

# Wrong — the verdict is not wrapped
result.value.value
result.value.explanation

# Correct — siblings on the result
result.value        # the verdict: bool, number, or the chosen label
result.explanation  # str, the evaluator's reasoning
result.passed       # the guardrail's decision when the evaluator has one, else the grader's own
result.status       # "passed" | "condition_failed" | "failed" | "timed_out"
result.type         # verdict shape discriminator; a categorical evaluator reports "string"
```

Also available: `categories`, `confidence`, `evaluator_id`, `trace_id`, `span_id`. In TypeScript the same call is `orq.evals.invoke({ id, invokeEvaluatorRequest: {...} })` — the request key is `invokeEvaluatorRequest`, not `requestBody` — and returns the same flat shape.

### Installing both TypeScript packages downgrades the node SDK

`@orq-ai/evaluatorq` 1.3.2 declares an **optional peer** on `@orq-ai/node` `^3.9.26`, so `npm install @orq-ai/evaluatorq @orq-ai/node` resolves the node SDK to 3.x — and `evals.invoke()` does not exist there. A scorer calling a platform evaluator then fails to compile with `Property 'invoke' does not exist on type 'Evals'`, even though the docs are right about 4.x.

Force the newer SDK (this is what `skills/orq-compare-agents/tests/ts/package.json` does, and how that fixture type-checks at all):

```json
{ "overrides": { "@orq-ai/node": "latest" } }
```

Verified 2026-09-21 against `@orq-ai/evaluatorq` 1.3.2 and `@orq-ai/node` 4.15.9.

### `parallelism` was renamed in Python

Python evaluatorq takes `datapoint_parallelism` (default `10`); `parallelism` is a still-accepted deprecated alias. TypeScript kept `parallelism`, and its default is `1` — sequential. See `evaluatorq-api.md`.

The Python SDK import is `from orq_ai_sdk import Orq` (package: `pip install orq-ai-sdk`).

---

## Evaluator Design

### Wording bias in evaluator prompts

If the evaluator prompt says "compared to the reference", it will penalize correct answers that use different wording. Use "factual correctness" language instead:

```
# Wrong
"Compare the response to the reference answer and score accuracy."

# Correct
"Check whether the response contains the same core facts as the reference.
Different wording is acceptable as long as the facts match."
```

### Same model for fair comparison

When comparing frameworks, ensure all agents use the same underlying model (e.g., `openai/gpt-5-mini`). Otherwise you're measuring model differences, not framework differences.

---

## Dataset Design

### Mock data bias

Do NOT write expected outputs that match one agent's hardcoded data. If one agent has a fake weather tool returning "Sunny, 22C" and another uses a real API, the fake agent will always win against a reference matching its own data.

For dynamic answers (weather, stock prices), write expected outputs as correctness criteria:

```
# Wrong
"The weather in Tokyo is sunny with a temperature of 22C."

# Correct
"The response should include the current temperature and conditions in Tokyo from a real data source."
```

---

## TypeScript-specific

### `wrapLangGraphAgent` type requirements

`wrapLangGraphAgent` from `@orq-ai/evaluatorq/langchain` expects a LangChain-compatible agent instance — not a raw LangGraph `StateGraph`.

### Exit codes for CI/CD

TypeScript evaluatorq exits with code 1 when any evaluator returns `pass: false`. This is intentional for CI/CD pipelines but can be surprising in development.

---

## Staging Environment

`ORQ_BASE_URL` is used **verbatim** — set `https://my.staging.orq.ai` (or any self-hosted host) and evaluatorq talks to exactly that host for inference, upload, and OTEL. Default: `https://my.orq.ai`.

> Historical note: evaluatorq versions before v1.10.0 rewrote `my.` to `api.` internally, which broke staging hosts. That rewrite is gone — if you carried an `api.staging.orq.ai` workaround, drop it.
