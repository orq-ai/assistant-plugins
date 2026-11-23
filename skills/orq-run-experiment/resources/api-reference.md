# orq.ai API Reference for Experiments

MCP tools and HTTP API endpoints used by the orq-run-experiment skill.

> **Official documentation:** [Run Experiments via the API](https://docs.orq.ai/docs/experiments/api#run-experiments-via-the-api)

## Contents
- MCP tools
- Experiments API (v3): create, configure, run and read experiments over HTTP
- HTTP API fallback (evaluators, agents, tools, memory, knowledge)

## MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

| Tool | Purpose |
|------|---------|
| `create_llm_eval` | Create an LLM evaluator |
| `create_python_eval` | Create a Python evaluator for code-based checks |
| `get_llm_eval` / `get_python_eval` | Retrieve an evaluator by ID |
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

## Experiments API (v3)

Reference: [Create an experiment](https://docs.orq.ai/reference/experiments/create-an-experiment) and the other pages under `reference/experiments/`. Base URL `https://my.orq.ai`, header `Authorization: Bearer $ORQ_API_KEY`.

**When to use it instead of the MCP tools:**
- the experiment must be repeatable from a script, CI or cron, with no agent present
- you need an orq.ai **agent** as the task (agent task column), or several prompt and agent columns side by side in one run
- you want a variant as a new run of the same experiment, compared to a baseline run with deltas (`baseline_run_id`)
- you need to re-run only failed or new cells (`EXECUTE_SCOPE_PARTIAL`), cancel a run, or read per-cell outputs and trace ids

For a one-off experiment from a chat, `create_experiment` (MCP) is still fine.

**Model.** An experiment holds runs. A run is a grid: one row per dataset datapoint (at most 1000) and columns of these types: dataset, task (`prompt`, `agent` or `openresponse`), metric (cost, latency, tokens; added with the first task column), evaluator and annotation. Each evaluator scores every task column. Dataset `inputs` fill `{{variables}}` in the task messages.

### 1. Create the experiment (its first run, "Run 1", is a draft)

```bash
curl -s https://my.orq.ai/v3/experiments \
  -H "Authorization: Bearer $ORQ_API_KEY" -H "Content-Type: application/json" \
  -d '{"display_name": "support-replies-v2", "dataset_id": "<DATASET_ID>"}' | jq '{experiment_id, selected_run_id}'
```

`project_id` defaults to the key's project and is required for keys that span several projects. Keep `experiment_id` as `EXP` and `selected_run_id` as `RUN`.

### 2. Add task columns

Prompt task (the model and messages under test):

```bash
curl -s https://my.orq.ai/v3/experiments/$EXP/runs/$RUN/tasks \
  -H "Authorization: Bearer $ORQ_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "display_name": "baseline prompt",
    "prompt": {
      "prompt_config": {
        "model": "openai/gpt-6-luna",
        "messages": [
          {"role": "system", "content": "You answer customer questions in two sentences."},
          {"role": "user", "content": "{{user_message}}"}
        ]
      }
    }
  }' | jq '{column_id, key}'
```

`messages_source`: `MESSAGES_SOURCE_PROMPT_CONFIG` (default, the prompt's messages only), `MESSAGES_SOURCE_MESSAGES_COLUMN` (the row's dataset messages) or `MESSAGES_SOURCE_COMBINED` (prompt messages, then the row's). `engine` can be `text` (default), `jinja` or `mustache`.

Agent task (an orq.ai agent by key; system messages are dropped because the agent's instructions provide them):

```bash
curl -s https://my.orq.ai/v3/experiments/$EXP/runs/$RUN/tasks \
  -H "Authorization: Bearer $ORQ_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "display_name": "support agent",
    "agent": {
      "agent_key": "<AGENT_KEY>",
      "messages_source": "MESSAGES_SOURCE_PROMPT_CONFIG",
      "messages": [{"role": "user", "content": "{{user_message}}"}]
    }
  }' | jq '{column_id, key}'
```

Use `"messages_source": "MESSAGES_SOURCE_MESSAGES_COLUMN"` with no `messages` to send the dataset's conversation instead.

### 3. Add evaluator columns (existing evaluators, by ID)

```bash
curl -s https://my.orq.ai/v3/experiments/$EXP/runs/$RUN/evaluators \
  -H "Authorization: Bearer $ORQ_API_KEY" -H "Content-Type: application/json" \
  -d '{"evaluator_id": "<EVALUATOR_ID>"}' | jq '{column_id, key}'
```

The evaluator's config lives on the evaluator; change it there, not in the experiment.

### 4. Execute and poll

```bash
curl -s https://my.orq.ai/v3/experiments/$EXP/runs/$RUN/execute \
  -H "Authorization: Bearer $ORQ_API_KEY" -H "Content-Type: application/json" \
  -d '{"scope": "EXECUTE_SCOPE_FULL", "constraints": {"llm": {"concurrency": 5}}}' | jq .status

# Poll until status is RUN_STATUS_COMPLETED, RUN_STATUS_FAILED or RUN_STATUS_CANCELED
curl -s https://my.orq.ai/v3/experiments/$EXP/runs/$RUN \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq '{status, row_count, cell_status_counts}'
```

Scopes: `EXECUTE_SCOPE_FULL` (every cell), `EXECUTE_SCOPE_PARTIAL` (skips completed and running cells, so use it after adding a column or to retry failures), and `EXECUTE_SCOPE_ROW`, `EXECUTE_SCOPE_COLUMN`, `EXECUTE_SCOPE_CELL` with `targets`. A queued or running run rejects a new execute until it finishes or you cancel it (`POST .../runs/$RUN/cancel` with `{}`). Check `cell_status_counts.failed` before reading scores; `not_run` cells on other columns after a clean run are normal. An optional `constraints.llm.timeout` (for example `"600s"`) must be at least the timeout of every agent in the run, or the execute call is rejected with `OPTIMIZATION_EXPERIMENTS_RUN_TIMEOUT_BELOW_AGENT_TIMEOUT`.

### 5. Read results

```bash
# Aggregates per task column: evaluator pass rates / averages, cost, latency, tokens
curl -s --get https://my.orq.ai/v3/experiments/$EXP/runs/$RUN/metrics \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq .metrics

# Per-row cells (outputs, per-cell cost, trace_id) for chosen columns, by display name
curl -s https://my.orq.ai/v3/experiments/$EXP/runs/$RUN/query \
  -H "Authorization: Bearer $ORQ_API_KEY" -H "Content-Type: application/json" \
  -d '{"columns": ["baseline prompt"], "limit": 50}' \
  | jq '.rows[] | .cells[] | {status, text: .value.last_message.content[0].text, trace_id: .value.trace_id}'
```

The response has `has_more`; page with `starting_after` set to the last `row_id`. The query also returns metric cells, so filter on the column you asked for if needed.

Metric keys are `{task_column_id}_{evaluator_id}` for evaluators and `{task_column_id}_orq_cost`, `_orq_latency`, `_orq_input_tokens` and so on for usage. Each value holds one of `boolean`, `number` or `categorical`: a boolean evaluator reports `.boolean.passed_rate` (0 to 100); a numeric metric reports `.number.min`, `max`, `avg`, `sum` (the total, e.g. total cost), `median`, `std_dev`; a label evaluator reports `categorical` counts and gets no `baseline`.

### 6. Iterate: new run, change one thing, compare to baseline

```bash
# New run copies the selected run's columns; outputs start empty
curl -s https://my.orq.ai/v3/experiments/$EXP/runs \
  -H "Authorization: Bearer $ORQ_API_KEY" -H "Content-Type: application/json" \
  -d '{"key": "Run 2 - shorter system prompt"}' | jq '{run_id, status}'   # keep run_id as RUN2

# Change the task column (prompt task columns keep their column_id in the new run)
curl -s -X PATCH https://my.orq.ai/v3/experiments/$EXP/runs/$RUN2/tasks/<TASK_COLUMN_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" -H "Content-Type: application/json" \
  -d '{"prompt": {"prompt_config": {"model": "openai/gpt-6-luna", "messages": [ ... ]}}}'

# Execute RUN2 as in step 4, then compare against the first run
curl -s --get https://my.orq.ai/v3/experiments/$EXP/runs/$RUN2/metrics \
  --data-urlencode "baseline_run_id=$RUN" \
  -H "Authorization: Bearer $ORQ_API_KEY" | jq '.metrics | map_values(.baseline)'
```

The PATCH replaces the whole task config, so resend `messages_source`, `engine` and `tools` if the column used non-default values.

`baseline` gives `avg`, `delta`, `pct_change` and `direction` (`up`, `down`, `same`) per metric. Agent task columns get a new `column_id` in each run, so look it up with `GET .../runs/$RUN2/tasks` before patching one. Because metric keys contain the column id, `baseline` is null for agent columns: compare agent variants as two columns in the same run instead, and use the baseline flow for prompt columns. A new run starts with empty cells, so any execute on it, `EXECUTE_SCOPE_PARTIAL` included, runs and bills every cell.

Other endpoints: list experiments `GET /v3/experiments`, list runs `GET /v3/experiments/$EXP/runs` (newest first), export a run `GET .../runs/$RUN/export`, delete an experiment `DELETE /v3/experiments/$EXP`.

## HTTP API Fallback

For operations not yet in MCP.

### Evaluators

```bash
# List evaluators (paginate with starting_after=<last _id> while has_more is true; `after=` is ignored)
curl -s "https://api.orq.ai/v2/evaluators?limit=50" \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get evaluator details
curl -s https://api.orq.ai/v2/evaluators/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Invoke an evaluator
curl -s https://api.orq.ai/v3/evaluators/<ID>/invoke \
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
orq responses create --model "agent/<AGENT_KEY>" --input "Test input" -o json

# Continue with the response id
orq responses create --model "agent/<AGENT_KEY>" --input "Step 2" \
  --previous-response-id "<response_id>" -o json
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
