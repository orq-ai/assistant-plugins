# orq.ai API Reference for Agent Building

MCP tools and HTTP API endpoints used by the build-agent skill.

> **Official documentation:** [Agents Framework and API Guide](https://docs.orq.ai/docs/common-architecture/agents-framework-guide#agents-framework-and-api-guide)

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
| `search_entities` | Find agents, knowledge bases (`type: "knowledge"`), memory stores (`type: "memory_store"`), evaluators (`type: "evaluator"`) |
| `search_directories` | Discover workspace project structure and paths — useful for KB `path` selection |
| `list_models` | List available models for agent configuration |
| `create_llm_eval` | Create evaluators for quality comparison |
| `get_evaluator_llm` | Retrieve an LLM evaluator by key or ID |
| `get_evaluator_python` | Retrieve a Python evaluator by key or ID |
| `list_traces` | Inspect traces for latency/cost data |

## HTTP API

### Agents

```bash
# List agents
curl -s https://my.orq.ai/v2/agents \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get agent details
curl -s https://my.orq.ai/v2/agents/<KEY> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Update agent configuration
curl -s -X PATCH https://my.orq.ai/v2/agents/<KEY> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"instructions": "Updated instructions..."}' | jq

# Update agent model
curl -s -X PATCH https://my.orq.ai/v2/agents/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "openai/gpt-4.1"}' | jq

# Invoke agent for testing
curl -s https://api.orq.ai/v2/agents/responses \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent_key": "<KEY>", "message": {"role": "user", "parts": [{"kind": "text", "text": "Test query"}]}}' | jq

# Multi-turn agent testing (reuse task_id)
curl -s https://api.orq.ai/v2/agents/responses \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent_key": "<KEY>", "message": {"role": "user", "parts": [{"kind": "text", "text": "Follow-up"}]}, "task_id": "<task_id>"}' | jq
```

### Tools

```bash
# List available tools
curl -s https://my.orq.ai/v2/tools \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a custom tool
curl -s https://my.orq.ai/v2/tools \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "tool-key", "description": "Tool description", "type": "function", "function": {"name": "tool_name", "parameters": {"type": "object", "properties": {...}}}}' | jq
```

### Evaluators

```bash
# Invoke evaluator to compare model outputs
curl -s https://my.orq.ai/v2/evaluators/<ID>/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"output": "Model output to evaluate", "query": "Original question"}' | jq
```

### Knowledge Bases

```bash
# List Knowledge Bases
curl -s https://my.orq.ai/v2/knowledge \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq .

# Get a specific Knowledge Base
curl -s https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq .

# Create a Knowledge Base
curl -s https://my.orq.ai/v2/knowledge \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "support-docs",
    "type": "internal",
    "embedding_model": "cohere/embed-english-v3.0",
    "path": "my-project/knowledge"
  }' | jq .

# Update a Knowledge Base
curl -s -X PATCH https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "updated-key"}' | jq .

# Delete a Knowledge Base
curl -s -X DELETE https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY"

# Search a Knowledge Base
curl -s https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/search \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "search query"}' | jq .

# Search with metadata filters
curl -s https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/search \
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
curl -s https://my.orq.ai/v2/files \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -F "file=@./document.pdf" | jq .
```

### Datasources

```bash
# Create a datasource from an uploaded file
curl -s https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Support Documentation", "file_id": "<FILE_ID>"}' | jq .

# Create an empty datasource (for manual chunks)
curl -s https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Manual Chunks"}' | jq .

# List datasources
curl -s https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq .

# Delete a datasource
curl -s -X DELETE https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources/<DATASOURCE_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY"
```

### Chunks

```bash
# Add chunks to a datasource
curl -s https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources/<DATASOURCE_ID>/chunks \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '[
    {"text": "Chunk content", "metadata": {"source": "docs", "topic": "billing"}}
  ]' | jq .

# List chunks
curl -s "https://my.orq.ai/v2/knowledge/<KNOWLEDGE_ID>/datasources/<DATASOURCE_ID>/chunks?status=completed" \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq .
```

### Memory Stores

```bash
# List memory stores
curl -s https://my.orq.ai/v2/memory-stores \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a memory store
curl -s https://my.orq.ai/v2/memory-stores \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "user-preferences", "description": "Stores user preferences and facts across sessions"}' | jq

# Get memory store details
curl -s https://my.orq.ai/v2/memory-stores/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# List memory documents
curl -s https://my.orq.ai/v2/memory-stores/<ID>/documents \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a memory document
curl -s https://my.orq.ai/v2/memory-stores/<ID>/documents \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "user_12345", "content": {"name": "John", "preferences": {"language": "en", "tone": "casual"}}}' | jq

# Delete a memory store
curl -s -X DELETE https://my.orq.ai/v2/memory-stores/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY"
```
