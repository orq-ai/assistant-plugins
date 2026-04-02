# orq.ai API Reference for Dataset Generation

MCP tools and HTTP API endpoints used by the generate-synthetic-dataset skill.

> **Official documentation:** [Datasets via the API — Programmatic Data Management](https://docs.orq.ai/docs/datasets/api-usage#datasets-via-the-api-programmatic-data-management)

## Contents
- MCP tools
- HTTP API: datasets, datapoints, evaluators

## MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

| Tool | Purpose |
|------|---------|
| `list_models` | List available models for choosing generation models |
| `create_dataset` | Create a new evaluation dataset |
| `create_datapoints` | Add datapoints to a dataset |
| `list_datapoints` | List datapoints in a dataset |
| `search_entities` | Find existing datasets (`type: "dataset"`) |
| `update_datapoint` | Modify existing datapoints (curation) |
| `delete_datapoints` | Remove datapoints from a dataset (curation) |

## HTTP API

> **When to use HTTP API vs MCP:** Use MCP tools for small to medium datasets (up to ~50 datapoints). For larger datasets, use the HTTP API for bulk operations to avoid MCP context/payload limits.

### Datasets

```bash
# List datasets
curl -s https://api.orq.ai/v2/datasets \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get dataset details
curl -s https://api.orq.ai/v2/datasets/<DATASET_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq
```

### Datapoints

```bash
# List datapoints in a dataset
curl -s "https://api.orq.ai/v2/datasets/<DATASET_ID>/datapoints" \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Bulk create datapoints (use for >50 datapoints)
curl -s -X POST "https://api.orq.ai/v2/datasets/<DATASET_ID>/datapoints" \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"datapoints": [...]}' | jq

# Delete datapoints (bulk)
curl -s -X DELETE https://api.orq.ai/v2/datasets/<DATASET_ID>/datapoints \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"ids": ["dp_1", "dp_2"]}' | jq
```

### Evaluators

```bash
# List evaluators (to understand what the dataset needs to support)
curl -s https://api.orq.ai/v2/evaluators \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq
```
