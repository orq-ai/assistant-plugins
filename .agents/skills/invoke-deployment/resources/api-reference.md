# orq.ai API Reference for Deployment Invocation

MCP tools and HTTP API endpoints used by the invoke-deployment skill.

> **Official documentation:** [Deployments](https://docs.orq.ai/docs/deployments/overview) · [Agents API](https://docs.orq.ai/docs/agents/agent-api) · [AI Router](https://docs.orq.ai/docs/router/getting-started)

## Contents

- MCP tools
- HTTP API: deployments, agents, models (AI Router)

## MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface.

| Tool | Purpose |
|------|---------|
| `search_entities` | Find deployments (`type: "deployment"`), agents (`type: "agent"`) |
| `list_models` | List available models for AI Router calls |
| `list_registry_keys` | List deployment registry keys |
| `list_traces` | Inspect traces after invocation |

## HTTP API

All endpoints require `Authorization: Bearer $ORQ_API_KEY`.

---

## Deployments

### Invoke (non-streaming and streaming)

**Endpoint:** `POST https://api.orq.ai/v2/deployments/invoke`

**Required fields:** `key`

**Request body:**

| Field | Type | Description |
|---|---|---|
| `key` | string | Deployment key to invoke |
| `stream` | boolean | If `true`, returns SSE stream (default: `false`) |
| `inputs` | object | Key-value pairs to replace `{{variable}}` placeholders in the prompt |
| `context` | object | Key-value pairs for deployment routing configuration |
| `messages` | array | Additional messages appended to the configured conversation |
| `prefix_messages` | array | Messages inserted after System but before configured User/Assistant pairs |
| `identity.id` | string (required) | Unique contact identifier |
| `identity.display_name` | string | Contact display name |
| `identity.email` | string | Contact email |
| `identity.metadata` | array | Additional contact key-value metadata |
| `identity.logo_url` | string | URL to contact avatar |
| `identity.tags` | array | Contact tags |
| `file_ids` | array | File IDs associated with the request |
| `metadata` | object | Key-value pairs attached to the trace log |
| `extra_params` | object | Additional model provider parameters (overrides deployment config) |
| `documents` | array | Ad-hoc context chunks: `[{text, metadata: {file_name, file_type, page_number}}]` |
| `invoke_options.include_retrievals` | boolean | Include KB chunk sources in response (default: `false`) |
| `invoke_options.include_usage` | boolean | Include token usage in response (default: `false`) |
| `invoke_options.mock_response` | string | Return mock content without calling LLM (for testing) |
| `thread.id` | string | Thread ID to group related invocations |
| `thread.tags` | array | Thread tags |
| `knowledge_filter` | object | Metadata filter for KB chunks: `{field: {eq/ne/gt/gte/lt/lte/in/nin/exists: value}}` |

**Response fields:**

| Field | Description |
|---|---|
| `id` | Unique response identifier (ULID) |
| `created` | ISO timestamp |
| `object` | `"chat"`, `"completion"`, or `"image"` |
| `model` | Model used |
| `provider` | Provider used |
| `is_final` | Whether this is the final response chunk (streaming) |
| `telemetry.trace_id` | Trace ID — use to find the trace in orq.ai |
| `telemetry.span_id` | Span ID |
| `choices[].message.content` | Generated text content |
| `choices[].message.tool_calls` | Tool call requests (if model used tools) |
| `choices[].finish_reason` | `stop`, `length`, `tool_calls`, `content_filter` |
| `usage.total_tokens` | Total tokens (requires `invoke_options.include_usage: true`) |
| `usage.prompt_tokens` | Prompt tokens |
| `usage.completion_tokens` | Completion tokens |
| `retrievals` | KB chunks retrieved (requires `invoke_options.include_retrievals: true`) |

```bash
# Non-streaming
curl -s -X POST https://api.orq.ai/v2/deployments/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<deployment-key>",
    "inputs": {"variable_name": "value"},
    "identity": {"id": "user_<unique_id>", "display_name": "Jane Doe"},
    "metadata": {"environment": "production"},
    "invoke_options": {"include_usage": true}
  }' | jq

# Streaming (stream: true in the body)
curl -s -X POST https://api.orq.ai/v2/deployments/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<deployment-key>",
    "inputs": {"variable_name": "value"},
    "identity": {"id": "user_<unique_id>"},
    "stream": true
  }'

# With documents (ad-hoc RAG)
curl -s -X POST https://api.orq.ai/v2/deployments/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<deployment-key>",
    "inputs": {"query": "How do I get a refund?"},
    "identity": {"id": "user_<unique_id>"},
    "documents": [
      {
        "text": "Refunds are processed within 5 business days.",
        "metadata": {"file_name": "policy.pdf", "file_type": "application/pdf", "page_number": 3}
      }
    ],
    "invoke_options": {"include_retrievals": true, "include_usage": true}
  }' | jq

# With knowledge filter
curl -s -X POST https://api.orq.ai/v2/deployments/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<deployment-key>",
    "inputs": {"query": "pricing"},
    "identity": {"id": "user_<unique_id>"},
    "knowledge_filter": {"topic": {"eq": "pricing"}, "region": {"in": ["us", "eu"]}}
  }' | jq

# List deployments
curl -s https://api.orq.ai/v2/deployments \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq
```

---

## Agents

### Create Response (invoke agent)

**Endpoint:** `POST https://api.orq.ai/v2/agents/{agent_key}/responses`

**Required fields:** `message`

**Request body:**

| Field | Type | Description |
|---|---|---|
| `message.role` | string | `"user"` or `"tool"` |
| `message.parts` | array | `[{kind: "text", text: "..."}]` for text; `[{kind: "file", file: {bytes/uri}}]` for files |
| `task_id` | string | Continue an existing conversation (from previous response's `task_id`) |
| `variables` | object | Replace `{{variable}}` placeholders in system prompt/instructions |
| `identity.id` | string (required) | Unique contact identifier |
| `identity.display_name` | string | Contact display name |
| `identity.email` | string | Contact email |
| `thread.id` | string | Thread ID to group related requests |
| `thread.tags` | array | Thread tags |
| `memory.entity_id` | string | Associate memory stores with a specific user/session |
| `metadata` | object | Key-value pairs included in trace |
| `stream` | boolean | Return SSE stream (default: `false`) |
| `background` | boolean | Return immediately with `task_id` (async, default: `false`) |
| `engine` | string | Override template engine: `text`, `jinja`, or `mustache` |

**Response fields:**

| Field | Description |
|---|---|
| `_id` | Unique response ID |
| `task_id` | Agent execution task ID — **save this for multi-turn** |
| `output[].role` | Message role (`agent`, `tool`, etc.) |
| `output[0].parts[0].text` | Generated text content |
| `model` | Model used |
| `usage.total_tokens` | Total tokens used |
| `usage.prompt_tokens` | Prompt tokens |
| `usage.completion_tokens` | Completion tokens |
| `finish_reason` | `stop`, `length`, `tool_calls`, `max_iterations`, `max_time` |
| `pending_tool_calls` | Tool calls awaiting user response (when `finish_reason` is `function_call`) |
| `telemetry.trace_id` | Trace ID — use to find the trace in orq.ai |

**Streaming events** (when `stream: true`):

| Event type | Payload |
|---|---|
| `response.started` | `{responseId, taskId, model, workflowRunId}` |
| `part.delta` | `{partId, delta: {kind: "text", text: "chunk"}}` — text token |
| `part.done` | `{partId, part}` — part fully streamed |
| `tool.started` | `{toolId, toolName, toolCallId, arguments}` |
| `tool.done` | `{toolId, toolCallId, result}` |
| `tool.failed` | `{toolId, toolCallId, error}` |
| `tool.review.requested` | `{toolId, toolCallId, requiresApproval}` — human-in-loop |
| `response.done` | `{finishReason, usage, pendingToolCalls}` |
| `response.failed` | `{error, code}` |

```bash
# Single turn
curl -s -X POST https://api.orq.ai/v2/agents/<agent-key>/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "Hello, can you help me?"}]
    },
    "identity": {"id": "user_<unique_id>", "display_name": "Jane Doe", "email": "jane@example.com"}
  }' | jq

# Multi-turn (pass task_id from previous response)
curl -s -X POST https://api.orq.ai/v2/agents/<agent-key>/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "<task_id_from_previous>",
    "message": {"role": "user", "parts": [{"kind": "text", "text": "Tell me more."}]}
  }' | jq

# With template variables (replaces {{variable}} in system prompt)
curl -s -X POST https://api.orq.ai/v2/agents/<agent-key>/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "message": {"role": "user", "parts": [{"kind": "text", "text": "Help me."}]},
    "variables": {"customer_tier": "premium", "language": "English"},
    "identity": {"id": "user_<unique_id>"}
  }' | jq

# Streaming
curl -s -X POST https://api.orq.ai/v2/agents/<agent-key>/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "message": {"role": "user", "parts": [{"kind": "text", "text": "Hello!"}]},
    "identity": {"id": "user_<unique_id>"},
    "stream": true
  }'

# List agents
curl -s https://api.orq.ai/v2/agents \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq

# Get agent details
curl -s https://api.orq.ai/v2/agents/<agent-key> \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq
```

---

## Models (AI Router)

### Create Chat Completion

**Endpoint:** `POST https://api.orq.ai/v2/router/chat/completions`

**Required fields:** `messages`, `model`

**Model format:** Always `provider/model-id` — e.g., `openai/gpt-4.1`, `anthropic/claude-sonnet-4-5`, `google-ai/gemini-2.5-pro`

**Key request fields:**

| Field | Description |
|---|---|
| `model` | Model in `provider/model-id` format |
| `messages` | Array of `{role, content}` — roles: `system`, `developer`, `user`, `assistant`, `tool` |
| `stream` | Enable SSE streaming (default: `false`) |
| `variables` | Template variable substitution in messages using `{{variableName}}` syntax |
| `temperature` | Sampling temperature 0–2 |
| `max_completion_tokens` | Max output tokens |
| `response_format` | `{type: "text"}`, `{type: "json_object"}`, or `{type: "json_schema", json_schema: {...}}` |
| `tools` | Function definitions for tool calling |
| `tool_choice` | `"none"`, `"auto"`, `"required"`, or `{type: "function", function: {name}}` |
| `thinking` | Extended thinking: `{type: "enabled", budget_tokens: 1024}` or `{type: "adaptive"}` |
| `reasoning_effort` | For reasoning models: `"none"`, `"low"`, `"medium"`, `"high"`, `"xhigh"` |
| `guardrails` | Content safety: `[{id: "orq_pii_detection", execute_on: "input"}]` |
| `fallbacks` | Fallback models: `[{model: "anthropic/claude-sonnet-4-5"}]` |
| `retry` | `{count: 3, on_codes: [429, 500, 502, 503, 504]}` |
| `cache` | `{type: "exact_match", ttl: 3600}` (max 3 days) |
| `load_balancer` | `{type: "weight_based", models: [{model: "...", weight: 0.7}, ...]}` |
| `timeout` | `{call_timeout: 30000}` (milliseconds) |
| `metadata` | Key-value pairs for tracing (string values, max 512 chars) |

**Response:** Standard OpenAI-compatible response. Access text via `response.choices[0].message.content`.

```bash
# Basic call
curl -s -X POST https://api.orq.ai/v2/router/chat/completions \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4.1",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is the capital of France?"}
    ]
  }' | jq '.choices[0].message.content'

# With variables (template substitution)
curl -s -X POST https://api.orq.ai/v2/router/chat/completions \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4.1",
    "messages": [
      {"role": "system", "content": "You assist {{customer_name}}."},
      {"role": "user", "content": "{{user_question}}"}
    ],
    "variables": {"customer_name": "Jane Doe", "user_question": "How do I reset my password?"}
  }' | jq

# With reliability features (fallback + retry)
curl -s -X POST https://api.orq.ai/v2/router/chat/completions \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4.1",
    "messages": [{"role": "user", "content": "Summarize this: ..."}],
    "fallbacks": [{"model": "anthropic/claude-sonnet-4-5"}, {"model": "google-ai/gemini-2.5-flash"}],
    "retry": {"count": 3, "on_codes": [429, 500, 502, 503]},
    "timeout": {"call_timeout": 30000}
  }' | jq

# Structured JSON output
curl -s -X POST https://api.orq.ai/v2/router/chat/completions \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4.1",
    "messages": [{"role": "user", "content": "Extract name and email from: John Doe, john@example.com"}],
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "contact",
        "schema": {
          "type": "object",
          "properties": {"name": {"type": "string"}, "email": {"type": "string"}},
          "required": ["name", "email"]
        },
        "strict": true
      }
    }
  }' | jq

# Streaming
curl -s -X POST https://api.orq.ai/v2/router/chat/completions \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "anthropic/claude-sonnet-4-5",
    "messages": [{"role": "user", "content": "Write me a haiku."}],
    "stream": true
  }'
```
