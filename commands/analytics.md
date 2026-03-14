---
description: Show performance metrics — latency, cost, token usage, and error rates across deployments
argument-hint: [--deployment <name>] [--last <duration>]
allowed-tools: Bash, AskUserQuestion, orq*
---

# Analytics

Show performance metrics for the user's orq.ai workspace — latency, cost, token usage, and error rates. Optionally filter by deployment or time window.

## Instructions

### 1. Validate environment

Check that `$ORQ_API_KEY` is set:

```bash
if [ -z "$ORQ_API_KEY" ]; then echo "ERROR: ORQ_API_KEY is not set"; exit 1; fi
```

If missing, tell the user to set it and stop.

### 2. Parse arguments

Extract filters from `$ARGUMENTS`. All are optional:

- `--deployment <name>` — filter metrics to a specific deployment
- `--last <duration>` — time window: `1h`, `6h`, `24h`, `7d`, `30d` (default: `24h`)

If `$ARGUMENTS` is empty, use defaults (last 24h, all deployments).

If the user provides plain text instead of flags (e.g., `/orq:analytics last week for summarizer`), interpret the intent and map to the appropriate filters.

### 3. Convert time window

Convert the `--last` duration to an ISO 8601 timestamp for the API query:

```bash
date -u -v-24H +"%Y-%m-%dT%H:%M:%SZ"  # macOS: 24 hours ago
```

Adjust the offset based on the duration provided.

### 4. Fetch data

Try MCP tools first, fall back to `curl` if unavailable.

**MCP path:** Use `get_analytics_overview` for a high-level summary. Use `query_analytics` for deployment-level drill-down or filtered data.

**curl fallback:**
```bash
curl -s -H "Authorization: Bearer $ORQ_API_KEY" \
  "https://api.orq.ai/v2/analytics/overview?start_date=START_DATE&end_date=END_DATE"
```

Add query parameters based on parsed filters (deployment, date range).

Run all applicable requests in parallel using multiple Bash calls.

### 5. Display dashboard

Present a clean, formatted dashboard — not raw JSON:

```
Analytics (last 24h)
====================

Overview
  Total requests: 1,234
  Avg latency:    420ms
  Total tokens:   89,200 (in: 62,100 / out: 27,100)
  Total cost:     $2.34
  Error rate:     2.1% (26 errors)

By Deployment
  summarizer     — 800 req, 380ms avg, $1.20, 1.2% errors
  classifier     — 434 req, 490ms avg, $1.14, 3.5% errors
```

Adapt the fields based on what the API actually returns. Show the most useful metrics: request count, latency, token usage, cost, error rate.

- If filtering by deployment, show only that deployment's metrics with more detail.
- If no data exists for the time window, say "No analytics data found for the selected time window. Try a broader range."
- Format numbers for readability (commas, appropriate units).

### 6. Error handling

- **401/403** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **Network error** — "Could not reach the orq.ai API. Check your internet connection."
- **Partial failure** — If some data is unavailable, show what you have and note what's missing.
