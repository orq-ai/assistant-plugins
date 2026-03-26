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
| `create_python_eval` | Create a Python evaluator for code-based checks |
| `get_evaluator_llm` | Retrieve an LLM evaluator by key or ID |
| `get_evaluator_python` | Retrieve a Python evaluator by key or ID |
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
curl -s https://my.orq.ai/v2/evaluators \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Invoke an evaluator
curl -s https://my.orq.ai/v2/evaluators/<ID>/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"output": "LLM output", "query": "Original input", "reference": "Expected answer"}' | jq
```

### Agents

```bash
# List agents
curl -s https://my.orq.ai/v2/agents \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Invoke an agent (for generating test traces)
curl -s https://api.orq.ai/v2/agents/responses \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent_key": "<KEY>", "message": {"role": "user", "parts": [{"kind": "text", "text": "Test input"}]}}' | jq

# Multi-turn agent testing (reuse task_id)
curl -s https://api.orq.ai/v2/agents/responses \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent_key": "<KEY>", "message": {"role": "user", "parts": [{"kind": "text", "text": "Step 2"}]}, "task_id": "<task_id>"}' | jq
```

### Tools

```bash
# List tools
curl -s https://my.orq.ai/v2/tools \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get tool details
curl -s https://my.orq.ai/v2/tools/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a tool
curl -s https://my.orq.ai/v2/tools \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "get-weather", "description": "Get weather", "type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}' | jq
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
  -d '{"key": "conversation-memory", "embedding_model": "text-embedding-3-small", "embedding_provider": "openai", "top_k": 5}' | jq

# Add a memory document
curl -s https://my.orq.ai/v2/memory-stores/<STORE_ID>/documents \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "User prefers email communication", "metadata": {"source": "session_1"}}' | jq

# List memory documents
curl -s https://my.orq.ai/v2/memory-stores/<STORE_ID>/documents \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq
```

### Knowledge Bases

```bash
# List knowledge bases
curl -s https://my.orq.ai/v2/knowledge \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get knowledge base details
curl -s https://my.orq.ai/v2/knowledge/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a knowledge base
curl -s https://my.orq.ai/v2/knowledge \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "product-docs", "embedding_model": "openai/text-embedding-3-small", "top_k": 5, "threshold": 0.7}' | jq

# Search a knowledge base (retrieval-only evaluation)
curl -s https://my.orq.ai/v2/knowledge/<ID>/search \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I reset my password?", "limit": 10, "threshold": 0.8}' | jq
```
