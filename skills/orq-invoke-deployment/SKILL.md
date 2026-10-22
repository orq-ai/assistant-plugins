---
name: orq-invoke-deployment
description: >
  Invoke orq.ai deployments, agents, and models via the Python SDK or HTTP API.
  Use when a user wants to call a deployment with prompt variables, invoke an
  agent in a conversation, or call a model directly through the AI Router.
  Do NOT use for creating or editing deployments/agents (use orq-improve-agent
  or orq-build-agent). Do NOT use for running evaluations (use orq-run-experiment).
allowed-tools: Bash(orq responses create:*), Bash(curl --fail-with-body -sS https://my.orq.ai/v3/router/responses), Bash(curl -s https://api.orq.ai/v2/*), Bash(curl -s -X POST https://api.orq.ai/v2/*), Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, mcp__orq-workspace__search_entities, mcp__orq-workspace__list_models, mcp__orq-workspace__list_traces
---

# Invoke Deployment

You are an **orq.ai integration engineer**. Your job is to help users invoke orq.ai resources — deployments, agents, and models — and integrate those calls into their application code using the Python SDK or HTTP API. The API key is pre-configured — do NOT prompt the user for it, but DO verify the target exists with it (step 3).

**Invocation policy (models and agents):** Default to `POST /v3/router/responses` or `orq responses create`. Set `model` to `provider/model-id` for a model or `agent/<key>` for a managed agent, and pass `input`. Use `openai/gpt-6-luna` for default model command examples unless the user specifies another model. Prefer Responses over Chat Completions; do not use v2 inference routes. Use another inference interface only when the user explicitly requests it. Legacy agent execution APIs remain deprecated. Never fall back to v2, Chat Completions, or legacy agent endpoints after a routing or model error. Use Responses for streaming and continuation too. Prefer the orq SDK for new code and when it is already imported. If orq is not imported and OpenAI already is, reuse OpenAI instead of adding a second SDK. Configuration, deployment, evaluator, and telemetry APIs retain their own contracts.

## Constraints

- **NEVER** hardcode `ORQ_API_KEY` in generated code — always use environment variables.
- **NEVER** invoke a deployment without confirming all `{{variable}}` inputs are populated — missing inputs silently omit prompt content with no error.
- **NEVER** skip `identity.id` in production calls — it links requests to contacts in orq.ai and enables per-user analytics and cost attribution.
- **ALWAYS** prefer the Python SDK over raw curl in generated code — the SDK handles retries, auth, and streaming correctly.
- **ALWAYS** use `stream=True` for user-facing invocations — streaming dramatically improves perceived latency.
- **ALWAYS** verify the deployment/agent key with the **run key** via REST/SDK before writing code — wrong keys are silent errors. Use `search_entities` to **browse** for keys, then verify with the run key (see [run-key preflight](../orq-shared/resources/run-key-preflight.md)).

**Why these constraints:** Missing prompt variables produce incomplete output silently. Hardcoded API keys are a security risk. Wrong keys waste budget. Skipping identity makes traces unattributable.

## Companion Skills

- `orq-improve-agent` — improve a deployment's prompt before invoking it
- `orq-build-agent` — create and configure an agent before invoking it
- `orq-run-experiment` — evaluate invocation quality across a dataset
- `orq-analyze-traces` — diagnose failures from invocation traces
- `orq-setup-observability` — instrument the application that calls the deployment
- **orq-cli** — the same platform operations from a shell, for anything that must run again without an agent present (CI, cron, scripts, bulk): auth via `ORQ_API_KEY`, `-o json` output. See its "MCP tools or the CLI?" table before choosing.

## When to use

- "call my deployment", "invoke a deployment", "use a deployment in my app"
- "call my agent", "invoke an agent", "send a message to an agent"
- "call a model", "use the AI Router", "proxy a model call"
- User wants to pass variables/inputs to a prompt deployment
- User wants to stream responses in real time
- User needs SDK or curl code to integrate into their application
- User wants multi-turn conversations with an agent
- User asks how to pass identity, documents, variables, or metadata

## When NOT to use

- **Need to create or edit a deployment/prompt?** → Use `orq-improve-agent`
- **Need to build or configure an agent?** → Use `orq-build-agent`
- **Need to evaluate quality?** → Use `orq-run-experiment`
- **Traces not appearing?** → Use `orq-setup-observability`

## Workflow Checklist

```
Invoke Progress:
- [ ] Phase 1: Discover — identify and verify the target resource (deployment / agent / model)
- [ ] Phase 2: Configure — determine inputs/variables, identity, and options
- [ ] Phase 3: Invoke — call the resource and verify the response
- [ ] Phase 4: Integrate — deliver production-ready code
```

## Done When

- Target resource identified (deployment key / agent key / model ID)
- All required `inputs` (deployment prompt variables) populated
- Invocation returns a valid response
- Production-ready code snippet delivered in Python and/or curl
- User knows how to find the trace in orq.ai

## Resources

- **API reference (MCP + HTTP):** See [resources/api-reference.md](resources/api-reference.md)

---

## orq.ai Documentation

**Deployments:** [Overview](https://docs.orq.ai/docs/deployments/overview) · [Invoke API](https://docs.orq.ai/reference/deployments/invoke) · [Stream API](https://docs.orq.ai/reference/deployments/stream) · [Get Config](https://docs.orq.ai/reference/deployments/get-config)

**Agents:** [Agent API](https://docs.orq.ai/docs/agents/agent-api) · [Create Response](https://docs.orq.ai/reference/responses/create-response)

**Models (AI Router):** [Getting Started](https://docs.orq.ai/docs/router/getting-started) · [OpenAI-Compatible API](https://docs.orq.ai/docs/proxy/openai-compatible-api) · [Supported Models](https://docs.orq.ai/docs/proxy/supported-models)

**SDKs:** [Install and usage](https://docs.orq.ai/reference/client-libraries) · [Python SDK](https://github.com/orq-ai/orq-python) · [Node.js SDK](https://github.com/orq-ai/orq-node)

### Key Concepts

- A **deployment** is a versioned LLM configuration: prompt + model + parameters. Invoke it with `inputs` to fill template `{{variables}}` and get a completion.
- An **agent** is a deployment with tools, memory, and knowledge bases. Invoke it for multi-turn conversations and tool-calling workflows.
- **Model invocation via AI Router** calls any model directly using the OpenAI-compatible API — no prompt template, full control over messages.
- **`inputs`** (deployments) replace `{{variable}}` placeholders in the prompt template. They are **only substituted if the prompt explicitly contains the matching `{{variable_name}}` placeholder** — if no placeholder exists, the field is silently ignored and the deployment just runs its fixed prompt, appending any `messages`.
- **`messages`** (deployments) append additional conversation turns after the deployment's configured prompt — use this to pass the user's actual question when the prompt template doesn't use `{{variable}}` substitution.
- **`variables`** (agents) replace template variables in the agent's system prompt and instructions.
- **`identity`** links requests to contacts in orq.ai — required `id`, optional `display_name`, `email`, `metadata`, `logo_url`, `tags`.
- **`stream=True`** enables server-sent events for real-time token delivery.
- **`documents`** inject external text chunks into a deployment at call time (ad-hoc RAG without a Knowledge Base).
- **`previous_response_id`** (agents) continues a stored conversation — save `id` from the previous response.

---

## Steps

Follow these steps **in order**. Do NOT skip steps.

### Phase 1: Discover the Target Resource

> **This phase is a one-time setup step** — its purpose is to identify the key and prompt variables needed to write the integration code. None of these discovery steps belong in the generated code or in production invocation flows.

1. **Identify what the user wants to invoke:**
   - **Deployment** — prompt template + model, versioned, invoke with `inputs` to fill variables
   - **Agent** — prompt + tools + memory + KB, multi-turn conversations via `responses.create`
   - **Model direct call** — OpenAI-compatible AI Router, no template

2. **Find the resource key** (agents and deployments only — skip for model direct calls) if the user doesn't already know it, using `search_entities` MCP tool to **browse**:
   - Deployments: `type: "deployment"`
   - Agents: `type: "agent"`

3. **Verify the key with the run key** (agents and deployments only — skip for model direct calls) — whether the key came from MCP browsing or the user provided it directly. Follow the [run-key preflight](../orq-shared/resources/run-key-preflight.md) using the `ORQ_API_KEY` the invocation will use. For deployments, save the `get_config` response — it contains the prompt template needed in step 4. If `get_config` returns 204, the deployment has no published version — stop and ask the user to publish it before proceeding.

4. **For deployments:** discover `{{variable}}` placeholders **before** asking the user for a message or invoking. Use the `get_config` response from step 3 — it already contains the prompt template.

   Scan the returned prompt template for `{{variable_name}}` patterns. These are the required `inputs` keys.

   If the template is empty or has no variables, ask the user: *"Does this deployment use any `{{variable}}` placeholders? If so, what are they?"*

   Then identify which invocation pattern applies:
   - **Variable substitution** — the prompt contains `{{variable}}` placeholders → pass values via `inputs`
   - **Message appending** — the prompt has no variables → pass the user's question via `messages: [{role: "user", content: "..."}]`
   - **Mixed** — some variables in the template AND a dynamic user message → use both `inputs` and `messages`

   **Do not ask the user for a message and do not invoke until you have confirmed the variable pattern.** Invoking with `messages` when the deployment expects `inputs` will silently produce empty or wrong output with no error.

   `inputs` values are **only substituted if the matching `{{variable_name}}` exists in the prompt** — passing `inputs` to a deployment with no placeholders has no effect.

### Phase 2: Configure the Invocation

5. **For deployments — determine the invocation pattern.**

   | Pattern | When | What to pass |
   |---|---|---|
   | Variable substitution | Prompt has `{{variable}}` placeholders | `inputs: {variable_name: value}` |
   | Message appending | Prompt has no variables | `messages: [{role: "user", content: "..."}]` |
   | Mixed | Prompt has variables AND needs user input | Both `inputs` and `messages` |

   For each `{{variable}}` in the prompt, confirm the value to pass:

   | Prompt variable | `inputs` key | Example |
   |---|---|---|
   | `{{customer_name}}` | `customer_name` | `"Jane Doe"` |
   | `{{issue}}` | `issue` | `"Payment failed"` |

6. **Determine `identity`** (deployments and agents).

   Always include at minimum `id` in production:
   ```json
   { "id": "user_<unique_id>", "display_name": "Jane Doe", "email": "jane@example.com" }
   ```

7. **Choose streaming vs. non-streaming.**

   | Use case | Mode |
   |---|---|
   | User-facing UI, chatbot | `stream=True` |
   | Background job, batch, eval | `stream=False` |

8. **Determine additional options as needed.**

   | Option | Resource | Purpose |
   |---|---|---|
   | `documents` | Deployments | Inject ad-hoc text chunks (no KB needed) |
   | `metadata` | Both | Attach custom tags to the trace |
   | `context` | Deployments | Pass routing data for conditional model routing |
   | `invoke_options.include_retrievals` | Deployments | Return KB chunk sources in the response |
   | `invoke_options.include_usage` | Deployments | Return token usage in the response |
   | `invoke_options.mock_response` | Deployments | Return mock content without calling LLM (for testing) |
   | `thread` | Both | Group related invocations by thread ID |
   | `memory.entity_id` | Agents | Associate memory stores with a specific user/session |
   | `background=True` | Agents | Return an asynchronous response; poll its response ID until completed |
   | `variables` | Agents | Replace template variables in system prompt/instructions |
   | `knowledge_filter` | Deployments | Filter KB chunks by metadata (eq, ne, gt, in, etc.) |

### Phase 3: Invoke

9. **Invoke the resource.** See [resources/api-reference.md](resources/api-reference.md) for full API details.

10. **Verify the response:**
   - Deployment: check `choices[0].message.content` for the output text
   - Agent: read Responses message `output[].content[]` text (OpenAI SDK: `response.output_text`); check `status` and save `response.id` for `previous_response_id` continuation
   - If wrong output: check for missing inputs, wrong key, or prompt issues

11. **Find the trace** — use telemetry if the response exposes it; otherwise find the invocation with `orq traces search`. Do not assume a general Responses/OpenAI SDK object has the deployment SDK's `telemetry` property. Hand the user a link built from the trace ID: `<host>/<workspace-key>/traces/(trace:<trace_id>)`, or add `//span:<telemetry.span_id>` inside the parentheses to open the call's own span. These telemetry ids have not been checked against the route; if the link opens nothing, look the trace up with `orq traces search` and use its `trace_id`. Host, workspace key and pitfalls: [`orq-cli` → Building app URLs](../orq-cli/resources/command-map.md#building-app-urls). If it opens on "No spans match your search", the user's Traces project selector is filtering it out: switch it to **All projects**.

### Phase 4: Generate Integration Code

12. **Ask for the user's language** if not already clear: Python or curl.

13. **Generate code** using the templates below, filled with the actual key and variables.

---

## Code Templates

One Python SDK example and one curl example per invocation type. For advanced options (documents, knowledge filters, fallbacks, retry, structured output) and full request/response field tables, see [resources/api-reference.md](resources/api-reference.md).

### Deployment — Python SDK

```python
import os
from orq_ai_sdk import Orq

client = Orq(api_key=os.environ["ORQ_API_KEY"])

# Pattern 1: variable substitution
# Use when the prompt template contains {{variable}} placeholders.
# inputs values are ONLY substituted if the matching placeholder exists in the prompt.
response = client.deployments.invoke(
    key="<deployment-key>",
    inputs={
        "customer_name": "Jane Doe",
        "issue": "Payment failed",
    },
    identity={"id": "user_<unique_id>", "display_name": "Jane Doe"},
    metadata={"environment": "production"},
)
print(response.choices[0].message.content)

# Pattern 2: message appending
# Use when the prompt has no {{variable}} placeholders — pass the user's question via messages.
response = client.deployments.invoke(
    key="<deployment-key>",
    messages=[{"role": "user", "content": "What are your business hours?"}],
    identity={"id": "user_<unique_id>"},
)
print(response.choices[0].message.content)

# Pattern 3: mixed — variables + user message
response = client.deployments.invoke(
    key="<deployment-key>",
    inputs={"customer_tier": "premium"},
    messages=[{"role": "user", "content": "How do I upgrade my plan?"}],
    identity={"id": "user_<unique_id>"},
)
print(response.choices[0].message.content)

# Streaming (works with any pattern above)
response = client.deployments.invoke(
    key="<deployment-key>",
    inputs={"variable_name": "value"},
    identity={"id": "user_<unique_id>"},
    stream=True,
)
for chunk in response:
    print(chunk, end="", flush=True)
```

### Deployment — curl

```bash
# Pattern 1: variable substitution (prompt has {{variable}} placeholders)
curl -s -X POST https://api.orq.ai/v2/deployments/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<deployment-key>",
    "inputs": {"customer_name": "Jane Doe", "issue": "Payment failed"},
    "identity": {"id": "user_<unique_id>", "display_name": "Jane Doe"},
    "metadata": {"environment": "production"}
  }' | jq

# Pattern 2: message appending (prompt has no {{variable}} placeholders)
curl -s -X POST https://api.orq.ai/v2/deployments/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<deployment-key>",
    "messages": [{"role": "user", "content": "What are your business hours?"}],
    "identity": {"id": "user_<unique_id>"}
  }' | jq

# Pattern 3: mixed — variables + user message
curl -s -X POST https://api.orq.ai/v2/deployments/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<deployment-key>",
    "inputs": {"customer_tier": "premium"},
    "messages": [{"role": "user", "content": "How do I upgrade my plan?"}],
    "identity": {"id": "user_<unique_id>"}
  }' | jq
```

### Agent — Python orq SDK (preferred)

Prefer the orq SDK for new code and when it is already imported. If orq is not imported and OpenAI already is, reuse OpenAI instead of adding a second SDK.

```python
import os
from orq_ai_sdk import Orq

with Orq(api_key=os.environ["ORQ_API_KEY"], server_url="https://my.orq.ai") as orq:
    response = orq.responses.create(model="agent/<agent-key>", input="Hello!", stream=False)
    if response.status != "completed":
        raise RuntimeError(f"Agent response {response.id}: {response.status}")
    text = "".join(
        part["text"]
        for item in response.output or [] if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") == "output_text"
    )
    print(text)
    follow_up = orq.responses.create(
        model="agent/<agent-key>", previous_response_id=response.id, input="Tell me more.", stream=False,
    )
    print(follow_up)
```

For async Python code, use `async with Orq(...) as orq` and `await orq.responses.create_async(...)`; see the [paired async examples](../orq-compare-agents/resources/job-patterns.md#orqai-agent).

### Agent — Python OpenAI SDK (already imported)

```python
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["ORQ_API_KEY"], base_url="https://my.orq.ai/v3/router")
response = client.responses.create(model="agent/<agent-key>", input="Hello, can you help me?")
if response.status != "completed":
    raise RuntimeError(f"Agent response {response.id}: {response.status}")
print(response.output_text)

follow_up = client.responses.create(
    model="agent/<agent-key>", previous_response_id=response.id, input="Tell me more."
)
print(follow_up.output_text)

# User-facing streaming stays on the same endpoint
stream = client.responses.create(model="agent/<agent-key>", input="Hello!", stream=True)
for event in stream:
    if event.type == "response.output_text.delta":
        print(event.delta, end="", flush=True)
```

### Agent — CLI and HTTP

```bash
orq responses create --model "agent/<agent-key>" --input "Hello, can you help me?" -o json

curl --fail-with-body -sS https://my.orq.ai/v3/router/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"agent/<agent-key>","input":"Hello, can you help me?","identity":{"id":"synthetic-user"}}'
```

### Agent — Node.js orq SDK (preferred)

```typescript
import { Orq } from "@orq-ai/node";

const orq = new Orq({ apiKey: process.env.ORQ_API_KEY, serverURL: "https://my.orq.ai" });
const response = await orq.responses.create({ model: "agent/<agent-key>", input: "Hello!", stream: false });
if (!("output" in response) || response.status !== "completed") {
  throw new Error("Expected a completed non-streaming response");
}
const text = (response.output ?? [])
  .filter(item => item.type === "message")
  .flatMap(item => item.content ?? [])
  .filter(part => part.type === "output_text")
  .map(part => part.text).join("");
console.log(text);
const followUp = await orq.responses.create({
  model: "agent/<agent-key>", previousResponseId: response.id, input: "Tell me more.", stream: false,
});
console.log(followUp);
```

### Agent — Node.js OpenAI SDK (already imported)

```typescript
import OpenAI from "openai";

const client = new OpenAI({ apiKey: process.env.ORQ_API_KEY, baseURL: "https://my.orq.ai/v3/router" });
const response = await client.responses.create({ model: "agent/<agent-key>", input: "Hello!" });
if (response.status !== "completed") throw new Error(`Agent response ${response.id}: ${response.status}`);
console.log(response.output_text);
const followUp = await client.responses.create({
  model: "agent/<agent-key>", previous_response_id: response.id, input: "Tell me more.",
});
console.log(followUp.output_text);
```

Install `orq-ai-sdk` (Python) or `@orq-ai/node` (Node.js) for the preferred SDK. Reuse `openai` when it is already imported and orq is not. The orq SDK takes the host (`server_url` / `serverURL`); OpenAI takes the router base URL ending in `/v3/router`. Both use general Responses. For production attribution, pass `identity` directly with orq, or use OpenAI Python `extra_body={"identity": {"id": "<user-id>"}}`. Keep `store: true` for continuation. See the [agent contract](resources/api-reference.md#agents) for parsing and streaming events.

### Model (AI Router) — Python orq SDK (preferred)

```python
import os
from orq_ai_sdk import Orq

with Orq(api_key=os.environ["ORQ_API_KEY"], server_url="https://my.orq.ai") as orq:
    response = orq.responses.create(
        model="openai/gpt-6-luna", instructions="You are a helpful assistant.",
        input="What is the capital of France?", stream=False,
    )
    if response.status != "completed":
        raise RuntimeError(f"Model response {response.id}: {response.status}")
    text = "".join(
        part["text"]
        for item in response.output or [] if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") == "output_text"
    )
    print(text)
```

### Model (AI Router) — Python OpenAI SDK (already imported)

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["ORQ_API_KEY"],
    base_url="https://my.orq.ai/v3/router",
)

response = client.responses.create(
    model="openai/gpt-6-luna",   # provider/model format; agent/<key> for managed agents
    instructions="You are a helpful assistant.",
    input="What is the capital of France?",
)
if response.status != "completed":
    raise RuntimeError(f"Model response {response.id}: {response.status}")
print(response.output_text)
```

### Model (AI Router) — CLI and curl

```bash
orq responses create --model "openai/gpt-6-luna" --input "What is the capital of France?" -o json

curl --fail-with-body -sS https://my.orq.ai/v3/router/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-6-luna","instructions":"You are a helpful assistant.","input":"What is the capital of France?"}'
```

For TypeScript model calls, use either Node.js SDK example above with `model: "openai/gpt-6-luna"`.

For either models or agents, stream with `stream: true` on this same endpoint and continue with `previous_response_id`. The CLI SSE decoding caveat in `orq-cli` applies to both.

---

## Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Invoking a deployment without `inputs` when prompt has `{{variables}}` | Always find and pass every `{{variable}}` in the prompt — missing ones silently omit content |
| Passing `inputs` to a deployment that has no `{{variable}}` placeholders | `inputs` are silently ignored if the placeholder doesn't exist — use `messages` to append the user's question instead |
| Hardcoding `ORQ_API_KEY` in source code | Use `os.environ["ORQ_API_KEY"]` / `process.env.ORQ_API_KEY` |
| Using legacy A2A `message.parts` for agents | Use Responses `input`, as plain text or input message items |
| Skipping `identity.id` in production | Always pass identity — enables per-user analytics and cost attribution |
| Using `stream=False` for user-facing UI | Use `stream=True` — streaming shows tokens in real time |
| Not saving the response ID for agent multi-turn | Store `response.id` and pass it as `previous_response_id` |
| Using model name without provider prefix | Use `openai/gpt-6-luna`, `anthropic/claude-sonnet-4-5` — not just `gpt-6-luna` |
| Not checking the trace after first invocation | Link the trace from `response.telemetry.trace_id` (step 11) and verify variable substitution and token counts |
| Using `contact` field in agents | Use `identity` instead — `contact` is deprecated |

## Open in orq.ai

After completing this skill, direct the user to:
- **Deployments:** [my.orq.ai](https://my.orq.ai/) → Deployments — review configuration and versions
- **Agents:** [my.orq.ai](https://my.orq.ai/) → Agents — review agent config and tools
- **Traces:** a direct link to the invocation's trace (step 11), or [my.orq.ai](https://my.orq.ai/) → Traces — inspect invocations, token usage, latency
- **Analytics:** [my.orq.ai](https://my.orq.ai/) → Analytics — per-deployment/agent cost and volume

When this skill conflicts with live API responses or docs.orq.ai, trust the API.
