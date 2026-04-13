---
name: models
description: List available AI models and their capabilities
argument-hint: [search-term]
allowed-tools: AskUserQuestion, orq*
---

# Models

List the AI models available in the user's orq.ai workspace. Optionally filter by a search term.

## Instructions

### 1. Parse arguments

`$ARGUMENTS` is optional. If provided, use it as a search term to filter models (e.g., "gpt-4", "claude", "embedding", "anthropic").

The search should be case-insensitive and match against model name, provider, or capabilities.

If the search term matches a model type (e.g., "chat", "embedding", "image", "tts", "stt", "rerank", "ocr"), use it as the `modelType` parameter directly.

### 2. Fetch data

Use the `list_models` MCP tool to retrieve available models. **Important:** The `modelType` parameter is always required — never call `list_models` without it.

- If a model type was identified from the search term, pass it as `modelType`.
- Otherwise, fetch the default types by calling `list_models` **three times in parallel** with: `modelType: "chat"`, `modelType: "completion"`, and `modelType: "embedding"`.

### 3. Display models

Present a clean summary using native markdown formatting (bold, headers, horizontal rules) — **not** inside a code block. This renders well in Claude Code's monospace terminal.

Output the models in this format:

```markdown
# Orq.ai AI Router — Active Models

**12** chat · **3** embedding · **2** image

Models enabled in your workspace. Add providers or enable more models at **[AI Router → Models](https://my.orq.ai/)**.

---

### OpenAI (6)

- **gpt-5** — chat · 1M context
- **gpt-5-mini** — chat · 1M context
- **gpt-4.1** — chat · 1M context
- **text-embedding-3-large** — embedding · 3072 dims
- **text-embedding-3-small** — embedding · 1536 dims
- ... and 1 more

### Anthropic (3)

- **claude-sonnet-4-20250514** — chat · 200k context
- **claude-haiku-4-5-20251001** — chat · 200k context
- **claude-opus-4-1-20250805** — chat · 200k context

### Google (2)

- **gemini-2.5-pro** — chat · 1M context
- **gemini-2.5-flash** — chat · 1M context
```

If a **search term** was provided, filter and show only matching models:

```markdown
# Orq.ai AI Router — Active Models [search: "embed"]

**3** embedding

Models enabled in your workspace. Add providers or enable more models at **[AI Router → Models](https://my.orq.ai/)**.

---

### OpenAI (2)

- **text-embedding-3-large** — embedding · 3072 dims
- **text-embedding-3-small** — embedding · 1536 dims

### Google (1)

- **text-embedding-004** — embedding · 768 dims
```

#### Formatting rules

- **Summary line** — a single line at the top with bold counts separated by ` · `, showing totals per model type.
- **`---` separator** — a horizontal rule after the summary line to visually separate the overview from the detail sections.
- **`### Provider (N)` headers** — each provider gets a level-3 heading with its model count.
- **Bold model names** — use `**name**` to visually anchor each list entry.
- **`·` delimiters for metadata** — use ` · ` (middle dot) to separate secondary attributes within a list item (e.g., type · context window). Use ` — ` (em dash) to separate the name from its metadata.
- **No code block wrapper** — output raw markdown so bold, headers, and rules render natively.
- Adapt the fields based on what the API actually returns. Show the most useful attributes: model name, type, context window.
- If no models match the search term, say "No models found matching '<term>'. Try a broader search."
- Cap each provider at 10 models. If there are more, show the count and say "... and N more".

### 4. Error handling

- **Auth errors** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **MCP errors** — "Could not reach the orq.ai MCP server. Make sure it's configured: `claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header 'Authorization: Bearer ${ORQ_API_KEY}'`"
