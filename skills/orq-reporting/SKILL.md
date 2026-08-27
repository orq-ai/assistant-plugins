---
name: orq-reporting
description: >
  Slice orq.ai reporting metrics (cost, latency, usage, evaluator, guardrail)
  by any dimension. Use when the user asks about spend, token volume, error
  rates, latency percentiles, evaluator pass rates, guardrail enforcement, or
  wants a cost/usage breakdown by model, provider, agent, identity, or project.
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(orq reporting query:*), mcp__orq__get_analytics_overview, mcp__orq__query_analytics, mcp__orq__search_entities, mcp__orq__search_docs, AskUserQuestion
metadata:
  verified: 2026-08-27
  surface: cli + api
  source: https://docs.orq.ai/docs/ai-studio/observability/reporting-api
---

# Reporting

You **slice** aggregate AI metrics by any dimension the user asks for. One endpoint (`POST /v2/reporting`), 18 metrics, 34 dimensions, all read-only.

## Gotchas (read first)

1. **TTFT metrics reject entity dimensions.** `genai.ttft.*` with `agent`, `tool`, or `deployment` in `group_by` returns HTTP 400. Slice by `model` or `provider` only.

2. **Agent dimension returns IDs, not names.** Values are ULIDs like `01M0Z18MRH0A862HVWV5R145EX`. Resolve to display names with `mcp__orq__search_entities type=agent`.

3. **`genai.usage` bundle keys differ from metric names.** Sub-fields use underscored names (`request_count`, `total_cost`), not the dotted `genai.*` namespace individual metrics use.

4. **Zero TTFT means no streaming data, not "fast".** TTFT covers streaming requests only. An empty or all-zero result is unobservable, not a measurement.

5. **Evaluator and guardrail metrics share a source.** Every evaluator run is also a guardrail check. Pick the metric family that matches the question being asked.

6. **Ingestion delay.** Near real time, seconds of lag. Queries against the most recent minutes may miss events.

7. **Quantile error band.** Latency and TTFT percentiles carry +/-1-2% from t-digest estimation. Small differences are noise.

8. **Retention limit.** Querying with a `from` timestamp beyond the workspace's retention window (typically 30 days) returns HTTP 400. Shorten the range.

9. **Evaluator cost is not in the Reporting API.** The eval/guardrail dimensions (`evaluator_name`, `evaluator_stage`, `guardrail_origin`, etc.) are rejected or return $0 for cost metrics (`genai.cost`, `genai.usage`). The evaluator-specific metrics (`genai.evaluator.runs`, `genai.guardrail.runs`) may also return 0 even when evaluator spans exist in traces. To get eval cost, walk traces: paginate `orq traces search` with `--eval-passed`, call `list_spans` per trace, and sum `orq.billing.total_cost` on `span.evaluator`-type children. The `billing_billable` filter (`"field": "billing_billable", "op": "eq", "values": ["1"]`) isolates platform-routed traffic (which includes evals) from prompt-cached Claude Code traffic.

10. **MCP and CLI hit different backends.** `mcp__orq__query_analytics` uses legacy analytics rollup tables; the CLI `orq reporting query` uses the newer Reporting API. Project IDs differ between them, and cost totals for the same window will not match. Use CLI for authoritative numbers.

11. **MCP requires `project_id`.** `mcp__orq__query_analytics` fails without a `project_id` filter when the API key spans multiple projects. The CLI has no such requirement and returns workspace-wide data by default.

12. **JMESPath quoting is shell-dependent.** The `-j` projection flag needs different escaping per shell. Bash: `metrics.\"genai.cost\"`. PowerShell: `` metrics.`"genai.cost`" ``.

## Steps

### 1. Map question to metric and slice

| Question shape | Metric | Slice by |
|---|---|---|
| Spend, cost, billing | `genai.cost` or `genai.usage` | `model`, `provider`, `identity`, `agent` |
| Request or token volume | `genai.requests` or `genai.tokens` | `model`, `agent`, `deployment` |
| What's failing | `genai.error_rate` or `genai.errors` | `model`, `http_status_code` |
| Latency, slowness | `genai.latency.p95` | `model`, `provider` |
| Evaluator quality | `genai.evaluator.pass_rate` | `evaluator`, `evaluator_name` |
| Guardrail enforcement | `genai.guardrail.block_rate` | `guardrail_action`, `evaluator_name` |
| Eval cost / spend | Not in Reporting API | Use trace span walk (Gotcha 9) |
| Time to first token | `genai.ttft.p95` | `model`, `provider` only (Gotcha 1) |

If the question is vague ("how's the system doing?"), start with `mcp__orq__get_analytics_overview` for a zero-config snapshot, then drill into any anomaly it surfaces.

**Done when:** you have a specific metric and know which dimensions to slice by.

### 2. Build and run the query

**CLI** (full Reporting API surface):

```bash
# Time series (default): bucketed by time
orq reporting query \
  --metric <metric> \
  --from <ISO8601> --to <ISO8601> \
  --grain day \
  --group-by <dim> \
  --include-totals \
  --json

# Scalar top-list: one row per group, ranked
orq reporting query \
  --metric <metric> \
  --from <ISO8601> --to <ISO8601> \
  --mode scalar \
  --group-by <dim> \
  --sort desc \
  --limit 10 \
  --json
```

For filters or complex queries, write the body to a JSON file and use `--from-file`:

```json
{
  "metric": "genai.cost",
  "from": "2026-08-01T00:00:00Z",
  "to": "2026-08-27T00:00:00Z",
  "mode": "scalar",
  "group_by": ["model"],
  "filters": [{"field": "agent", "op": "eq", "values": ["<agent-id>"]}],
  "include_totals": true
}
```

```bash
orq reporting query --from-file query.json --json
```

Project large responses with JMESPath:

```bash
orq reporting query --from-file query.json --json -j "totals"
orq reporting query ... --json -j "data[].{model: dimensions.model, cost: metrics.\"genai.cost\"}"
```

**MCP** (quick single-project checks, fewer dimensions):

`mcp__orq__query_analytics` covers 6 metric categories (`usage`, `cost`, `latency`, `errors`, `agents`, `model_performance`) and 5 dimensions (`provider`, `model`, `project_id`, `http_status_code`, `agent_name`). Requires a `project_id` filter when the API key spans multiple projects (Gotcha 11). Hits a different backend than the CLI, so numbers will not match (Gotcha 10). Best category is `model_performance` (returns cost + latency per model in one query). The `errors` and `latency` categories may return empty model strings or non-standard status codes. The `agents` category may return empty even when agent traffic exists in the CLI view. Fall back to CLI for workspace-wide queries, evaluator/guardrail metrics, identity slices, cross-project aggregation, or the full 34 dimensions.

**Done when:** the response contains `"object": "report"` and data rows, or an empty `data: []` confirming no matching traffic.

### 3. Interpret and present

- **Cost** is USD. `genai.usage` splits input/output/cached/reasoning cost in one query.
- **Latency** is milliseconds. p50 = typical, p95/p99 = tail.
- **Ratios** (`error_rate`, `pass_rate`, `block_rate`) are [0, 1]. Present as percentages.
- **`has_more: true`**: increase `limit` (max 5000) or narrow the window.
- **Empty `data: []`** with no warnings: no matching traffic in the window, not an error.

If the result reveals an anomaly the user wants to investigate further, point to trace-level inspection for root-cause analysis.

**Done when:** the user's question is answered with specific numbers sliced by the dimensions they asked for.

## Reference

### All 18 metrics

**Usage:** `genai.requests` `genai.tokens` `genai.cost` `genai.errors` `genai.error_rate` `genai.latency.p50` `genai.latency.p95` `genai.latency.p99` `genai.ttft.avg` `genai.ttft.p50` `genai.ttft.p95` `genai.usage`

**Evaluator:** `genai.evaluator.runs` `genai.evaluator.pass_rate` `genai.evaluator.score.avg`

**Guardrail:** `genai.guardrail.runs` `genai.guardrail.block_rate` `genai.guardrail.triggered`

### All 34 dimensions

**Infrastructure:** `project` `provider` `model` `api_key` `credential_type` `status_code` `http_status_code`

**Entities:** `agent` `tool` `deployment` `evaluator` `dataset` `prompt` `policy` `conversation` `thread` `memory_store` `knowledge` `sheet`

**Identity:** `identity` `product` `tag` `dimension` `dimension_type`

**Eval/guardrail:** `guardrail_origin` `evaluator_name` `evaluator_type` `evaluator_version` `result_type` `evaluation_stage` `guardrail_stage` `evaluator_stage` `guardrail_action` `result_label`

**Filter-only** (not in `group_by`): `billing_billable`

**Filter operators:** `eq` `neq` `in` `not_in`

### Query parameters

| Param | Required | Default | Values |
|---|---|---|---|
| `metric` | yes | | 18 metrics above |
| `from` | yes | | ISO 8601 UTC |
| `to` | yes | | ISO 8601 UTC, after `from` |
| `grain` | no | `auto` | `auto` `minute` `hour` `day` |
| `group_by` | no | `[]` | Dimension names |
| `filters` | no | `[]` | `[{field, op, values}]` |
| `limit` | no | 1000 | Max 5000 |
| `time_zone` | no | UTC | IANA tz name |
| `include_totals` | no | false | Adds `totals` block |
| `mode` | no | `timeseries` | `timeseries` or `scalar` |
| `sort` | no | `desc` | `desc` `asc` (scalar only) |

### Response shape

```json
{
  "object": "report",
  "data": [{"timestamp": "2026-08-10T00:00:00Z", "dimensions": {"model": "..."}, "metrics": {"genai.cost": 31.13}}],
  "totals": {"metrics": {"genai.cost": 571.05}},
  "has_more": false,
  "meta": {"effective_grain": "day", "row_count": 4, "request_id": "req_...", "currency": "USD", "warnings": []}
}
```

Scalar mode sets `timestamp: null`. The `totals` block appears only when `include_totals: true`.

### `genai.usage` bundle sub-fields

`request_count` `input_tokens` `output_tokens` `total_tokens` `input_cost` `output_cost` `total_cost` `cached_input_tokens` `cached_cost` `reasoning_tokens` `reasoning_cost` `audio_input_tokens` `audio_output_tokens` `error_count`
