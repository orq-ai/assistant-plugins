# Known Gotchas

Common pitfalls when running cross-framework agent comparisons with evaluatorq.

---

## orq.ai SDK

### `agents.invoke()` vs `agents.responses.create()`

`invoke()` returns an async A2A task with `status: "submitted"` — it does NOT return the agent's response. Use `responses.create(background=False)` to get a synchronous response with output.

```python
# Wrong — returns task status, not output
response = orq.agents.invoke(agent_key="my-agent", ...)

# Correct — returns actual response
response = orq.agents.responses.create(
    agent_key="my-agent",
    background=False,
    message={"role": "user", "parts": [{"kind": "text", "text": query}]},
)
```

### Message format

The orq agent API uses A2A message format, not OpenAI-style:

```python
# Wrong
message={"role": "user", "content": "Hello"}

# Correct
message={"role": "user", "parts": [{"kind": "text", "text": "Hello"}]}
```

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

### Response structure is nested

The response from `evals.invoke()` wraps the evaluation result inside `result.value`:

```python
result = orq.evals.invoke(id="...", query="...", output="...", reference="...")

# Wrong — AttributeError: 'InvokeEvalResponseBodyLLM' has no attribute 'explanation'
result.explanation

# Correct — value and explanation are nested under result.value
result.value.value        # bool or number (the evaluator score)
result.value.explanation  # str (the evaluator reasoning)
```

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

When comparing frameworks, ensure all agents use the same underlying model (e.g., `openai/gpt-4o-mini`). Otherwise you're measuring model differences, not framework differences.

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

When using `ORQ_BASE_URL` with a staging URL like `https://my.staging.orq.ai`, evaluatorq internally transforms `my.` to `api.` in URLs. If `api.staging.orq.ai` doesn't exist, this will cause connection errors.

**Workaround:** Set `ORQ_BASE_URL=https://api.staging.orq.ai` directly, bypassing the transformation.
