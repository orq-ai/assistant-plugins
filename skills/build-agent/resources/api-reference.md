# orq.ai API Reference for Agent Building

MCP tools and HTTP API endpoints used by the build-agent skill.

> **Official documentation:** [Agents API Guide](https://docs.orq.ai/docs/agents/agent-api) · [Create Agent](https://docs.orq.ai/reference/agents/create-agent) · [Create Response](https://docs.orq.ai/reference/agents/create-response)

## Contents
- MCP tools
- HTTP API: agents, tools, knowledge bases, memory stores, files, evaluators

## MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

| Tool | Purpose |
|------|---------|
| `create_agent` | Create a new agent with configuration |
| `get_agent` | Get agent details — verify configuration after creation or updates |
| `update_agent` | Update agent configuration (instructions, model, tools) — iterate without recreating |
| `search_entities` | Find agents, knowledge bases (`type: "knowledge"`), memory stores (`type: "memory_store"`) |
| `search_directories` | Discover workspace project structure and paths — useful for KB `path` selection |
| `list_models` | List available models for agent configuration |
| `create_llm_eval` | Create evaluators for quality comparison |
| `evaluator_get` | Retrieve any evaluator by ID |
| `list_traces` | Inspect traces for latency/cost data |

## HTTP API

### Agents

**Required fields for agent creation:** `key`, `role`, `description`, `instructions`, `path`, `model`, `settings`

```bash
# Create agent
curl -s -X POST https://api.orq.ai/v2/agents \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
  "key": "my-agent",
  "role": "Assistant",
  "description": "A helpful assistant",
  "instructions": "Be helpful and concise",
  "path": "Default/agents",
  "model": {"id": "openai/gpt-4o"},
  "settings": {
    "max_iterations": 10,
    "max_execution_time": 300,
    "tools": []
  }
}' | jq

# List agents
curl -s https://api.orq.ai/v2/agents \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get agent details
curl -s https://api.orq.ai/v2/agents/<KEY> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Update agent configuration
curl -s -X PATCH https://api.orq.ai/v2/agents/<KEY> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"instructions": "Updated instructions..."}' | jq

# Update agent model
curl -s -X PATCH https://api.orq.ai/v2/agents/<KEY> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "openai/gpt-4o"}' | jq
```

### Agent Responses

**Endpoint:** `POST https://api.orq.ai/v2/agents/{agent_key}/responses` — agent_key goes in the URL path.

```bash
# Invoke agent
curl -s -X POST https://api.orq.ai/v2/agents/<AGENT_KEY>/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
  "message": {"role": "user", "parts": [{"kind": "text", "text": "Test query"}]},
  "background": false
}' | jq

# Multi-turn (reuse task_id from previous response)
curl -s -X POST https://api.orq.ai/v2/agents/<AGENT_KEY>/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
  "task_id": "<task_id>",
  "message": {"role": "user", "parts": [{"kind": "text", "text": "Follow-up"}]},
  "background": false
}' | jq

# Call specific version
curl -s -X POST https://api.orq.ai/v2/agents/<AGENT_KEY>@4.0.0/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
  "message": {"role": "user", "parts": [{"kind": "text", "text": "Query"}]}
}' | jq
```

### Tools

Supported tool types: `function`, `http`, `code` (Python), `mcp`, `json_schema`, and built-in tools (`current_date`, `google_search`, `web_scraper`, `retrieve_knowledge_bases`, `query_knowledge_base`, `retrieve_memory_stores`, `query_memory_store`, `write_memory_store`, `delete_memory_document`, `retrieve_agents`, `call_sub_agent`). Provider tools are also supported (e.g., `openai:web_search`).

Tools are configured in the `settings.tools` array:

```bash
# List available tools
curl -s https://api.orq.ai/v2/tools \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a custom tool
curl -s https://api.orq.ai/v2/tools \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "tool-key", "description": "Tool description", "type": "function", "function": {"name": "tool_name", "parameters": {"type": "object", "properties": {}}}}' | jq
```

### Evaluators

```bash
# Invoke evaluator to compare model outputs
curl -s https://api.orq.ai/v2/evaluators/<ID>/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"output": "Model output to evaluate", "query": "Original question"}' | jq
```

### Knowledge Bases

Attach to agents via the `knowledge_bases` array during creation. You must also add the built-in tools `retrieve_knowledge_bases` and `query_knowledge_base` to `settings.tools` for the agent to use them.

```bash
# List Knowledge Bases
curl -s https://api.orq.ai/v2/knowledge \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq .

# Get a specific Knowledge Base
curl -s https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq .

# Create a Knowledge Base
curl -s https://api.orq.ai/v2/knowledge \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "support-docs",
    "type": "internal",
    "embedding_model": "cohere/embed-english-v3.0",
    "path": "my-project/knowledge"
  }' | jq .

# Update a Knowledge Base
curl -s -X PATCH https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "updated-key"}' | jq .

# Delete a Knowledge Base
curl -s -X DELETE https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY"

# Search a Knowledge Base
curl -s https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/search \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "search query"}' | jq .

# Search with metadata filters
curl -s https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/search \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "billing issue",
    "filter_by": {"topic": {"eq": "billing"}, "client_id": {"eq": "acme"}},
    "search_options": {"include_metadata": true, "include_scores": true}
  }' | jq .
```

### Files

```bash
# Upload a file
curl -s https://api.orq.ai/v2/files \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -F "file=@./document.pdf" | jq .
```

### Datasources

```bash
# Create a datasource from an uploaded file
curl -s https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Support Documentation", "file_id": "<FILE_ID>"}' | jq .

# Create an empty datasource (for manual chunks)
curl -s https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Manual Chunks"}' | jq .

# List datasources
curl -s https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq .

# Delete a datasource
curl -s -X DELETE https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources/<DATASOURCE_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY"
```

### Chunks

```bash
# Add chunks to a datasource
curl -s https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources/<DATASOURCE_ID>/chunks \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '[
    {"text": "Chunk content", "metadata": {"source": "docs", "topic": "billing"}}
  ]' | jq .

# List chunks
curl -s "https://api.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources/<DATASOURCE_ID>/chunks?status=completed" \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq .
```

### Memory Stores

Create memory stores with `key`, `description`, `path`, and `embedding_config` (required). Attach to agents via the `memory_stores` array (list of store keys). You must also add the built-in tools `retrieve_memory_stores`, `query_memory_store`, `write_memory_store`, and `delete_memory_document` to `settings.tools`.

When invoking an agent with memory, include `memory.entity_id` in the request to identify whose memory to access.

```bash
# List memory stores
curl -s https://api.orq.ai/v2/memory-stores \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a memory store
curl -s https://api.orq.ai/v2/memory-stores \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
  "key": "user-preferences",
  "description": "Stores user preferences and facts across sessions",
  "path": "Default/agents",
  "embedding_config": {
    "model": "openai/text-embedding-3-small"
  }
}' | jq

# Get memory store details
curl -s https://api.orq.ai/v2/memory-stores/<KEY> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a memory (sub-resource of memory store)
curl -s https://api.orq.ai/v2/memory-stores/<KEY>/memories \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
  "entity_id": "user_12345",
  "metadata": {
    "type": "customer",
    "segment": "premium"
  }
}' | jq

# Create a memory document (sub-resource of memory)
curl -s https://api.orq.ai/v2/memory-stores/<KEY>/memories/<MEMORY_ID>/documents \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content": "User prefers casual tone and English language"}' | jq

# List memories in a store
curl -s https://api.orq.ai/v2/memory-stores/<KEY>/memories \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Delete a memory store
curl -s -X DELETE https://api.orq.ai/v2/memory-stores/<KEY> \
  -H "Authorization: Bearer $ORQ_API_KEY"
```
