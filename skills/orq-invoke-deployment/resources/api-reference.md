# orq.ai API Reference for Deployment Invocation

MCP tools and HTTP API endpoints used by the orq-invoke-deployment skill.

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
| `telemetry.trace_id` | Trace ID — link it as `<host>/<workspace-key>/traces/(trace:<trace_id>)` ([format, and the unverified-id caveat](../../orq-cli/resources/command-map.md#building-app-urls)) |
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

### General Responses API

**Endpoint:** `POST https://my.orq.ai/v3/router/responses`

Use the configured host for staging or self-hosted environments; retain `/v3/router/responses`.

**Required fields:** `model: "agent/<key>"`, `input` (plain text or Responses input items).
Use `variables` for template substitution, `identity` for attribution, and `stream: true` for SSE on this same endpoint. Do not send legacy A2A `message.parts` or `task_id`.

```bash
# Single turn
curl --fail-with-body -sS https://my.orq.ai/v3/router/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"agent/<AGENT_KEY>","input":"Test query","identity":{"id":"synthetic-user"}}'

# Continue using the previous response's id (original request must use store: true, the default)
orq responses create --model "agent/<AGENT_KEY>" \
  --input "Follow-up" --previous-response-id "<response_id>" -o json

# Streaming uses the same route
curl --fail-with-body -sS -N https://my.orq.ai/v3/router/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"agent/<AGENT_KEY>","input":"Hello","stream":true}'
```

For HTTP continuation, add `previous_response_id` to the same request body. For HTTP streaming, add `stream: true` and use `curl -N`.
Read text from `output[]` message items and their `content[]` blocks of type `output_text`; tool and reasoning items may appear before messages. Save `id` for continuation. Streaming emits Responses events such as `response.output_text.delta`, not legacy `part.delta` events.
Check `status` and errors before treating a call as completed; asynchronous/background results are not completed answers.

**Contract source (2026-10-22):** [Run Agents](https://docs.orq.ai/ai-studio/ai-engineering/run-agents) · [Responses API](https://docs.orq.ai/ai-gateway/features/responses-api).

---

## Models (AI Router)

### Create Response

**Endpoint:** `POST https://my.orq.ai/v3/router/responses`

**Required fields:** `model` in `provider/model-id` format and `input` (text or Responses input items). For managed agents the same endpoint accepts `model: "agent/<key>"`.

Use Responses by default. Chat Completions or a v2 inference interface requires an explicit user request; do not select one as an error fallback. Preserve the configured host in staging/self-hosted environments.

| Field | Description |
|---|---|
| `instructions` | System instructions |
| `input` | User input or Responses message/tool items |
| `stream` | SSE using Responses events such as `response.output_text.delta` |
| `previous_response_id` | Continue from a stored response `id` (keep `store: true`) |
| `variables` | Template substitutions in instructions |
| `max_output_tokens` | Output token cap |
| `text.format` | Structured output format, e.g. `{type: "json_schema", name, schema, strict: true}` |
| `reasoning` | Reasoning configuration; discover supported effort values per model |
| `tools`, `tool_choice` | Responses tool definitions and selection; use the Responses schema |
| `guardrails`, `fallbacks`, `retry`, `cache`, `load_balancer`, `timeout` | Router reliability and policy options; inspect current Responses docs or `orq responses create --help` for supported shapes |
| `metadata`, `identity` | Trace metadata and end-user attribution |

**Output:** Check `status` and errors. Extract message `output[].content[]` blocks of type `output_text` (OpenAI SDK: `response.output_text`). Save `id` for continuation. Do not parse Chat Completions `choices` or legacy A2A `parts` for these calls.

```bash
# Basic model call
orq responses create --model "openai/gpt-6-luna" --input "What is the capital of France?" -o json

# Template variables
curl --fail-with-body -sS https://my.orq.ai/v3/router/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"openai/gpt-6-luna",
    "instructions":"You assist {{customer_name}}.",
    "input":"How do I reset my password?",
    "variables":{"customer_name":"Jane Doe"}
  }'

# Reliability: change model only, retain the Responses interface
curl --fail-with-body -sS https://my.orq.ai/v3/router/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"openai/gpt-6-luna","input":"Summarize this: ...",
    "fallbacks":[{"model":"anthropic/claude-sonnet-4-5"}],
    "retry":{"count":3,"on_codes":[429,500,502,503]},
    "timeout":{"call_timeout":30000}
  }'

# Structured JSON output: text.format, not response_format
curl --fail-with-body -sS https://my.orq.ai/v3/router/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"openai/gpt-6-luna",
    "input":"Extract name and email from: John Doe, john@example.com",
    "text":{"format":{
      "type":"json_schema","name":"contact","strict":true,
      "schema":{"type":"object","properties":{"name":{"type":"string"},"email":{"type":"string"}},"required":["name","email"],"additionalProperties":false}
    }}
  }'

# Streaming stays on Responses for every model
curl --fail-with-body -sS -N https://my.orq.ai/v3/router/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-6-luna","input":"Write me a haiku.","stream":true}'
```

**Contract sources:** [Responses API](https://docs.orq.ai/ai-gateway/features/responses-api) · [Create Response](https://docs.orq.ai/reference/responses/create-response).
