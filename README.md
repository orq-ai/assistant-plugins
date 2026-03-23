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


### Connect the MCP Server

Make sure you have Orq.ai MCP configured. The MCP server gives skills and commands access to your Orq.ai workspace.

```bash
# Set your API key
export ORQ_API_KEY=your-key-here  # add to ~/.zshrc or ~/.bashrc

# Connect the MCP server (run once)
claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp \
  --header "Authorization: Bearer ${ORQ_API_KEY}"
```

### Install orq-skills

Pick one method:

```bash
# Option 1: npx skills CLI (works with Cursor, Gemini CLI, etc.)
npx skills add orq-ai/orq-skills

# Option 2: Claude Code plugin marketplace
/plugin marketplace add orq-ai/orq-skills
/plugin install orq@orq-skills

# Option 3: Direct from GitHub
/plugin install github:orq-ai/orq-skills

# Option 4: Manual clone
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills
claude --plugin-dir .
```


### Verify

Run the interactive onboarding to confirm everything works:

```
/orq:quickstart
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
| **build-agent** | Design, create, and configure an orq.ai Agent with tools, instructions, knowledge bases, and memory | [SKILL.md](skills/build-agent/SKILL.md) |
| **build-evaluator** | Create validated LLM-as-a-Judge evaluators following evaluation best practices | [SKILL.md](skills/build-evaluator/SKILL.md) |
| **analyze-trace-failures** | Read production traces, identify what's failing, build failure taxonomies, and categorize issues | [SKILL.md](skills/analyze-trace-failures/SKILL.md) |
| **run-experiment** | Create and run orq.ai experiments — compare configurations with specialized agent, conversation, and RAG evaluation | [SKILL.md](skills/run-experiment/SKILL.md) |
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