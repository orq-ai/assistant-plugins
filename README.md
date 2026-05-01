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

  ```bash
  export ORQ_API_KEY=your-key-here
  ```

### Quick reference

| Tool | Recommended install |
|------|---------------------|
| Claude Code (CLI) | [Claude Code plugin](#claude-code-plugin) |
| Claude Cowork (Desktop) | [Claude Cowork install guide](docs/install-claude-cowork.md) |
| Cursor | [Cursor install guide](docs/install-cursor.md) |
| Codex | [Codex install guide](docs/install-codex.md) |
| Gemini CLI, Cline, Copilot, Windsurf | [Skills-only install (npx)](#skills-only-install) |
| Any MCP-capable client | [MCP-only install](#mcp-only-install) |

---

### Claude Code plugin

Use this if you want easy access to all components — skills, MCP tools, and trace hooks — in one install. Installed via the [orq-ai/assistant-plugins](https://github.com/orq-ai/assistant-plugins) marketplace.

```bash
# In Claude Code:
/plugin marketplace add orq-ai/assistant-plugins

# Install all 3 plugins
/plugin install orq-skills@assistant-plugins
/plugin install orq-mcp@assistant-plugins
/plugin install orq-trace@assistant-plugins
```

| Plugin | What it gives you |
|--------|-------------------|
| `orq-skills` | Skills, commands, and agents for the Build → Evaluate → Optimize lifecycle |
| `orq-mcp` | MCP server registration — Claude can call orq.ai APIs directly |
| `orq-trace` | OTLP tracing hooks that capture Claude Code sessions into orq.ai |

Verify with the interactive onboarding — checks `ORQ_API_KEY`, MCP reachability, and credentials:

```
/orq:quickstart
```

---

### Skills-only install

Use this when you're on a non-Claude agent (Cursor, Gemini CLI, Cline, Copilot CLI, Codex, Windsurf, and [many others](https://www.npmjs.com/package/skills)), or when you only want the skills without MCP/trace hooks.

```bash
npx skills add orq-ai/assistant-plugins
```

Auto-detects your agent and writes skills to the correct location (e.g. `.claude/skills/`, `.cursor/rules/`). Run inside your project directory.

Agent-specific install guides:

- [Cursor plugin](docs/install-cursor.md)
- [Codex plugin](docs/install-codex.md)
- [Manual clone (Claude Code)](docs/install-manual.md)

---

### MCP-only install

Use this when you want orq.ai MCP tools in a tool that isn't the Claude Code plugin (Claude Desktop, other MCP-capable clients, or manual Claude Code setup).

```bash
# Manual registration in Claude Code
claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp \
  --header "Authorization: Bearer ${ORQ_API_KEY}"
```

For other clients, most accept a JSON block with `url` + `headers`:

```json
{
  "mcpServers": {
    "orq-workspace": {
      "type": "http",
      "url": "https://my.orq.ai/v2/mcp",
      "headers": { "Authorization": "Bearer ${ORQ_API_KEY}" }
    }
  }
}
```

---

### Manifest validation

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
| **setup-observability** | Set up orq.ai observability for LLM applications — AI Router proxy, OpenTelemetry, tracing setup, and trace enrichment | [SKILL.md](skills/setup-observability/SKILL.md) |
| **invoke-deployment** | Invoke orq.ai deployments, agents, and models via the Python SDK or HTTP API — pass prompt variables, stream responses, and generate integration code | [SKILL.md](skills/invoke-deployment/SKILL.md) |
| **build-agent** | Design, create, and configure an orq.ai Agent with tools, instructions, knowledge bases, and memory | [SKILL.md](skills/build-agent/SKILL.md) |
| **build-evaluator** | Create validated LLM-as-a-Judge evaluators following evaluation best practices | [SKILL.md](skills/build-evaluator/SKILL.md) |
| **analyze-trace-failures** | Read production traces, identify what's failing, build failure taxonomies, and categorize issues | [SKILL.md](skills/analyze-trace-failures/SKILL.md) |
| **run-experiment** | Create and run orq.ai experiments — compare configurations with specialized agent, conversation, and RAG evaluation | [SKILL.md](skills/run-experiment/SKILL.md) |
| **compare-agents** | Run cross-framework agent comparisons using evaluatorq — compare orq.ai, LangGraph, CrewAI, OpenAI Agents SDK, and others | [SKILL.md](skills/compare-agents/SKILL.md) |
| **generate-synthetic-dataset** | Generate and curate evaluation datasets — structured generation, quick from description, expansion, and dataset maintenance | [SKILL.md](skills/generate-synthetic-dataset/SKILL.md) |
| **optimize-prompt** | Analyze and optimize system prompts using a structured prompting guidelines framework | [SKILL.md](skills/optimize-prompt/SKILL.md) |
<!-- END_SKILLS_TABLE -->

---

## Workflows

### 1. Build a New Agent

```
"I need a customer support agent"             → build-agent
"Create test cases for it"                     → generate-synthetic-dataset
"Build an evaluator for response accuracy"     → build-evaluator
"Run an experiment to get a baseline"          → run-experiment
```

### 2. Debug Production Issues

```
/orq:traces --status error --last 24h          # Find errors
"Analyze these failures"                       → analyze-trace-failures
"Fix the prompt based on the failure analysis" → optimize-prompt
"Re-run the experiment to verify the fix"      → run-experiment
```

### 3. Improve an Existing Agent

```
/orq:analytics --group-by deployment           # Spot high error rates
"Analyze traces for the checkout agent"        → analyze-trace-failures
"Build evaluators for the failure modes"       → build-evaluator
"Generate a dataset covering edge cases"       → generate-synthetic-dataset
"Run an experiment and compare"                → run-experiment
"Optimize the prompt based on results"         → optimize-prompt
```

### 4. Improve an existing Prompt

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
- [GitHub Repository](https://github.com/orq-ai/assistant-plugins)
- [Agent Skills Standard](https://agentskills.io)
