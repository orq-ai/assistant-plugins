---
description: List available AI models and their capabilities
argument-hint: [search-term]
allowed-tools: Bash, AskUserQuestion, orq*
---

# Models

List the AI models available in the user's orq.ai workspace. Optionally filter by a search term.

## Instructions

### 1. Validate environment

Check that `$ORQ_API_KEY` is set:

```bash
if [ -z "$ORQ_API_KEY" ]; then echo "ERROR: ORQ_API_KEY is not set"; exit 1; fi
```

If missing, tell the user to set it and stop.

### 2. Parse arguments

`$ARGUMENTS` is optional. If provided, use it as a search term to filter models (e.g., "gpt-4", "claude", "embedding", "anthropic").

The search should be case-insensitive and match against model name, provider, or capabilities.

### 3. Fetch data

Try MCP tools first, fall back to `curl` if unavailable.

**MCP path:** Use `list_models` to retrieve available models.

**curl fallback:**
```bash
curl -s -H "Authorization: Bearer $ORQ_API_KEY" "https://api.orq.ai/v2/models"
```

### 4. Display models

Present models grouped by provider, formatted cleanly — not raw JSON:

```
Available Models
================

OpenAI
  gpt-4o              — chat, vision, tools       128k context
  gpt-4o-mini         — chat, tools               128k context
  o1                   — reasoning                 200k context
  text-embedding-3-large — embedding               8k context
  ...

Anthropic
  claude-sonnet-4-20250514  — chat, vision, tools  200k context
  claude-haiku-4-5-20251001 — chat, vision, tools  200k context
  ...
```

If a **search term** was provided, filter and show only matching models:

```
[search: "embed"]

Embedding Models
  text-embedding-3-large   — OpenAI      3072 dims
  text-embedding-3-small   — OpenAI      1536 dims
  ...
```

Adapt the fields based on what the API actually returns. Show the most useful attributes: model name, provider, capabilities, context window.

- If no models match the search term, say "No models found matching '<term>'. Try a broader search."
- Cap the list at 50 models per provider. If there are more, show the count and say "... and N more".

### 5. Error handling

- **401/403** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **Network error** — "Could not reach the orq.ai API. Check your internet connection."
