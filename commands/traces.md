---
name: traces
description: Query and summarize traces with filters — debugging entry point before orq-analyze-traces
argument-hint: "[--deployment <name>] [--status <status>] [--last <duration>] [--limit <n>]"
allowed-tools: AskUserQuestion, Bash(orq auth whoami:*), mcp__orq-workspace__list_traces, mcp__orq-workspace__list_registry_keys, mcp__orq-workspace__list_registry_values
---

# Traces

Query production traces from the orq.ai platform and display a summary. Use this as a debugging entry point — once you spot a problem, hand off to the `orq-analyze-traces` skill for deep analysis.

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

**Then list traces grouped by status (errors first), each trace as a link to it in Studio:**

```
Errors (4)

| # | Time (UTC)       | Duration | Trace |
|---|------------------|----------|-------|
| 1 | Mar 13, 14:22:07 | 209ms    | [b422a93c](<https://my.orq.ai/acme/traces/(trace:b422a93c8b34ddfa70eaf5c7e3705c19)>) |
| 2 | Mar 13, 13:01:06 | 192ms    | [4f195772](<https://my.orq.ai/acme/traces/(trace:4f1957728f7b0a6ba6b3ce0fd1679c4a)>) |

Success (showing 10 of 41)

| # | Time (UTC)       | Duration | Trace |
|---|------------------|----------|-------|
| 1 | Mar 13, 15:30:58 | 493ms    | [2d8781e8](<https://my.orq.ai/acme/traces/(trace:2d8781e8d628b89e9bde9fcdf7f80827)>) |
```

If a link opens on "No spans match your search", switch the Traces project selector to **All projects** and reload.

#### Formatting rules

- **Every trace is a link, never a bare id.** Build `<host>/<workspace-key>/traces/(trace:<trace_id>)` with the host and workspace key resolved as in the `orq-trace-format` skill (`skills/orq-trace-format/SKILL.md`); label it with the first 8 characters of the trace id. `my.orq.ai/acme` above is an example; never hardcode it.
- Only when neither the workspace key nor the host can be resolved, say so once and show the full 32-character trace id instead.
- Use a Markdown table so the links are clickable.
- Adapt fields based on what the API returns. Prioritize: timestamp, entity, error message or latency/tokens, trace link.
- Show errors and failures first — they're why the user is here.
- Cap each group at 10 items. If there are more, show the count.

### 5. Suggest next steps

After displaying traces, if there are errors, suggest:

> "To analyze these failures in depth, use the `orq-analyze-traces` skill."

### 6. Error handling

- **Auth errors** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **No traces found** — "No traces found matching your filters. Try broadening the time window or removing filters."
- **MCP errors** — "Could not reach the orq.ai MCP server. Make sure it's configured: `claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header 'Authorization: Bearer ${ORQ_API_KEY}'`"
