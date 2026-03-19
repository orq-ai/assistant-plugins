---
description: Show a workspace overview — agents, deployments, prompts, datasets, experiments, projects, and knowledge bases
argument-hint: [section]
allowed-tools: AskUserQuestion, orq*
---

# Workspace

Show a quick overview of the user's orq.ai workspace — agents, deployments, prompts, datasets, experiments, projects, and knowledge bases. Optionally filter to a specific section.

## Instructions

### 1. Parse arguments

`$ARGUMENTS` is optional. If provided, it narrows the output to a specific section:
- `agents` — show only agents
- `deployments` — show only deployments
- `prompts` — show only prompts
- `datasets` — show only datasets
- `experiments` — show only experiments
- `projects` — show only projects
- `knowledge` — show only knowledge bases

If empty, show all sections.

### 2. Fetch data

Use the `search_entities` MCP tool to fetch each section. Run all applicable calls in parallel.

- **Agents:** `search_entities` with `type: "agent"`
- **Deployments:** `search_entities` with `type: "deployment"`
- **Prompts:** `search_entities` with `type: "prompt"`
- **Datasets:** `search_entities` with `type: "dataset"`
- **Experiments:** `search_entities` with `type: "experiment"`
- **Projects:** `search_entities` with `type: "project"`
- **Knowledge:** `search_entities` with `type: "knowledge"`

Fetch only the sections needed based on arguments.

### 3. Display the overview

Present a clean summary using native markdown formatting (bold, headers, horizontal rules) — **not** inside a code block. This renders well in Claude Code's monospace terminal.

Output the overview in this format:

```markdown
# Orq.ai Workspace — Overview

**3** agents · **5** deployments · **8** prompts · **2** datasets · **1** experiment · **2** projects · **2** knowledge bases

Manage your workspace at **[Workspace → my.orq.ai](https://my.orq.ai/)**.

---
### Projects (2)

- **customer-support** — 3 agents, 5 prompts
- **internal-tools** — 1 agent, 2 prompts

### Agents (3)

- **customer-support-bot** — gpt-4o · 2 tools · 1 KB
- **onboarding-agent** — claude-sonnet · 3 tools
- **internal-qa** — gpt-4o-mini

### Deployments (5)

- **summarizer** v2.1 — active
- **classifier** v1.0 — active
- ... and 3 more

### Prompts (8)

- **support-response** — 4 versions, latest: v4
- **extract-entities** — 2 versions, latest: v2
- ... and 6 more

### Datasets (2)

- **eval-customer-queries** — 150 datapoints
- **edge-cases** — 42 datapoints

### Experiments (1)

- **prompt-comparison-mar** — completed · 3 evaluators


### Knowledge Bases (2)

- **product-docs** — 120 documents
- **faq-database** — 45 documents
```

#### Formatting rules

- **Summary line** — a single line at the top with bold counts separated by ` · `, showing all section totals at a glance.
- **`---` separator** — a horizontal rule after the summary line to visually separate the overview from the detail sections.
- **`### Section (N)` headers** — each section gets a level-3 heading with its item count.
- **Bold item names** — use `**name**` to visually anchor each list entry.
- **`·` delimiters for metadata** — use ` · ` (middle dot) to separate secondary attributes within a list item (e.g., model · tools · size). Use ` — ` (em dash) to separate the name from its metadata.
- **No code block wrapper** — output raw markdown so bold, headers, and rules render natively.
- Adapt the fields based on what the API actually returns. Show the most useful attributes: name, model, version count, status, size. Keep it scannable.
- If a section is empty, show the header with `(none)` below it instead of omitting the section.
- Cap each section at 10 items. If there are more, show the count and say "... and N more".

### 4. Error handling

- **Auth errors** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **MCP errors** — "Could not reach the orq.ai MCP server. Make sure it's configured: `claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header 'Authorization: Bearer ${ORQ_API_KEY}'`"
- **Partial failure** — If some calls fail but others succeed, show what you have and note which sections failed.
