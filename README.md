# Orq.ai Agent Skills

Agent Skills for building, evaluating, and improving LLM pipelines on the [orq.ai](https://orq.ai) platform.

Each skill encodes best practices from prompt engineering, agent design, evaluation methodology, and experimentation into repeatable workflows. 

Together they cover the full **Build → Evaluate → Optimize** lifecycle — from creating agents and writing prompts, through trace analysis and dataset generation, to running validated experiments and iterating on results.

Built on the [Agent Skills](https://agentskills.io/home#adoption) standard format — works with any compatible agent (Claude Code, Cursor, Gemini CLI, and others).

## Installation

Using npx skills (Cursor, Claude Code, Gemini CLI, etc.)
```bash
npx skills add orq-ai/orq-skills
```

Using Claude Code Plugin Manager
```bash
/plugin install orq-ai@orq-skills
```

Manual: clone and point your agent to the directory
```bash
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills
claude --plugin-dir .
```

## Quickstart

New to orq.ai? Run the interactive onboarding to set up your credentials, connect the MCP server, and get a guided tour of all available commands and skills:

```
/orq:quickstart
```

## Requirements

- An [orq.ai](https://orq.ai) account with an API key (`ORQ_API_KEY` environment variable)
- The orq.ai MCP server connected to Claude Code:
  ```bash
  claude mcp add --transport http orq https://my.orq.ai/v2/mcp \
    --header "Authorization: Bearer $ORQ_API_KEY"
  ```

## Commands

Quick-action slash commands for common operations. Commands are thin orchestrators — they parse arguments, call the API, and display results.

| Command | What It Does | Usage |
|---------|-------------|-------|
| **quickstart** | Interactive onboarding — credentials, MCP setup, tour | `/orq:quickstart` |
| **workspace** | Show workspace overview — agents, deployments, prompts, datasets, experiments | `/orq:workspace [section]` |
| **traces** | Query and summarize traces with filters — debugging entry point | `/orq:traces [--deployment <name>] [--status <status>] [--last <duration>]` |
| **models** | List available AI models and capabilities | `/orq:models [search-term]` |

**Commands vs skills:** Commands are for quick, focused actions (list, query, inspect). Skills are for multi-step workflows that require reasoning and decision-making (build, evaluate, optimize).

## Available Skills

<!-- BEGIN_SKILLS_TABLE -->
| Skill | What It Does | Documentation |
|-------|-------------|---------------|
| **build-agent** | Design, create, and configure an orq.ai Agent with tools, instructions, knowledge bases, and memory | [SKILL.md](skills/build-agent/SKILL.md) |
| **build-evaluator** | Create validated LLM-as-a-Judge evaluators following evaluation best practices | [SKILL.md](skills/build-evaluator/SKILL.md) |
| **analyze-trace-failures** | Read production traces, identify what's failing, build failure taxonomies, and categorize issues | [SKILL.md](skills/analyze-trace-failures/SKILL.md) |
| **run-experiment** | Create and run orq.ai experiments — compare configurations with specialized agent, conversation, and RAG evaluation | [SKILL.md](skills/run-experiment/SKILL.md) |
| **generate-synthetic-dataset** | Generate and curate evaluation datasets — structured generation, quick from description, expansion, and dataset maintenance | [SKILL.md](skills/generate-synthetic-dataset/SKILL.md) |
| **optimize-prompt** | Analyze and optimize system prompts using a structured prompting guidelines framework | [SKILL.md](skills/optimize-prompt/SKILL.md) |
| **prompt-learning** | Automatically improve prompts by collecting feedback, generating rules via a meta-prompt, and validating with multi-judge experiments | [SKILL.md](skills/prompt-learning/SKILL.md) |
<!-- END_SKILLS_TABLE -->

## Usage

### Commands

Commands are invoked with `/orq:<command>` in Claude Code.

```
/orq:quickstart
```
Interactive onboarding — set up credentials, connect to orq.ai, and tour all commands and skills.

```
/orq:workspace
```
Show workspace overview — agents, deployments, prompts, datasets, experiments.

```
/orq:traces --status error --last 24h
```
Query and summarize traces. Useful as a debugging entry point before deeper analysis.

```
/orq:models gpt
```
List available AI models, optionally filtered by search term.

### Skills

Skills are triggered by describing what you need in natural language. Claude picks the right skill automatically.

```
"I need a customer support agent with access to our order database"
→ build-agent
```

```
"My support agent is giving bad answers, help me figure out why"
→ analyze-trace-failures → build-evaluator → run-experiment
```

```
"I need to create test cases for my product recommendation agent"
→ generate-synthetic-dataset
```

```
"My prompt keeps breaking character, help me fix it"
→ optimize-prompt
```