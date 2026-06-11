# The `@traced` Decorator

The `@traced` decorator from the orq.ai **Python** SDK adds custom spans to your traces for application logic that isn't automatically captured by framework instrumentors. Node.js/TypeScript does not have a `@traced` equivalent — use OpenTelemetry span APIs directly for custom spans in Node.js.

**Docs:** [Custom Tracing using the @traced decorator](https://docs.orq.ai/docs/observability/traces#custom-tracing-using-the-@traced-decorator)

## Prerequisites

The orq.ai SDK client must be initialized before `@traced` will export spans:

```python
from orq_ai_sdk import Orq
from orq_ai_sdk.traced import traced
import os

client = Orq(api_key=os.environ["ORQ_API_KEY"])

@traced(name="my-operation", type="function")
def my_function():
    ...
```

Without initializing `Orq(api_key=...)`, the `@traced` decorator will silently do nothing — no error, but no spans exported.

## When to Use

| Scenario | Use `@traced` | Use Framework Instrumentor |
|----------|:------------:|:--------------------------:|
| LLM calls via OpenAI/LangChain/etc. | | yes |
| Custom business logic between LLM calls | yes | |
| Data preprocessing / postprocessing | yes | |
| Tool implementations in an agent | yes | |
| RAG retrieval logic | yes | |
| Orchestration / routing functions | yes | |

**Rule:** Use framework instrumentors for LLM calls (they capture model, tokens, etc. automatically). Use `@traced` for everything else that you want visible in the trace.

## Span Types

| Type | When to Use |
|------|-------------|
| `agent` | Orchestration workflows, agent execution loops |
| `llm` | Direct LLM API calls (prefer framework instrumentors when available) |
| `tool` | External tool invocations, API calls, database queries |
| `retrieval` | Knowledge lookups, vector search, document fetching |
| `embedding` | Embedding operations |
| `function` | General processing steps, data transformation, validation |

## Parameters

```python
@traced(
    name="operation_name",       # Descriptive name shown in trace UI
    type="function",             # Span type (see table above)
    capture_input=True,          # Whether to capture function input args
    capture_output=True,         # Whether to capture function return value
    attributes={                 # Custom key-value metadata
        "custom_key": "value"
    }
)
```

| Parameter | Default | Notes |
|-----------|---------|-------|
| `name` | function name | Use descriptive names: `"fetch-user-context"` not `"step1"` |
| `type` | `"function"` | Pick the semantic type that matches the operation |
| `capture_input` | `True` | **Default captures all function args.** Set `False` if inputs contain PII or secrets |
| `capture_output` | `True` | **Default captures all return values.** Set `False` if outputs contain sensitive data |
| `attributes` | `{}` | Add searchable metadata: user tier, feature name, etc. |

## Examples

### Sync Function
```python
from orq_ai_sdk.traced import traced

@traced(name="extract-keywords", type="function")
def extract_keywords(text: str) -> list[str]:
    # Your logic here
    return keywords
```

### Async Function
```python
from orq_ai_sdk.traced import traced

@traced(name="fetch-context", type="retrieval")
async def fetch_context(query: str) -> list[dict]:
    results = await vector_db.search(query)
    return results
```

### Agent Orchestration
```python
from orq_ai_sdk.traced import traced

@traced(name="support-agent", type="agent")
def run_support_agent(user_message: str) -> str:
    context = fetch_context(user_message)      # traced as retrieval
    response = generate_response(context)       # traced by framework instrumentor
    log_interaction(user_message, response)     # traced as function
    return response
```

### Hiding Sensitive Data
```python
@traced(
    name="process-payment",
    type="tool",
    capture_input=False,   # Don't capture credit card details
    capture_output=False,  # Don't capture payment tokens
    attributes={"service": "payments"}
)
def process_payment(card_number: str, amount: float) -> dict:
    ...
```

### Adding Custom Attributes
```python
@traced(
    name="classify-intent",
    type="function",
    attributes={
        "feature": "routing",
        "version": "2.1",
    }
)
def classify_intent(message: str) -> str:
    ...
```

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Using `@traced` for LLM calls when instrumentor exists | Misses model/token metadata | Use framework instrumentor for LLM calls |
| Generic names like `"step1"`, `"process"` | Hard to find in trace UI | Use descriptive names: `"classify-intent"`, `"fetch-user-orders"` |
| `capture_input=True` on functions with secrets | Leaks API keys, tokens, PII into traces | Set `capture_input=False` and use `attributes` for safe metadata |
| Wrong span type | Breaks trace analytics (e.g., retrieval latency dashboard) | Match type to semantic meaning of the operation |
| Forgetting to trace orchestration function | Top-level agent loop invisible in traces | Wrap the entry point with `@traced(type="agent")` |
