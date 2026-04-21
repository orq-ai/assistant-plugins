---
name: invoke-deployment
description: >
  Invoke orq.ai deployments, agents, and models via the Python SDK or HTTP API.
  Use when a user wants to call a deployment with prompt variables, invoke an
  agent in a conversation, or call a model directly through the AI Router.
  Do NOT use for creating or editing deployments/agents (use optimize-prompt
  or build-agent). Do NOT use for running evaluations (use run-experiment).
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Invoke Deployment

You are an **orq.ai integration engineer**. Your job is to help users invoke orq.ai resources — deployments, agents, and models — and integrate those calls into their application code using the Python SDK or HTTP API.

## Constraints

- **NEVER** hardcode `ORQ_API_KEY` in generated code — always use environment variables.
- **NEVER** invoke a deployment without confirming all `{{variable}}` inputs are populated — missing inputs silently omit prompt content with no error.
- **NEVER** skip `identity.id` in production calls — it links requests to contacts in orq.ai and enables per-user analytics and cost attribution.
- **ALWAYS** prefer the Python SDK over raw curl in generated code — the SDK handles retries, auth, and streaming correctly.
- **ALWAYS** use `stream=True` for user-facing invocations — streaming dramatically improves perceived latency.
- **ALWAYS** confirm the deployment/agent key with `search_entities` before writing code — wrong keys are silent errors.

**Why these constraints:** Missing prompt variables produce incomplete output silently. Hardcoded API keys are a security risk. Wrong keys waste budget. Skipping identity makes traces unattributable.

## Companion Skills

- `optimize-prompt` — improve a deployment's prompt before invoking it
- `build-agent` — create and configure an agent before invoking it
- `run-experiment` — evaluate invocation quality across a dataset
- `analyze-trace-failures` — diagnose failures from invocation traces
- `setup-observability` — instrument the application that calls the deployment

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

- **Need to create or edit a deployment/prompt?** → Use `optimize-prompt`
- **Need to build or configure an agent?** → Use `build-agent`
- **Need to evaluate quality?** → Use `run-experiment`
- **Traces not appearing?** → Use `setup-observability`

## Workflow Checklist

```
Invoke Progress:
- [ ] Phase 1: Discover — identify the target resource (deployment / agent / model)
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

**Deployments:** [Overview](https://docs.orq.ai/docs/deployments/overview) · [Invoke API](https://docs.orq.ai/reference/deployments/invoke-deployment) · [Stream API](https://docs.orq.ai/reference/deployments/stream-deployment) · [Get Config](https://docs.orq.ai/reference/deployments/get-deployment-config)

**Agents:** [Agent API](https://docs.orq.ai/docs/agents/agent-api) · [Create Response](https://docs.orq.ai/reference/agents/create-response)

**Models (AI Router):** [Getting Started](https://docs.orq.ai/docs/router/getting-started) · [OpenAI-Compatible API](https://docs.orq.ai/docs/proxy/openai-compatible-api) · [Supported Models](https://docs.orq.ai/docs/proxy/supported-models)

**SDKs:** [Python SDK](https://docs.orq.ai/docs/sdk/python) · [Node.js SDK](https://docs.orq.ai/docs/sdk/node)

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
- **`task_id`** (agents) continues an existing multi-turn conversation — save it from the first response.

---

## Steps

Follow these steps **in order**. Do NOT skip steps.

### Phase 1: Discover the Target Resource

> **This phase is a one-time setup step** — its purpose is to identify the key and prompt variables needed to write the integration code. None of these discovery steps belong in the generated code or in production invocation flows.

1. **Identify what the user wants to invoke:**
   - **Deployment** — prompt template + model, versioned, invoke with `inputs` to fill variables
   - **Agent** — prompt + tools + memory + KB, multi-turn conversations via `responses.create`
   - **Model direct call** — OpenAI-compatible AI Router, no template

2. **Find the resource key** if the user doesn't already know it, using `search_entities` MCP tool:
   - Deployments: `type: "deployment"`
   - Agents: `type: "agent"`

   If the user already knows the key, skip directly to step 3.

3. **For deployments:** inspect the prompt template and identify which invocation pattern applies:
   - **Variable substitution** — the prompt contains `{{variable}}` placeholders → pass values via `inputs`
   - **Message appending** — the prompt has no variables → pass the user's question via `messages: [{role: "user", content: "..."}]`
   - **Mixed** — some variables in the template AND a dynamic user message → use both `inputs` and `messages`

   `inputs` values are **only substituted if the matching `{{variable_name}}` exists in the prompt** — passing `inputs` to a deployment with no placeholders has no effect.

### Phase 2: Configure the Invocation

4. **For deployments — determine the invocation pattern.**

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

5. **Determine `identity`** (deployments and agents).

   Always include at minimum `id` in production:
   ```json
   { "id": "user_<unique_id>", "display_name": "Jane Doe", "email": "jane@example.com" }
   ```

6. **Choose streaming vs. non-streaming.**

   | Use case | Mode |
   |---|---|
   | User-facing UI, chatbot | `stream=True` |
   | Background job, batch, eval | `stream=False` |

7. **Determine additional options as needed.**

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
   | `background=True` | Agents | Return immediately with task ID (async execution) |
   | `variables` | Agents | Replace template variables in system prompt/instructions |
   | `knowledge_filter` | Deployments | Filter KB chunks by metadata (eq, ne, gt, in, etc.) |

### Phase 3: Invoke

8. **Invoke the resource.** See [resources/api-reference.md](resources/api-reference.md) for full API details.

9. **Verify the response:**
   - Deployment: check `choices[0].message.content` for the output text
   - Agent: check `response.output[0].parts[0].text` for the output text; save `response.task_id` for multi-turn
   - If wrong output: check for missing inputs, wrong key, or prompt issues

10. **Find the trace** — direct user to [my.orq.ai](https://my.orq.ai/) → Traces, or use `response.telemetry.trace_id`.

### Phase 4: Generate Integration Code

11. **Ask for the user's language** if not already clear: Python or curl.

12. **Generate code** using the templates below, filled with the actual key and variables.

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

### Agent — Python SDK

```python
import os
from orq_ai_sdk import Orq

client = Orq(api_key=os.environ["ORQ_API_KEY"])

# Single turn — note: agents use parts format, NOT OpenAI-style content
response = client.agents.responses.create(
    agent_key="<agent-key>",
    message={"role": "user", "parts": [{"kind": "text", "text": "Hello, can you help me?"}]},
    identity={"id": "user_<unique_id>", "display_name": "Jane Doe"},
)
print(response.output[0].parts[0].text)

# Multi-turn: save task_id and pass it in follow-ups
task_id = response.task_id
follow_up = client.agents.responses.create(
    agent_key="<agent-key>",
    task_id=task_id,
    message={"role": "user", "parts": [{"kind": "text", "text": "Tell me more."}]},
)
print(follow_up.output[0].parts[0].text)
```

### Agent — curl

```bash
curl -s -X POST https://api.orq.ai/v2/agents/<agent-key>/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "Hello, can you help me?"}]
    },
    "identity": {"id": "user_<unique_id>", "display_name": "Jane Doe"}
  }' | jq
```

### Agent — Node.js SDK

```typescript
import { Orq } from "@orq-ai/node";

const client = new Orq({ apiKey: process.env.ORQ_API_KEY });

const response = await client.agents.responses.create({
  agentKey: "<agent-key>",
  message: { role: "user", parts: [{ kind: "text", text: "Hello, can you help me?" }] },
  identity: { id: "user_<unique_id>", displayName: "Jane Doe" },
});
console.log(response.output[0].parts[0].text);

// Multi-turn
const followUp = await client.agents.responses.create({
  agentKey: "<agent-key>",
  taskId: response.taskId,
  message: { role: "user", parts: [{ kind: "text", text: "Tell me more." }] },
});
console.log(followUp.output[0].parts[0].text);
```

### Model (AI Router) — Python SDK

Uses the `openai` library pointed at orq.ai — no orq SDK needed:

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["ORQ_API_KEY"],
    base_url="https://api.orq.ai/v2/router",
)

response = client.chat.completions.create(
    model="openai/gpt-4.1",   # always use provider/model format
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"},
    ],
)
print(response.choices[0].message.content)
```

### Model (AI Router) — curl

```bash
curl -s -X POST https://api.orq.ai/v2/router/chat/completions \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4.1",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is the capital of France?"}
    ]
  }' | jq
```

---

## Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Invoking a deployment without `inputs` when prompt has `{{variables}}` | Always find and pass every `{{variable}}` in the prompt — missing ones silently omit content |
| Passing `inputs` to a deployment that has no `{{variable}}` placeholders | `inputs` are silently ignored if the placeholder doesn't exist — use `messages` to append the user's question instead |
| Hardcoding `ORQ_API_KEY` in source code | Use `os.environ["ORQ_API_KEY"]` / `process.env.ORQ_API_KEY` |
| Using OpenAI message format for agents (`{"role": "user", "content": "..."}`) | Use A2A parts format: `{"role": "user", "parts": [{"kind": "text", "text": "..."}]}` |
| Skipping `identity.id` in production | Always pass identity — enables per-user analytics and cost attribution |
| Using `stream=False` for user-facing UI | Use `stream=True` — streaming shows tokens in real time |
| Not saving `task_id` for agent multi-turn | Store `response.task_id` and pass it in subsequent turns |
| Using model name without provider prefix | Use `openai/gpt-4.1`, `anthropic/claude-sonnet-4-5` — not just `gpt-4.1` |
| Not checking the trace after first invocation | Use `response.telemetry.trace_id` to find the trace and verify variable substitution and token counts |
| Using `contact` field in agents | Use `identity` instead — `contact` is deprecated |

## Open in orq.ai

After completing this skill, direct the user to:
- **Deployments:** [my.orq.ai](https://my.orq.ai/) → Deployments — review configuration and versions
- **Agents:** [my.orq.ai](https://my.orq.ai/) → Agents — review agent config and tools
- **Traces:** [my.orq.ai](https://my.orq.ai/) → Traces — inspect invocations, token usage, latency
- **Analytics:** [my.orq.ai](https://my.orq.ai/) → Analytics — per-deployment/agent cost and volume

## Documentation & Resolution

When you need to look up orq.ai platform details, check in this order:

1. **orq MCP tools** — query live data first (`search_entities` with `type: "deployment"` or `"agent"`); API responses are always authoritative
2. **orq.ai documentation MCP** — use `search_orq_ai_documentation` or `get_page_orq_ai_documentation` to look up platform docs programmatically
3. **[docs.orq.ai](https://docs.orq.ai)** — browse official documentation directly
4. **This skill file** — may lag behind API or docs changes

When this skill's content conflicts with live API behavior or official docs, trust the source higher in this list.
