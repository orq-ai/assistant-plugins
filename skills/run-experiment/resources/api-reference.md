# orq.ai API Reference for Experiments

MCP tools and HTTP API endpoints used by the run-experiment skill.

> **Official documentation:** [Run Experiments via the API](https://docs.orq.ai/docs/experiments/api#run-experiments-via-the-api)

## Contents
- MCP tools
- HTTP API fallback (experiments, evaluators, agents, tools, memory, knowledge)

## MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

| Tool | Purpose |
|------|---------|
| `create_llm_eval` | Create an LLM evaluator |
| `list_traces` | List and filter traces for error analysis |
| `list_spans` | List spans within a trace |
| `get_span` | Get detailed span information |
| `get_agent` | Get agent configuration and details |
| `create_agent` | Create a new agent |
| `create_experiment` | Create and run an experiment |
| `list_experiment_runs` | List experiment runs |
| `get_experiment_run` | Get experiment run details |
| `list_models` | List available models (including embedding models) |
| `get_analytics_overview` | Quick health check — error rate, request volume before experiments |
| `query_analytics` | Drill-down analytics — correlate experiment results with production metrics |

## HTTP API Fallback

For operations not yet in MCP.

### Evaluators

```bash
# List evaluators
curl -s https://api.orq.ai/v2/evaluators \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get evaluator details
curl -s https://api.orq.ai/v2/evaluators/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Invoke an evaluator
curl -s https://api.orq.ai/v2/evaluators/<ID>/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"output": "LLM output", "query": "Original input", "reference": "Expected answer"}' | jq
```

### Agents

```bash
# List agents
curl -s https://api.orq.ai/v2/agents \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Invoke an agent (for generating test traces)
curl -s -X POST https://api.orq.ai/v2/agents/<AGENT_KEY>/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": {"role": "user", "parts": [{"kind": "text", "text": "Test input"}]}, "background": false}' | jq

# Multi-turn agent testing (reuse task_id)
curl -s -X POST https://api.orq.ai/v2/agents/<AGENT_KEY>/responses \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"task_id": "<task_id>", "message": {"role": "user", "parts": [{"kind": "text", "text": "Step 2"}]}, "background": false}' | jq
```

### Tools

```bash
# List tools
curl -s https://api.orq.ai/v2/tools \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get tool details
curl -s https://api.orq.ai/v2/tools/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a tool
curl -s https://api.orq.ai/v2/tools \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "get-weather", "description": "Get weather", "type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}' | jq
```

### Memory Stores

```bash
# List memory stores
curl -s https://api.orq.ai/v2/memory-stores \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a memory store (key, description, path, embedding_config required)
curl -s https://api.orq.ai/v2/memory-stores \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "conversation-memory", "description": "Stores conversation context", "path": "Default/agents", "embedding_config": {"model": "openai/text-embedding-3-small"}}' | jq

# Create a memory (sub-resource of memory store)
curl -s https://api.orq.ai/v2/memory-stores/<STORE_KEY>/memories \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "user_123"}' | jq

# List memories in a store
curl -s https://api.orq.ai/v2/memory-stores/<STORE_KEY>/memories \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq
```

### Knowledge Bases

```bash
# List knowledge bases
curl -s https://api.orq.ai/v2/knowledge \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get knowledge base details
curl -s https://api.orq.ai/v2/knowledge/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a knowledge base
curl -s https://api.orq.ai/v2/knowledge \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "product-docs", "type": "internal", "path": "Default/knowledge", "embedding_model": "openai/text-embedding-3-small"}' | jq

# Search a knowledge base (retrieval-only evaluation)
curl -s https://api.orq.ai/v2/knowledge/<ID>/search \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I reset my password?", "top_k": 10, "threshold": 0.8}' | jq
```
