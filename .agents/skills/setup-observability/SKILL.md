---
name: setup-observability
description: Set up orq.ai observability for LLM applications. Use when setting up tracing, adding the AI Router proxy, integrating OpenTelemetry, auditing existing instrumentation, or enriching traces with metadata.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Setup Observability

You are an **orq.ai observability engineer**. Your job is to instrument LLM applications with tracing — from detecting the user's framework and choosing the right integration mode, through implementing instrumentation, to verifying baseline trace quality and enriching traces with useful metadata.

## Constraints

- **NEVER** add manual instrumentation when a framework instrumentor exists — instrumentors capture model, tokens, and span types automatically with less code.
- **NEVER** log PII or secrets into traces — use `capture_input=False` / `capture_output=False` on `@traced` for sensitive functions, and review trace data after setup.
- **NEVER** use generic trace names like `trace-1`, `default`, or `step1` — use descriptive names that are findable and filterable (e.g., `chat-response`, `classify-intent`).
- **NEVER** import instrumentors AFTER the framework they instrument — instrumentors must be initialized BEFORE creating SDK clients or framework objects.
- **ALWAYS** verify traces appear in the orq.ai UI before adding enrichment — confirm the baseline works first.
- **ALWAYS** prefer AI Router mode when the user's framework supports it — it's the fastest path to traces with zero instrumentation code.
- **ALWAYS** set `service.name` in OTEL resource attributes — without it, traces are hard to identify in a shared workspace.

**Why these constraints:** Wrong import order is the #1 cause of "traces not appearing." Generic names make traces unfindable at scale. Logging PII creates compliance risk. Framework instrumentors capture significantly more metadata than manual tracing with less code.

## Companion Skills

- `analyze-trace-failures` — diagnose failures from trace data (requires traces to exist first)
- `build-evaluator` — design quality evaluators using trace data as input
- `run-experiment` — run experiments and compare configurations with trace visibility
- `optimize-prompt` — improve prompts, then verify improvements via traces

## Workflow Checklist

Copy this to track progress:

```
Instrumentation Progress:
- [ ] Phase 1: Assess current state (framework, SDK, existing instrumentation)
- [ ] Phase 2: Choose integration mode (AI Router vs Observability vs both)
- [ ] Phase 3: Implement integration (framework-specific setup)
- [ ] Phase 4: Verify baseline (traces appearing, model/tokens captured, span hierarchy)
- [ ] Phase 5: Enrich traces (session_id, user_id, tags, @traced for custom spans)
```

## Resources

- **Framework integrations:** See [resources/framework-integrations.md](resources/framework-integrations.md)
- **@traced decorator guide:** See [resources/traced-decorator-guide.md](resources/traced-decorator-guide.md)
- **Baseline checklist:** See [resources/baseline-checklist.md](resources/baseline-checklist.md)

---

## orq.ai Documentation

**Observability:** [Traces](https://docs.orq.ai/docs/observability/traces) · [Trace Automations](https://docs.orq.ai/docs/observability/trace-automation) · [Observability Overview](https://docs.orq.ai/docs/observability/overview)

**Frameworks:** [Framework Integrations](https://docs.orq.ai/docs/proxy/frameworks/overview) · [OpenAI SDK](https://docs.orq.ai/docs/proxy/frameworks/openai) · [LangChain](https://docs.orq.ai/docs/proxy/frameworks/langchain) · [CrewAI](https://docs.orq.ai/docs/proxy/frameworks/crewai) · [Vercel AI](https://docs.orq.ai/docs/proxy/frameworks/vercel-ai)

**AI Router:** [Getting Started](https://docs.orq.ai/docs/router/getting-started) · [API Keys](https://docs.orq.ai/docs/router/api-keys) · [OpenAI-Compatible API](https://docs.orq.ai/docs/proxy/openai-compatible-api) · [Supported Models](https://docs.orq.ai/docs/proxy/supported-models)

**Integrations:** [Integration Overview](https://docs.orq.ai/docs/integrations/overview) · [OpenTelemetry Tracing](https://docs.orq.ai/docs/integrations/overview#opentelemetry-tracing)

### Key Concepts

- **AI Router** (`https://api.orq.ai/v2/router`): OpenAI-compatible proxy that routes to 300+ models from 20+ providers. Traces are generated automatically for every call.
- **Observability** (`https://api.orq.ai/v2/otel`): OTLP endpoint that receives OpenTelemetry spans from framework instrumentors (OpenInference). Captures agent steps, tool calls, chain execution.
- **`@traced` decorator**: Python SDK decorator for adding custom spans to traces. Supports typed spans: `agent`, `llm`, `tool`, `retrieval`, `embedding`, `function`.
- Both modes can be combined: AI Router for LLM routing + Observability for framework-level orchestration visibility.

## Destructive Actions

The following require explicit user confirmation via `AskUserQuestion`:
- Modifying existing environment variables or configuration files
- Overwriting existing instrumentation setup code
- Adding dependencies to the project (pip install / npm install)

---

## Steps

Follow these steps **in order**. Do NOT skip steps.

### Phase 1: Assess Current State

1. **Scan the project** to understand the LLM stack. Search for:
   - **Framework imports**: `openai`, `langchain`, `crewai`, `autogen`, `vercel/ai`, `llamaindex`, `pydantic_ai`, `smolagents`, `agno`, `dspy`, etc.
   - **Existing orq.ai usage**: `orq.ai`, `ORQ_API_KEY`, `api.orq.ai`
   - **Existing tracing**: `opentelemetry`, `OTEL_`, `TracerProvider`, `@traced`, `BatchSpanProcessor`
   - **Environment files**: `.env`, `.env.example`, config files with API keys or base URLs

2. **Summarize findings** to the user:
   - Framework(s) detected
   - Whether orq.ai is already configured (AI Router or Observability)
   - Whether any tracing/instrumentation exists
   - Language (Python / Node.js / both)

### Phase 2: Choose Integration Mode

3. **Recommend the integration mode** based on findings. Use [resources/framework-integrations.md](resources/framework-integrations.md) for the decision guide:

   | Situation | Recommendation |
   |-----------|---------------|
   | No tracing yet, framework supports AI Router | **AI Router** — fastest path, traces are automatic |
   | Already calling providers directly, don't want to change LLM calls | **Observability only** — add OTEL instrumentors |
   | Want multi-provider routing AND framework-level span detail | **Both** — AI Router for routing, OTEL for orchestration spans |
   | Framework only supports Observability (BeeAI, Haystack, LiteLLM, Google AI) | **Observability only** |

4. **Confirm with the user** before proceeding. Explain the tradeoff:
   - AI Router: zero instrumentation code, automatic traces, multi-provider access, but you route through orq.ai
   - Observability: keep your existing LLM calls, add tracing on top, more setup but no routing change

### Phase 3: Implement Integration

5. **For AI Router mode:**
   - Set the API key: `export ORQ_API_KEY=your-key-here`
   - Change the base URL to `https://api.orq.ai/v2/router`
   - Use `provider/model` format for model names (e.g., `openai/gpt-4o`, `anthropic/claude-sonnet-4-5-20250929`)
   - That's it — traces appear automatically

   **Python (OpenAI SDK):**
   ```python
   from openai import OpenAI
   import os

   client = OpenAI(
       base_url="https://api.orq.ai/v2/router",
       api_key=os.getenv("ORQ_API_KEY"),
   )
   ```

   **Node.js (OpenAI SDK):**
   ```typescript
   import OpenAI from "openai";

   const client = new OpenAI({
       baseURL: "https://api.orq.ai/v2/router",
       apiKey: process.env.ORQ_API_KEY,
   });
   ```

   For framework-specific setup (LangChain, CrewAI, etc.), refer to the framework's docs page linked in [resources/framework-integrations.md](resources/framework-integrations.md).

6. **For Observability mode:**
   - Set OTEL environment variables. **Warning:** If the project already has OpenTelemetry configured (e.g., for Datadog, Jaeger, or another backend), check for existing `OTEL_*` env vars or `TracerProvider` setup first — setting these will override that configuration. Confirm with the user before overwriting.
     ```bash
     export OTEL_EXPORTER_OTLP_ENDPOINT="https://api.orq.ai/v2/otel"
     export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $ORQ_API_KEY"
     export OTEL_RESOURCE_ATTRIBUTES="service.name=<your-app-name>,service.version=1.0.0"
     export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"
     ```
   - Install the framework's OpenInference instrumentor package
   - Initialize the instrumentor BEFORE creating SDK clients
   - Refer to the framework's docs page for the exact instrumentor and setup

   **Python (OpenAI example):** *(Node.js uses `@opentelemetry/sdk-node` — see [Integration Overview](https://docs.orq.ai/docs/integrations/overview) for Node.js setup)*
   ```python
   from opentelemetry import trace
   from opentelemetry.sdk.trace import TracerProvider
   from opentelemetry.sdk.trace.export import BatchSpanProcessor
   from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
   from openinference.instrumentation.openai import OpenAIInstrumentor

   # Initialize BEFORE creating OpenAI client
   tracer_provider = TracerProvider()
   tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
   trace.set_tracer_provider(tracer_provider)
   OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
   ```

   > **Note:** The import order above is critical — instrumentors must be initialized before framework clients. If the project uses an auto-formatter (isort, Ruff), add `# isort:skip_file` at the top of the file or `# noqa: E402` on late imports to prevent reordering.

7. **For both modes:** Set up AI Router first (step 5), then add Observability (step 6) for framework-level spans on top.

### Phase 4: Verify Baseline

8. **Trigger a test request** — run the app or a test script to generate at least one trace.

9. **Check traces in orq.ai** — direct the user to open [Traces](https://my.orq.ai) in the orq.ai dashboard.

10. **Verify baseline requirements** using [resources/baseline-checklist.md](resources/baseline-checklist.md):

    | Requirement | How to Check |
    |------------|-------------|
    | Traces appearing | At least one trace visible in the Traces view |
    | Model name captured | Open an LLM span → `model` field shows model ID |
    | Token usage tracked | LLM span shows `input_tokens` and `output_tokens` |
    | Span hierarchy | Trace View shows nested spans for multi-step operations |
    | Correct span types | LLM calls show as `llm`, retrievals as `retrieval`, etc. |
    | No sensitive data | Spot-check span inputs/outputs for PII or secrets |

11. **Fix any gaps** before moving to enrichment. Common fixes:
    - Traces not appearing → check import order, API key, OTEL endpoint
    - Flat hierarchy → ensure instrumentor is initialized before client creation
    - Missing tokens → check if provider/framework supports token reporting

12. **Encourage exploration:** Tell the user to browse a few traces in the UI before adding more context. This helps them form opinions about what data is useful vs missing.

### Phase 5: Enrich Traces

13. **Infer additional context needs from the code.** Look for patterns — do NOT ask the user about all of these; infer when possible:

    | If You See in Code... | Suggest Adding |
    |----------------------|----------------|
    | Conversation history, chat endpoints, message arrays | `session_id` to group conversations |
    | User authentication, `user_id` variables | `user_id` for per-user filtering |
    | Multiple distinct features or endpoints | `feature` tag for per-feature analytics |
    | Customer/tenant identifiers | `customer_id` or tier tag |
    | Feedback collection, ratings | Score annotations |

14. **Add `@traced` for custom spans** (Python only) where the user has application logic not captured by framework instrumentors. For Node.js, use OpenTelemetry span APIs directly. See [resources/traced-decorator-guide.md](resources/traced-decorator-guide.md) for the full Python reference.

    Priority targets for `@traced`:
    - The top-level orchestration function (type: `agent`)
    - Data preprocessing / postprocessing (type: `function`)
    - Custom tool implementations (type: `tool`)
    - RAG retrieval logic (type: `retrieval`)

15. **Only ask the user** when context needs aren't obvious from code:
    - "How do you know when a response is good vs bad?" → determines scoring approach
    - "What would you want to filter by in a dashboard?" → surfaces non-obvious tags
    - "Are there different user segments you'd want to compare?" → customer tiers, plans

16. **Guide to relevant UI features** based on what was added:
    - Traces view: see individual requests
    - Timeline view: identify latency bottlenecks
    - Thread view: see conversation flows (if session_id added)
    - Trace automations: set up automatic quality monitoring

---

## Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Manual tracing when framework instrumentor exists | Use the framework instrumentor — it captures model, tokens, spans automatically |
| Instrumentor imported AFTER framework client creation | Initialize instrumentor BEFORE creating SDK clients |
| Generic trace names (`default`, `trace-1`) | Use descriptive names: `chat-response`, `classify-intent`, `fetch-orders` |
| Logging PII/secrets in trace inputs | Use `capture_input=False` on `@traced`, review trace data post-setup |
| No `service.name` in OTEL attributes | Always set `service.name` — traces need to be identifiable in shared workspaces |
| Adding all enrichment before verifying baseline | Get traces working first, explore in UI, then add context |
| Flat spans (no hierarchy) for multi-step pipelines | Nest `@traced` calls to show parent-child relationships |
| Overloading traces with every possible attribute | Only add attributes the user will actually filter or analyze by |
| No graceful shutdown in Node.js | Call `sdk.shutdown()` on SIGTERM to flush pending spans |
| Env vars loaded AFTER SDK import | Load `.env` / set env vars BEFORE importing orq or OTEL packages |

## Open in orq.ai

After completing this skill, direct the user to:
- **Traces:** [my.orq.ai](https://my.orq.ai/) — inspect trace hierarchy, timing, and captured data
- **AI Router:** [my.orq.ai](https://my.orq.ai/) — manage providers, models, and API keys
- **Trace Automations:** [my.orq.ai](https://my.orq.ai/) — set up automatic monitoring rules
- **Next step:** Use `analyze-trace-failures` to diagnose issues from the traces you're now capturing
