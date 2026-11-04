# Framework Integrations

## Which Integration Mode?

| Mode | What It Does | When to Use |
|------|-------------|-------------|
| **AI Router** | Route LLM calls through `https://my.orq.ai/v3/router` — traces generated automatically | You want multi-provider access, fallbacks, caching, cost tracking with zero instrumentation code |
| **Observability** | Send OpenTelemetry traces from your existing setup to `https://api.orq.ai/v2/otel` | You already call providers directly and want to add tracing without changing your LLM calls |
| **Both** | AI Router for routing + Observability for framework-level spans | You want full pipeline visibility: framework orchestration spans + LLM call traces |
| **Control Tower** | Full agent lifecycle management — deploy, monitor, and control agents from the orq.ai dashboard | Framework has native orq.ai integration for agent orchestration (currently: LangGraph, OpenAI Agents, Vercel AI SDK) |

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

For model and agent generation, point a Responses-capable client at the v3 router and explicitly select Responses. Changing the base URL alone does not switch a Chat Completions client to Responses. If a framework cannot use Responses, use a Responses-backed callable or ask for an explicit interface exception.

Prefer the orq SDK for new code and when it is already imported. If orq is not imported and OpenAI already is, reuse OpenAI instead of adding a second SDK.

**Python (orq SDK, preferred):**
```python
from orq_ai_sdk import Orq
import os

with Orq(api_key=os.environ["ORQ_API_KEY"], server_url="https://my.orq.ai") as orq:
    response = orq.responses.create(model="openai/gpt-6-luna", input="Hello", stream=False)
    print(response)
```

**Node.js (orq SDK, preferred):**
```typescript
import { Orq } from "@orq-ai/node";

const orq = new Orq({ apiKey: process.env.ORQ_API_KEY, serverURL: "https://my.orq.ai" });
const response = await orq.responses.create({ model: "openai/gpt-6-luna", input: "Hello", stream: false });
console.log(response);
```

**Python (OpenAI SDK, already imported):**
```python
from openai import OpenAI
import os

client = OpenAI(
    base_url="https://my.orq.ai/v3/router",
    api_key=os.environ["ORQ_API_KEY"],
)
response = client.responses.create(model="openai/gpt-6-luna", input="Hello")
print(response.output_text)
```

**Node.js (OpenAI SDK, already imported):**
```typescript
import OpenAI from "openai";

const client = new OpenAI({
    baseURL: "https://my.orq.ai/v3/router",
    apiKey: process.env.ORQ_API_KEY,
});
const response = await client.responses.create({ model: "openai/gpt-6-luna", input: "Hello" });
console.log(response.output_text);
```

**LangChain:**
```python
from langchain_openai import ChatOpenAI
import os

llm = ChatOpenAI(
    model="openai/gpt-6-luna",
    api_key=os.environ["ORQ_API_KEY"],
    base_url="https://my.orq.ai/v3/router",
    use_responses_api=True,
)
```

Select a `langchain-openai` version with Responses support. See [ChatOpenAI Responses routing](https://reference.langchain.com/python/langchain-openai/langchain_openai/chat_models/base/ChatOpenAI).

## Observability (OpenTelemetry) Quick Setup Pattern

**First-party one-liners (Python).** The orq SDK ships two setups that export to orq.ai without wiring OTLP yourself for LangChain; the Agents one still needs a tracer provider:

```python
# LangChain / LangGraph (needs langchain-core) — zero config
from orq_ai_sdk.langchain import setup
setup()  # reads ORQ_API_KEY; pass api_key=… / api_url=… to override

# OpenAI Agents SDK (needs openai-agents, opentelemetry-sdk, opentelemetry-exporter-otlp,
# opentelemetry-instrumentation). The instrumentor uses the global TracerProvider and
# nothing sets one, so configure OTLP export first (block below) or pass your own.
# It also replaces the Agents SDK's default trace processors, so OpenAI platform traces stop.
from orq_ai_sdk.openai_agents_instrumentation import OpenAIAgentsInstrumentor
OpenAIAgentsInstrumentor().instrument(tracer_provider=tracer_provider)
```

For any other framework, use its OpenInference instrumentor with OTLP export to orq.ai:

**Environment variables (all frameworks):**
```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://api.orq.ai/v2/otel"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $ORQ_API_KEY"
export OTEL_RESOURCE_ATTRIBUTES="service.name=<your-app-name>,service.version=1.0.0"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"
```

**Python (OpenAI instrumentor):**
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from openinference.instrumentation.openai import OpenAIInstrumentor

tracer_provider = TracerProvider()
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(tracer_provider)

OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
```

**Key:** Each framework has its own OpenInference instrumentor package. See the framework-specific docs page for the exact package name and import.

> **Node.js/TypeScript:** The Observability examples above are Python-only. For Node.js OTEL setup, use `@opentelemetry/sdk-node` with `@opentelemetry/exporter-trace-otlp-http` and framework-specific instrumentors from the `@opentelemetry/instrumentation-*` namespace (not OpenInference). See the [orq.ai Integration Overview](https://docs.orq.ai/docs/integrations/overview) for Node.js setup.
