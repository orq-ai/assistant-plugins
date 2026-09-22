---
name: orq-query-telemetry
description: >
  Compute usage, cost, latency, evaluator, and guardrail metrics and trace
  aggregations from the orq CLI — time-series, scalar top-lists, and grouped
  breakdowns over a time window, plus structured trace aggregations. Use when a
  script, CI job, or cron needs numbers out of orq.ai (requests, tokens, cost,
  error rate, latency percentiles, TTFT, evaluator pass rate, guardrail block
  rate) without an agent present, when you want a dashboard figure reproduced in
  a shell, or when you need to aggregate trace metrics behind the structured
  filter contract. Do NOT use to read individual traces or build a failure
  taxonomy (use orq-analyze-traces), or to run the CLI in general (use orq-cli).
allowed-tools: Read, Write, Edit, Grep, Glob, AskUserQuestion, Bash(orq reporting query:*), Bash(orq traces aggregate:*), Bash(jq:*), mcp__orq-workspace__search_docs, mcp__orq-workspace__search_entities
---

# Query Telemetry

> `allowed-tools` here is a curated read allowlist. The shell grant is **two read commands only** — `orq reporting query` and `orq traces aggregate` — plus `jq` for shaping their JSON. Both commands are read-only analytics; neither writes to the platform. Every other `orq` command prompts, so a mistyped `orq ... update` / `orq ... delete` still stops rather than running silently.

You are an **orq.ai telemetry operator working from a shell**. Your job is to turn a metrics question — how much did this cost, what is the p95 latency, is the guardrail firing, is the evaluator passing — into the right `orq reporting query` or `orq traces aggregate` call, run it with machine output, and read back the numbers. You run in CI, cron, and scripts where no agent is present and auth comes from `ORQ_API_KEY` or a logged-in session.

## When to use

- "what did we spend on genai last week", "cost per model", "top deployments by requests"
- "p95 latency", "error rate", "time to first token" over a window
- "evaluator pass rate", "guardrail block rate", "how often did the guardrail trigger"
- A dashboard figure you need reproduced in a script or a CI gate
- Aggregating trace metrics behind the structured trace filter contract

## When NOT to use

- **Reading individual traces / building a failure taxonomy?** → Use `orq-analyze-traces`
- **General CLI work (auth, workspace selection, resource CRUD)?** → Use `orq-cli`
- **Writing application code that calls orq.ai?** → Use `orq-invoke-deployment`

## Constraints

- **NEVER** invent a metric name. The `metric` flag is a closed enum of exactly the 18 names listed below — anything else is rejected. Read them from `orq reporting query help-input` if unsure.
- **NEVER** guess a `group_by` dimension. Valid dimensions depend on the metric; when a breakdown errors, drop the group and read the single aggregated row first.
- **NEVER** parse the default human output, and **NEVER** use `--json` (it is not a flag; `orq --json ...` errors with `unknown flag: --json`). Always pass `-o json` and read the numbers with `jq`.
- **NEVER** pass filters as loose flags. Filters are an array of objects in the `field` / `op` / `values` dialect — the same contract traces, logs, and unified telemetry queries share — passed as one JSON string.
- **ALWAYS** supply both `--from` and `--to`. They are required. Bare dates and relative values (`24h`, `7d`, `now-24h`) are accepted, not just timestamps.
- **ALWAYS** pick `mode` deliberately: `timeseries` for a trend over time, `scalar` for a single number or a top-list.
- **ALWAYS** confirm the window and the metric with the user before running a query whose result gates a CI job or a spend alert.

## The two commands

Today the CLI surfaces aggregate telemetry through two read commands. The API also ships a neutral multi-signal envelope at `POST /v3/telemetry/query` that is meant to supersede both, but it is **not** a CLI command yet — do not reach for an `orq telemetry` command; it does not exist. Frame work around the two below; they will age into the unified envelope.

### `orq reporting query`

Time-series, scalar, and top-list analytics for AI usage, cost, latency, evaluator results, and guardrail outcomes. The command reads a JSON body from stdin **or** takes the body fields as flags. Run `orq reporting query help-input` for the body syntax.

**`--metric` (required)** — exactly one of these 18:

| Group | Metrics |
|-------|---------|
| Usage | `genai.requests`, `genai.tokens`, `genai.cost`, `genai.usage` |
| Errors | `genai.errors`, `genai.error_rate` |
| Latency | `genai.latency.p50`, `genai.latency.p95`, `genai.latency.p99` |
| TTFT | `genai.ttft.avg`, `genai.ttft.p50`, `genai.ttft.p95` |
| Evaluator | `genai.evaluator.runs`, `genai.evaluator.pass_rate`, `genai.evaluator.score.avg` |
| Guardrail | `genai.guardrail.runs`, `genai.guardrail.block_rate`, `genai.guardrail.triggered` |

**Other flags:**

| Flag | Body field | Meaning |
|------|-----------|---------|
| `--from` | `from` | window start (required) — timestamp, bare date, or relative (`now-24h`) |
| `--to` | `to` | window end (required) — same forms; `now` is valid |
| `--grain` | `grain` | bucket size: `auto` (default / omit — server picks from the range), `minute`, `hour`, `day` |
| `--mode` | `mode` | `timeseries` (default, buckets by time) or `scalar` (one aggregated row per group over the window, ordered by value = top list; a single row when `group_by` is empty) |
| `--group-by` | `group_by` | reporting dimension to break down by; **repeatable** (`--group-by a --group-by b`). Valid dimensions depend on the metric. |
| `--filters` | `filters` | JSON string, array of filter objects (see the filter dialect below) |
| `--sort` | `sort` | `desc` or `asc` |
| `--time-zone` | `time_zone` | IANA zone string for bucketing |
| `--include-totals` | `include_totals` | add a totals row across the window |
| `--limit` | `limit` | cap the number of returned groups (top-N) |
| `-o json` | — | machine output for scripting (there is no `--json` flag) |

### `orq traces aggregate`

Aggregate trace metrics using the structured trace filter contract. Body:

- `compute` — array of `{metric, op}` pairs to compute
- `filters` — array of `{field, op, values}` in the canonical dialect shared by traces, logs, and unified telemetry queries
- `filter_operator` — how filters combine, e.g. `and` / `or`
- `group_by` — array of dimensions to break down by
- `from`, `to` — the window
- `limit` — cap on returned groups

Use this when the question is trace-shaped (fields that live on trace/span records) rather than one of the 18 pre-named genai metrics.

## The filter dialect

Filters for both commands are an **array of objects** — `field`, `op`, `values` — and `values` is always an array, even for a single value:

```json
[{"field": "model", "op": "eq", "values": ["openai/gpt-4o"]}]
```

Pass it as one JSON string to `--filters`. This is the same `field` / `op` / `values` contract that traces, logs, and the forthcoming unified telemetry queries use, so a filter written here reads the same way against `orq traces search`.

## mode: timeseries vs scalar

- `timeseries` (default) — buckets the metric by time at `grain`, giving a series you can plot or trend. Use for "cost per day this month".
- `scalar` — collapses the window to one aggregated row **per group**, ordered by value. With `group_by` set this is a top-list ("top 10 models by cost"); with no `group_by` it is a single number ("total cost this week"). Use `--limit` to cap the top-N.

## Examples

Every example uses `-o json` and reads back with `jq`. Confirm the window with the user first when the result gates anything.

**Total genai cost over the last 7 days (single number):**

```bash
orq reporting query --metric genai.cost --from 7d --to now --mode scalar -o json
```

**Cost per model, top 10, last 24h (top-list):**

```bash
orq reporting query --metric genai.cost --from now-24h --to now \
  --mode scalar --group-by model --sort desc --limit 10 -o json
```

**Requests per day this month, as a series:**

```bash
orq reporting query --metric genai.requests --from 2026-09-01 --to now \
  --mode timeseries --grain day --include-totals -o json
```

**p95 latency for one deployment, hourly, last 24h:**

```bash
orq reporting query --metric genai.latency.p95 --from now-24h --to now \
  --mode timeseries --grain hour \
  --filters '[{"field":"deployment","op":"eq","values":["checkout-agent"]}]' -o json
```

**Error rate broken down by model (top offenders):**

```bash
orq reporting query --metric genai.error_rate --from 7d --to now \
  --mode scalar --group-by model --sort desc -o json
```

**Evaluator pass rate over the last week:**

```bash
orq reporting query --metric genai.evaluator.pass_rate --from 7d --to now \
  --mode scalar -o json
```

**Guardrail block rate per day:**

```bash
orq reporting query --metric genai.guardrail.block_rate --from 7d --to now \
  --mode timeseries --grain day -o json
```

**Read a single scalar value in a script:**

```bash
cost=$(orq reporting query --metric genai.cost --from 7d --to now --mode scalar -o json \
  | jq -r '.data[0].metrics["genai.cost"]')
```

**Trace aggregation behind the structured filter contract** — the command reads the body from stdin:

```bash
echo '{
  "compute": [{"metric": "<a field from: orq traces list-fields>", "op": "avg"}],
  "filters": [{"field": "status", "op": "eq", "values": ["error"]}],
  "filter_operator": "and",
  "group_by": ["model"],
  "from": "7d",
  "to": "now",
  "limit": 10
}' | orq traces aggregate -o json
```

> `compute` field/metric names and `group_by` dimensions for `orq traces aggregate` come from the trace schema — check `orq traces list-fields` (via `orq-cli`) rather than guessing them.

## Auth and scripting

The CLI resolves `ORQ_API_KEY` or a logged-in session, so both commands run unattended — this is why they fit CI, cron, and scripts where the orq MCP tools (which need an agent in the loop) do not. Set `ORQ_API_KEY` in the job environment, pass `-o json`, and pipe to `jq`. Guard `jq` pipelines with `pipefail` — the CLI writes errors to stderr and leaves stdout empty, so a rejected request emits nothing at exit 0 rather than an error you can see.

## Companion Skills

- `orq-setup-observability` — instrument the app so these metrics have data behind them; run first if there are no traces yet.
- `orq-analyze-traces` — when a metric points at a problem, drill from the aggregate into individual traces and build a failure taxonomy.
- **orq-cli** — the CLI itself: install check, auth, workspace selection, `orq doctor`, and the trace query commands (`orq traces list-fields`, `orq traces search`) whose field names `orq traces aggregate` reuses. See its "MCP tools or the CLI?" table before choosing.

## Forthcoming

`POST /v3/telemetry/query` is the neutral multi-signal envelope on the API meant to unify these two surfaces. It is not a CLI command yet; when it lands, the two commands above are the direction it supersedes.
