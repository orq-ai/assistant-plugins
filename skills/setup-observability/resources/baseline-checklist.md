# Baseline Instrumentation Checklist

Verify these requirements after setting up instrumentation. Framework integrations handle most automatically — only manual instrumentation needs all checks.

## Requirements

| # | Requirement | Why | Auto with AI Router? | Auto with Framework Instrumentor? |
|---|------------|-----|:---:|:---:|
| 1 | **Model name captured** | Enables model comparison, cost attribution, filtering by model | yes | yes |
| 2 | **Token usage tracked** | Enables cost calculation and usage analytics | yes | yes |
| 3 | **Descriptive trace names** | Makes traces findable — `chat-response` not `trace-1` | partial | partial |
| 4 | **Proper span hierarchy** | Shows which step is slow or failing in multi-step operations | n/a | yes |
| 5 | **Correct span types** | Enables type-specific analytics (LLM latency, retrieval quality) | yes | yes |
| 6 | **Sensitive data masked** | Prevents PII/secrets from leaking into trace storage | no | no |
| 7 | **Trace input/output set explicitly** | Makes traces readable; avoids logging irrelevant function args | partial | partial |

### How to Verify Each

**1. Model name** — Open a trace in [Traces](https://my.orq.ai) → click an LLM span → confirm `model` field shows the model ID (e.g., `openai/gpt-4o`).

**2. Token usage** — Same LLM span → check `input_tokens` and `output_tokens` are populated. If zero, the instrumentor may not support the provider or streaming mode.

**3. Trace names** — In the Traces list view, scan the Name column. Look for generic names (`default`, `trace-1`, `LLMChain`) and rename with descriptive alternatives. For `@traced`, set the `name` parameter. For frameworks, check how to customize trace/chain names in the framework docs.

**4. Span hierarchy** — Open a trace → switch to Trace View. Multi-step operations should show nested spans (parent → child). Flat traces with all spans at the same level indicate missing nesting. For `@traced`, ensure child functions are called within the parent's traced scope.

**5. Span types** — In Trace View, check that LLM calls show as `llm` type, retrievals as `retrieval`, tool calls as `tool`, etc. Framework instrumentors set these automatically. For `@traced`, set the `type` parameter correctly.

**6. Sensitive data** — Review a few traces for PII (names, emails, tokens, API keys) in span inputs/outputs. Use `capture_input=False` / `capture_output=False` on `@traced` for sensitive functions. For framework instrumentors, check if they offer input/output filtering.

**7. Trace input/output** — Open a trace → check the top-level input shows the user's actual request (not internal state). For `@traced` with `capture_input=True`, only the function args are logged — ensure they represent meaningful input. Use `attributes` for metadata instead of polluting input.

## After Baseline Passes

Encourage the user to explore traces in the orq.ai UI before adding more context:

> "Your traces are appearing in orq.ai. Open a few in [Traces](https://my.orq.ai) — look at the span hierarchy, timing, and captured data. What's useful? What's missing? This helps us decide what additional context to add."

## Additional Context (Add After Baseline)

Only add these when relevant — infer from the user's code when possible:

| If You See in Code... | Suggest Adding | Why |
|----------------------|----------------|-----|
| Conversation history, chat endpoints, message arrays | `session_id` | Groups messages from the same conversation |
| User authentication, `user_id` variables | `user_id` on traces | Enables per-user filtering and cost attribution |
| Multiple distinct features or endpoints | `feature` tag via attributes | Enables per-feature analytics |
| Customer/tenant identifiers | `customer_id` or tier tag | Cost/quality breakdown by segment |
| Feedback collection, ratings | Score annotations | Enables quality trend monitoring |
| Environment variables like `NODE_ENV`, `FLASK_ENV` | `environment` tag | Separates dev/staging/prod traces |

### How to Add Context

**With `@traced`:**
```python
@traced(
    name="chat-response",
    type="agent",
    attributes={
        "session_id": session_id,
        "user_id": user_id,
        "feature": "customer-support",
    }
)
```

**With OpenTelemetry span attributes:**
```python
from opentelemetry import trace

span = trace.get_current_span()
span.set_attribute("session_id", session_id)
span.set_attribute("user_id", user_id)
```
