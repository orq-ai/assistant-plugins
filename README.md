# Orq.ai Agent Skills

Agent Skills for the full **Build → Evaluate → Optimize** lifecycle of LLM pipelines on [orq.ai](https://orq.ai). 

**Skills** are multi-step workflows that require reasoning (e.g. build an agent, run an experiment); 

**Commands** are quick actions for immediate results (list traces, show analytics). 

Each skill encodes best practices from prompt engineering, agent design, evaluation methodology, and experimentation into repeatable workflows. From creating agents and writing prompts, through trace analysis and dataset generation, to running validated experiments and iterating on results. 

Built on the [Agent Skills](https://agentskills.io/home#adoption) standard format, so it works with any compatible agent (Claude Code, Cursor, Gemini CLI, and others).


## Setup

### Prerequisites

- An [orq.ai](https://orq.ai) account
- An API key from [Settings → API Keys](https://my.orq.ai)
- The `ORQ_API_KEY` environment variable set:

  ```bash
  echo 'export ORQ_API_KEY=your-key-here'
  ```

---
### Install orq-skills

#### Option A — Claude Code plugin (recommended)

Installs skills, commands, agents, **and the MCP server** in one go.

```bash
# Add the marketplace
claude plugin marketplace add orq-ai/claude-plugins

# Install the MCP server and the skills bundle
claude plugin install orq-mcp@orq-claude-plugin
claude plugin install orq-skills@orq-claude-plugin
```

See the [claude-plugins repo](https://github.com/orq-ai/claude-plugins) for details on what each plugin ships.

---

#### Option B — Cursor plugin

Installs skills and MCP config:

```bash
# Symlink or copy to ~/.cursor/plugins/local/orq
ln -s /path/to/orq-skills ~/.cursor/plugins/local/orq
```

Cursor discovers components from default paths (`skills/`, `mcp.json`) in the plugin root.

---

#### Option C — Codex plugin

Installs skills and MCP config:

```bash
# This repository includes a ready marketplace entry at:
# .agents/plugins/marketplace.json
# (plugin source path: "./plugins/orq")
```

See [Codex plugin docs](https://developers.openai.com/codex/plugins/build) for installation details.

---

#### Option D — Manual MCP installation + npx skills CLI

For any other Agent Skills–compatible agent. Two steps:

**1. Register the `orq-workspace` MCP server.**

For Claude Code:

```bash
claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp \
  --header "Authorization: Bearer ${ORQ_API_KEY}"
```

For Cursor, Gemini CLI, Codex, Cline, Copilot CLI, Windsurf, and others — see [MANUAL_INSTALL.md](MANUAL_INSTALL.md) for the per-tool MCP config.

**2. Install the skills:**

```bash
npx skills add orq-ai/orq-skills
```

`npx skills add` auto-detects Claude Code, Cursor, Gemini CLI, Copilot, Cline, Codex, and Windsurf and installs skills into the right directory. If you'd rather copy the skill folders by hand, see [MANUAL_INSTALL.md](MANUAL_INSTALL.md).

> **Note:** Commands (`/orq:quickstart`, `/orq:workspace`, …) and agents are only bundled with Option A. Other options give you skills only.

---

### Verify

Run the interactive onboarding — it checks your `ORQ_API_KEY`, confirms the MCP server is reachable, and validates your credentials against a live API call:

```
/orq:quickstart
```

Smoke test Cursor:

1. Restart Cursor after installing/symlinking the plugin.
2. Open Cursor settings and confirm `orq` skills are available.
3. Open MCP settings and confirm `orq-workspace` can be enabled from `mcp.json`.

Smoke test Codex:

1. Ensure Codex can read this repository's `.agents/plugins/marketplace.json`.
2. Install the `orq` plugin from that marketplace.
3. Confirm the plugin exposes skills and the `orq-workspace` MCP server.

Validate manifests:

```bash
tests/scripts/validate-plugin-manifests.sh
```

---

## Commands

Quick-action slash commands. Use `/orq:<command>` in Claude Code.

| Command | What It Does | Usage |
|---------|-------------|-------|
| **quickstart** | Interactive onboarding — credentials, MCP setup, skills tour | `/orq:quickstart` |
| **workspace** | Workspace overview — agents, deployments, prompts, datasets, experiments | `/orq:workspace [section]` |
| **traces** | Query and summarize traces with filters | `/orq:traces [--deployment name] [--status error] [--last 24h]` |
| **models** | List available AI models by provider | `/orq:models [search-term]` |
| **analytics** | Usage analytics — requests, cost, tokens, errors | `/orq:analytics [--last 24h] [--group-by model]` |

### Examples

```
/orq:workspace agents          # Show only agents
/orq:traces --status error --last 1h   # Recent errors
/orq:models gpt-4              # Search for GPT-4 variants
/orq:analytics --group-by deployment    # Cost per deployment
```

---

## Skills

Skills are triggered by describing what you need. Claude picks the right skill automatically.

<!-- BEGIN_SKILLS_TABLE -->
| Skill | What It Does | Documentation |
|-------|-------------|---------------|
| **setup-observability** | Set up orq.ai observability for existing LLM applications — AI Router proxy, OpenTelemetry, `@traced` decorator, and trace enrichment | [SKILL.md](skills/setup-observability/SKILL.md) |
| **build-agent** | Design, create, and configure an orq.ai Agent with tools, instructions, knowledge bases, and memory | [SKILL.md](skills/build-agent/SKILL.md) |
| **build-evaluator** | Create validated LLM-as-a-Judge evaluators following evaluation best practices | [SKILL.md](skills/build-evaluator/SKILL.md) |
| **analyze-trace-failures** | Read production traces, identify what's failing, build failure taxonomies, and categorize issues | [SKILL.md](skills/analyze-trace-failures/SKILL.md) |
| **run-experiment** | Create and run orq.ai experiments — compare configurations with specialized agent, conversation, and RAG evaluation | [SKILL.md](skills/run-experiment/SKILL.md) |
| **generate-synthetic-dataset** | Generate and curate evaluation datasets — structured generation, quick from description, expansion, and dataset maintenance | [SKILL.md](skills/generate-synthetic-dataset/SKILL.md) |
| **optimize-prompt** | Analyze and optimize system prompts using a structured prompting guidelines framework | [SKILL.md](skills/optimize-prompt/SKILL.md) |
<!-- END_SKILLS_TABLE -->

---

## Workflows

### 1. Instrument an Existing App

```
"Add orq.ai tracing to my app"               → setup-observability
/orq:traces --last 1h                          # Verify traces are flowing
"Analyze these traces for failures"            → analyze-trace-failures
```

### 2. Build a New Agent

```
"I need a customer support agent"             → build-agent
"Create test cases for it"                     → generate-synthetic-dataset
"Build an evaluator for response accuracy"     → build-evaluator
"Run an experiment to get a baseline"          → run-experiment
```

### 3. Debug Production Issues

```
/orq:traces --status error --last 24h          # Find errors
"Analyze these failures"                       → analyze-trace-failures
"Fix the prompt based on the failure analysis" → optimize-prompt
"Re-run the experiment to verify the fix"      → run-experiment
```

### 4. Improve an Existing Agent

```
/orq:analytics --group-by deployment           # Spot high error rates
"Analyze traces for the checkout agent"        → analyze-trace-failures
"Build evaluators for the failure modes"       → build-evaluator
"Generate a dataset covering edge cases"       → generate-synthetic-dataset
"Run an experiment and compare"                → run-experiment
"Optimize the prompt based on results"         → optimize-prompt
```

### 5. Improve an Existing Prompt

```
"My prompt isn't performing well, help me improve it" → optimize-prompt
"Create test cases to compare before and after"       → generate-synthetic-dataset
"Build an evaluator for [specific dimension]"         → build-evaluator
"Run an experiment: current vs optimized prompt"     → run-experiment
"Refine the prompt based on failure cases"            → optimize-prompt
```

---

## Links

- [orq.ai Dashboard](https://my.orq.ai)
- [Documentation](https://docs.orq.ai)
- [GitHub Repository](https://github.com/orq-ai/orq-skills)
- [Agent Skills Standard](https://agentskills.io)
