# Framework Integrations

## Which Integration Mode?

| Mode | What It Does | When to Use |
|------|-------------|-------------|
| **AI Router** | Route LLM calls through `https://api.orq.ai/v2/router` — traces generated automatically | You want multi-provider access, fallbacks, caching, cost tracking with zero instrumentation code |
| **Observability** | Send OpenTelemetry traces from your existing setup to `https://api.orq.ai/v2/otel` | You already call providers directly and want to add tracing without changing your LLM calls |
| **Both** | AI Router for routing + Observability for framework-level spans | You want full pipeline visibility: framework orchestration spans + LLM call traces |

**Rule of thumb:** If the user's framework is in the AI Router column, start there — it's the fastest path to traces. Add Observability on top only if they need framework-level span detail (agent steps, tool calls, chain execution).

## Supported Frameworks

| Framework | AI Router | Observability | Control Tower | Docs |
|-----------|:---------:|:-------------:|:-------------:|------|
| Agno | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/agno) |
| AutoGen | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/autogen) |
| AWS Strands | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/aws-strands) |
| Azure AI Agents | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/azure-ai-agents) |
| BeeAI | | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/beeai) |
| CrewAI | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/crewai) |
| DSPy | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/dspy) |
| Google AI | | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/google-ai) |
| Haystack | | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/haystack) |
| Instructor | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/instructor) |
| LangChain | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/langchain) |
| LangGraph | yes | | yes | [docs](https://docs.orq.ai/docs/proxy/frameworks/langgraph) |
| LiteLLM | | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/litellm) |
| LiveKit | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/livekit) |
| LlamaIndex | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/llamaindex) |
| LlamaIndex Agents | yes | | | [docs](https://docs.orq.ai/docs/proxy/frameworks/llamaindex-agents) |
| Mastra | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/mastra) |
| OpenAI SDK | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/openai) |
| OpenAI Agents | yes | yes | yes | [docs](https://docs.orq.ai/docs/proxy/frameworks/openai-agents) |
| OpenClaw | | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/openclaw) |
| Pydantic AI | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/pydantic-ai) |
| Semantic Kernel | yes | | | [docs](https://docs.orq.ai/docs/proxy/frameworks/semantic-kernel) |
| SmolAgents | yes | yes | | [docs](https://docs.orq.ai/docs/proxy/frameworks/smolagents) |
| Vercel AI SDK | yes | yes | yes | [docs](https://docs.orq.ai/docs/proxy/frameworks/vercel-ai) |

## AI Router Quick Setup Pattern

All AI Router integrations follow the same pattern — point your SDK's base URL to orq.ai:

**Python (OpenAI SDK):**
```python
from openai import OpenAI

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

**LangChain:**
```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4o",
    api_key=os.getenv("ORQ_API_KEY"),
    base_url="https://api.orq.ai/v2/router",
)
```

## Observability (OpenTelemetry) Quick Setup Pattern

All observability integrations use OpenInference instrumentors with OTLP export to orq.ai:

**Environment variables (all frameworks):**
```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://api.orq.ai/v2/otel"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $ORQ_API_KEY"
export OTEL_RESOURCE_ATTRIBUTES="service.name=my-app,service.version=1.0.0"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"
```

**Python (OpenAI instrumentor):**
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.openai import OpenAIInstrumentor

tracer_provider = TracerProvider()
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(tracer_provider)

OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
```

**Key:** Each framework has its own OpenInference instrumentor package. See the framework-specific docs page for the exact package name and import.
