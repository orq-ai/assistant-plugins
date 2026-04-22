---
name: analytics
description: Show workspace analytics — requests, cost, tokens, errors, top models, and drill-down trends
argument-hint: "[--last <duration>] [--group-by <dimension>]"
allowed-tools: AskUserQuestion, orq*
---

# Analytics

Show workspace analytics from orq.ai — request volume, cost, token usage, error rates, and top models. Use this to get a quick health check or drill into usage trends.

## Instructions

### 1. Parse arguments

Extract options from `$ARGUMENTS`. All are optional:

- `--last <duration>` — time window: `1h`, `6h`, `24h`, `7d`, `30d` (default: `24h`)
- `--group-by <dimension>` — drill-down dimension: `model`, `deployment`, `status`, `agent` (optional)

If `$ARGUMENTS` is empty, show the overview for the last 24 hours.

If the user provides plain text instead of flags (e.g., `/orq:analytics cost by model last 7 days`), interpret the intent and map to the appropriate options.

### 2. Fetch analytics data

Run these MCP calls in parallel:

- **Overview:** Use `get_analytics_overview` to fetch the high-level summary (requests, cost, tokens, errors).
- **Drill-down (if `--group-by` provided):** Use `query_analytics` with the specified dimension to fetch grouped breakdowns.

If no `--group-by` is specified, still call `query_analytics` grouped by `model` to show the top models breakdown — this is almost always useful context.

### 3. Display the overview

Present analytics using native markdown formatting — **not** inside a code block.

**Overview format:**

```markdown
# Orq.ai Analytics — Last 24h

**12,450** requests · **$8.42** cost · **1.2M** tokens · **0.03%** error rate

View detailed analytics at **[Analytics → my.orq.ai](https://my.orq.ai/)**.

---

### Request Volume

- **Total:** 12,450 requests
- **Success:** 12,412 (99.97%)
- **Errors:** 38 (0.03%)
- **Avg latency:** 340ms
- **Avg TTFT:** 120ms

### Cost Breakdown

- **Total:** $8.42
- **Input tokens:** 890,000 ($3.12)
- **Output tokens:** 310,000 ($5.30)

### Top Models

| # | Model | Requests | Cost | Avg Latency |
|---|-------|----------|------|-------------|
| 1 | gpt-4.1 | 5,200 | $4.10 | 420ms |
| 2 | claude-sonnet-4-5 | 3,800 | $3.20 | 380ms |
| 3 | gpt-4.1-mini | 2,100 | $0.85 | 180ms |
| 4 | gemini-2.5-flash | 1,350 | $0.27 | 150ms |
```

**If `--group-by` is provided, add a drill-down section:**

```markdown
### Usage by [Dimension]

| # | [Dimension] | Requests | Cost | Error Rate |
|---|-------------|----------|------|------------|
| 1 | ... | ... | ... | ... |
| 2 | ... | ... | ... | ... |
```

#### Formatting rules

- **Summary line** — a single line at the top with bold counts separated by ` · `, showing key metrics at a glance.
- **`---` separator** — a horizontal rule after the summary line.
- **`### Section` headers** — each section gets a level-3 heading.
- **Bold numbers** — use `**N**` for key metrics.
- **No code block wrapper** — output raw markdown so bold, headers, and rules render natively.
- Format large numbers with commas (12,450). Format costs with 2 decimal places ($8.42). Format percentages with 2 decimal places (0.03%).
- Format token counts in shorthand (1.2M, 890K) for the summary line; use exact numbers in the breakdown.
- Cap tables at 10 rows. If there are more, show "... and N more".
- Adapt fields based on what the API actually returns. Show the most useful attributes available.

### 4. Suggest next steps

After displaying analytics:

- If error rate is elevated (>1%), suggest: "Error rate is elevated. Use `/orq:traces --status error` to inspect failing traces, then `analyze-trace-failures` for deep analysis."
- If cost is high for a model, suggest: "Consider using `/orq:models` to explore cheaper alternatives, or the `run-experiment` skill to compare model quality."
- Otherwise: "Use `--group-by deployment` or `--group-by agent` to drill into specific components."

### 5. Error handling

- **Auth errors** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **MCP errors** — "Could not reach the orq.ai MCP server. Make sure it's configured: `claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header 'Authorization: Bearer ${ORQ_API_KEY}'`"
- **No data** — "No analytics data found for the selected time window. Try a broader range with `--last 7d`."
- **Partial failure** — If one call fails but the other succeeds, show what you have and note what's missing.
