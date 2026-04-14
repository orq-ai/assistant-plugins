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

### Connect the MCP Server

The MCP server gives skills and commands access to your orq.ai workspace.

```bash
# Set your API key (add to ~/.zshrc or ~/.bashrc to persist)
export ORQ_API_KEY=your-key-here
```

> **Skip the rest of this section if you're using Option 1, 2, or 3 below** — those plugins register the `orq-workspace` MCP server automatically from the bundled manifest. You only need to register it manually for Options 4–5 or when connecting a tool that doesn't use the plugin format.

For manual registration in Claude Code:

```bash
claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp \
  --header "Authorization: Bearer ${ORQ_API_KEY}"
```

### Install orq-skills

**Option 1: Claude Code plugin (recommended)** — installs skills, commands, and agents:
```bash
/plugin install github:orq-ai/orq-skills
```

**Option 2: Cursor plugin** — installs skills and MCP config.

The repo root is a Cursor plugin (`.cursor-plugin/plugin.json` declares `./skills/` and `./.mcp.json`). Cursor loads local plugins from `~/.cursor/plugins/local/<name>`:

```bash
# 1. Clone the repo
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills

# 2. Symlink into Cursor's local plugins directory
mkdir -p ~/.cursor/plugins/local
ln -s "$(pwd)" ~/.cursor/plugins/local/orq

# 3. Export your API key (the MCP config references ${ORQ_API_KEY})
export ORQ_API_KEY=your-key-here
```

Restart Cursor (or run **Developer: Reload Window**). Verify: **Settings → Rules** should list the `orq` skills under *Agent Decides*, and **Settings → Features → Model Context Protocol** should let you enable `orq-workspace`. Then ask in chat: *"List my orq.ai agents."*

*Testing as a Cursor marketplace:* to smoke-test the repo as if it were a Cursor team marketplace before submitting to [cursor.com/marketplace/publish](https://cursor.com/marketplace/publish), add a `.cursor-plugin/marketplace.json` at the repo root and import the repo via **Dashboard → Settings → Plugins → Team Marketplaces → Import** with your GitHub URL. Team marketplaces require a Teams or Enterprise plan.

**Option 3: Codex plugin** — installs skills and MCP config.

The repo ships a Codex plugin at [plugins/orq](plugins/orq) (`plugins/orq/.codex-plugin/plugin.json`) and a repo-level marketplace at [.agents/plugins/marketplace.json](.agents/plugins/marketplace.json). Codex reads marketplace manifests from two locations: `$REPO_ROOT/.agents/plugins/marketplace.json` (repo-level) and `~/.agents/plugins/marketplace.json` (personal).

Repo install — test the bundled marketplace in place:

```bash
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills
export ORQ_API_KEY=your-key-here

# Launch Codex from the repo root so it picks up .agents/plugins/marketplace.json
codex
```

Restart Codex and verify the `orq` plugin appears in the plugin directory — the manifest registers the skills folder and the `orq-workspace` MCP server automatically.

Personal install — use the plugin outside this repo:

```bash
# Copy the plugin bundle into Codex's personal plugins dir
mkdir -p ~/.codex/plugins
cp -r plugins/orq ~/.codex/plugins/orq

# Reference it in your personal marketplace (use an absolute path —
# tilde expansion is not guaranteed inside JSON string values)
mkdir -p ~/.agents/plugins
cat > ~/.agents/plugins/marketplace.json <<JSON
{
  "name": "personal",
  "plugins": [
    {
      "name": "orq",
      "source": { "source": "local", "path": "$HOME/.codex/plugins/orq" }
    }
  ]
}
JSON

export ORQ_API_KEY=your-key-here
```

Restart Codex. See the [Codex plugin docs](https://developers.openai.com/codex/plugins/build) for the full plugin spec.

**Option 4: npx skills CLI** — installs skills only (works with Cursor, Gemini CLI, Cline, Copilot CLI, Windsurf, etc.):
```bash
npx skills add orq-ai/orq-skills
```
Then register the `orq-workspace` MCP server in your tool using the HTTP config shown in "Connect the MCP Server" above — most tools accept a JSON block with `url` + `headers`. Check your tool's MCP docs for the exact config file path.

**Option 5: Manual clone** — with Claude Code:
```bash
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills
claude --plugin-dir .
```

> **Note:** Commands (`/orq:quickstart`, `/orq:workspace`, etc.) and agents are only available when installed as a Claude Code plugin.


### Verify

**Claude Code (Option 1 or 5):** run the interactive onboarding — it checks `ORQ_API_KEY`, confirms the MCP server is reachable, and validates your credentials with a live API call.

```
/orq:quickstart
```

**Cursor (Option 2):** restart Cursor, confirm `orq` skills appear under *Settings → Rules*, enable `orq-workspace` under *Settings → Features → MCP*, and ask the chat *"List my orq.ai agents."*

**Codex (Option 3):** restart Codex from the repo root, confirm the `orq` plugin appears in the plugin directory, and ask *"List my orq.ai agents."*

**All options:** validate the plugin manifests with

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
| **invoke-deployment** | Invoke orq.ai deployments, agents, and models — pass prompt variables, stream responses, and generate Python SDK or curl integration code | [SKILL.md](skills/invoke-deployment/SKILL.md) |
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
- [GitHub Repository](https://github.com/orq-ai/orq-skills)
- [Agent Skills Standard](https://agentskills.io)
