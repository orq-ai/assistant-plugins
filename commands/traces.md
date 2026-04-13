---
name: traces
description: Query and summarize traces with filters — debugging entry point before analyze-trace-failures
argument-hint: [--deployment <name>] [--status <status>] [--last <duration>] [--limit <n>]
allowed-tools: AskUserQuestion, orq*
---

# Traces

Query production traces from the orq.ai platform and display a summary. Use this as a debugging entry point — once you spot a problem, hand off to the `analyze-trace-failures` skill for deep analysis.

## Instructions

### 1. Parse arguments

Extract filters from `$ARGUMENTS`. All are optional:

- `--deployment <name>` — filter by deployment name or key
- `--status <status>` — filter by trace status (e.g., `error`, `success`)
- `--last <duration>` — time window: `1h`, `6h`, `24h`, `7d`, `30d` (default: `24h`)
- `--limit <n>` — max traces to return (default: `20`)

If `$ARGUMENTS` is empty, use defaults (last 24h, limit 20, no filters).

If the user provides plain text instead of flags (e.g., `/orq:traces errors from today`), interpret the intent and map to the appropriate filters.

### 2. Build filter string

The `list_traces` MCP tool uses Typesense filter syntax. Build the `filter` parameter from parsed arguments:

- **Status filter:** `status:=ERROR` or `status:=OK`
- **Model filter:** `attr_kv:=gen_ai.request.model=<model>`
- **Combined:** join with ` && `

Use `list_registry_keys` and `list_registry_values` to discover available filter keys if needed.

### 3. Fetch traces

Use the `list_traces` MCP tool with the constructed filter, limit, and sort parameters.

- `limit`: from `--limit` or default 25
- `filter`: from step 2 (omit if no filters)
- `sort_by`: `"timestamp:desc"` (default)

### 4. Display the summary

Present traces in a scannable format, not raw JSON.

**Header, overview line, and workspace link:**

```
# Orq.ai Traces — Recent Activity

Traces (last 24h): 47 total — 41 success, 4 error, 2 timeout
```

Then add: `View and filter traces at **[Traces → my.orq.ai](https://my.orq.ai/)**.`

**Then list traces grouped by status (errors first) using ASCII table format with full trace IDs:**
```
Errors (4)

  ┌───┬──────────────────┬──────────┬──────────────────────────────────┐
  │ # │    Time (UTC)    │ Duration │            Trace ID              │
  ├───┼──────────────────┼──────────┼──────────────────────────────────┤
  │ 1 │ Mar 13, 14:22:07 │ 209ms    │ b422a93c8b34ddfa70eaf5c7e3705c19 │
  │ 2 │ Mar 13, 13:01:06 │ 192ms    │ 4f1957728f7b0a6ba6b3ce0fd1679c4a │
  │ 3 │ Mar 13, 12:55:04 │ 228ms    │ 89d0a987ff11b23aca1f5b2e4d8efac2 │
  │ 4 │ Mar 13, 12:54:04 │ 297ms    │ c20e270e79257144f93b5beedcc6c62f │
  └───┴──────────────────┴──────────┴──────────────────────────────────┘

Success (showing 10 of 41)

  ┌───┬──────────────────┬──────────┬──────────────────────────────────┐
  │ # │    Time (UTC)    │ Duration │            Trace ID              │
  ├───┼──────────────────┼──────────┼──────────────────────────────────┤
  │ 1 │ Mar 13, 15:30:58 │ 493ms    │ 2d8781e8d628b89e9bde9fcdf7f80827 │
  │ 2 │ Mar 13, 15:28:57 │ 629ms    │ 201ee2b617ce05622bc8e1888703a0bd │
  └───┴──────────────────┴──────────┴──────────────────────────────────┘
```

#### Formatting rules

- Use ASCII box-drawing characters (`┌ ─ ┬ ┐ │ ├ ┤ └ ┴ ┘`) for the table borders.
- Show the **full trace ID** (all 32 hex characters) — never truncate it.
- Adapt fields based on what the API returns. Prioritize: timestamp, entity, error message or latency/tokens, trace ID.
- Show errors and failures first — they're why the user is here.
- Cap each group at 10 items. If there are more, show the count.
- Include the trace ID so the user can reference it in follow-up.

### 5. Suggest next steps

After displaying traces, if there are errors, suggest:

> "To analyze these failures in depth, use the `analyze-trace-failures` skill."

### 6. Error handling

- **Auth errors** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **No traces found** — "No traces found matching your filters. Try broadening the time window or removing filters."
- **MCP errors** — "Could not reach the orq.ai MCP server. Make sure it's configured: `claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header 'Authorization: Bearer ${ORQ_API_KEY}'`"
