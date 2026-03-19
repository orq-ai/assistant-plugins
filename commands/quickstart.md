---
description: Interactive onboarding guide — set up credentials, connect to orq.ai, and learn every command and skill
argument-hint:
allowed-tools: Bash, AskUserQuestion, orq*
---
<!-- Note: Bash is kept for env var check and MCP setup command only. All data fetching uses MCP tools. -->

# Quickstart

Interactive onboarding guide for the orq.ai plugin. Walks the user through credential setup, connection verification, and a tour of all commands and skills.

## Instructions

### 1. Welcome & Orientation

Greet the user and give a brief overview:

> **Welcome to the orq.ai plugin for Claude Code!**
>
> This plugin gives you **6 commands** (quick actions) and **6 skills** (multi-step workflows) for building, evaluating, and improving LLM pipelines on the orq.ai platform.
>
> **The lifecycle:** Build → Deploy → Monitor → Evaluate → Optimize

Then use `AskUserQuestion` to determine the user's setup state:

- **"Brand new to orq.ai"** → Go to Section 2
- **"I have an API key but haven't set up the plugin"** → Go to Section 3
- **"I'm all set up"** → Go to Section 4

### 2. Account & API Key Setup

Direct the user to create an account and generate an API key:

> 1. Go to [my.orq.ai](https://my.orq.ai) and create an account (or sign in)
> 2. Navigate to **Settings → API Keys**
> 3. Click **Create API Key** and copy the key

Then continue to Section 3.

### 3. Environment Variable Setup

**Security rule:** Never ask the user to paste their API key in chat.

Tell the user:

> **To set your API key securely:**
>
> 1. Exit this session: type `/exit`
> 2. Add the key to your shell profile so it persists across sessions:
>    - **zsh** (macOS default): `echo 'export ORQ_API_KEY=your-key-here' >> ~/.zshrc && source ~/.zshrc`
>    - **bash**: `echo 'export ORQ_API_KEY=your-key-here' >> ~/.bashrc && source ~/.bashrc`
> 3. Restart Claude Code: `claude`
> 4. Come back here: `/orq:quickstart`

**For brand-new users:** Stop here after giving these instructions.

**For returning users** (who say they already have the key set): Verify with a non-leaking check:

```bash
if [ -z "$ORQ_API_KEY" ]; then echo "NOT_SET"; else echo "SET"; fi
```

If `NOT_SET`, repeat the setup instructions above. If `SET`, continue to Section 4.

### 4. Verify Connection

Test the connection by making an MCP call: use `search_entities` with `type: "agent"`.

- **Success** — Connection verified. Show a workspace snapshot by running the equivalent of `/orq:workspace` inline (use `search_entities` for agents, deployments, prompts, datasets, experiments — all in parallel).
- **Auth error** — "Authentication failed. Your API key may be invalid or expired. Go to [my.orq.ai](https://my.orq.ai) → Settings → API Keys to generate a new one."
- **MCP not available** — Guide the user to set up the MCP server:

> Run this command in your terminal to add the orq.ai MCP server:
>
> ```bash
> claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header "Authorization: Bearer ${ORQ_API_KEY}"
> ```
>
> Then restart Claude Code and come back: `/orq:quickstart`

### 5. Skills Overview

Present the six skills in a table:

```
Available Skills
================

| Skill                      | When to Use                                                    |
|----------------------------|----------------------------------------------------------------|
| build-agent                | Create and configure a new orq.ai agent with tools and memory  |
| build-evaluator            | Create LLM-as-a-Judge evaluators for measuring failure modes   |
| analyze-trace-failures     | Read production traces and categorize what's failing           |
| run-experiment             | Compare configurations against datasets using evaluators       |
| generate-synthetic-dataset | Generate evaluation datasets for testing                       |
| optimize-prompt            | Analyze and rewrite system prompts for better performance      |
```

Explain the lifecycle:

> **The eval cycle:** analyze-trace-failures → build-evaluator → run-experiment → optimize-prompt → repeat
>
> Skills are auto-discovered — just describe what you want to do in natural language and the right skill will activate.

### 6. What's Next?

**If no models are enabled yet**, proactively suggest setting up provider keys:

> **Enable your first models:** To start using models, add an API key for your preferred provider (OpenAI, Anthropic, Google, etc.):
>
> 1. Go to [my.orq.ai](https://my.orq.ai) → **Model Garden → Providers**
> 2. Select a provider and choose **Setup own API Key**
> 3. Once added, toggle on the models you want to use in the **Model Garden**

Then use `AskUserQuestion` to route the user to their next task:

- **"Build an agent"** → Suggest the `build-agent` skill
- **"Evaluate an existing agent"** → Suggest `analyze-trace-failures` → `build-evaluator` → `run-experiment`
- **"Debug production traces"** → Suggest `/orq:traces` then `analyze-trace-failures`
- **"Optimize a prompt"** → Suggest the `optimize-prompt` skill
- **"Generate test data"** → Suggest the `generate-synthetic-dataset` skill
- **"Just explore"** → Suggest starting with `/orq:workspace` and trying commands
- **"Visit [https://docs.orq.ai](https://docs.orq.ai)"** → Visit Orq.ai documentation.

Finally, point the user to the official documentation:

> **Learn more:** Visit the [orq.ai documentation](https://docs.orq.ai/docs/quick-start) for in-depth guides on deployments, the Model Garden, SDKs, and more.
