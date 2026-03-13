---
name: prompt-optimizer
description: Analyze and optimize system prompts — get AI-powered analysis and rewriting using structured prompting guidelines
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Prompt Optimizer

Analyze and optimize system prompts using a structured prompting guidelines framework. The agent itself performs the analysis and rewriting — no external deployments needed.

**Companion skills:**
- `optimize-prompt` — trace-driven prompt iteration (use when you have production traces showing specific failures)
- `run-experiment` — validate optimized prompts with A/B experiments
- `manage-deployment` — configure deployments with the optimized prompt

## When to use

- User asks to optimize, improve, or rewrite a system prompt
- User wants AI-powered analysis of prompt quality
- User has a prompt that needs general improvement without trace data

## orq.ai Documentation

Consult these docs when working with the orq.ai platform:
- **Prompts overview:** https://docs.orq.ai/docs/prompts/overview
- **Prompt management:** https://docs.orq.ai/docs/prompts/management
- **Prompt versioning:** https://docs.orq.ai/docs/prompts/versioning
- **Deployments overview:** https://docs.orq.ai/docs/deployments/overview

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `search_entities` | Find prompts (`type: "prompts"`) and deployments |

**HTTP API fallback** (for operations not yet in MCP):

```bash
# Get prompt details with versions
curl -s https://my.orq.ai/v2/prompts/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a new prompt version
curl -s -X POST https://my.orq.ai/v2/prompts/<ID>/versions \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages": [...], "model": "...", "parameters": {...}}' | jq
```

## Prompting Guidelines Framework

Use this framework to analyze and optimize prompts. Each guideline is a dimension to evaluate — identify what's missing or weak, then improve it.

1. **Role assignment & expertise** — Clear, emphasized role with specific domain expertise and qualifications
2. **Task definition** — Clear explanation of what the system will do
3. **Stress induction** — Emphasis on the importance and criticality of the task
4. **Guidelines** — Breakdown of the task into clear guidelines covering: task explanation, behavioral constraints, communication style, knowledge boundaries
5. **Output format** — Specified and stressed output format. If tools are present, they provide their own format so no additional output format is needed
6. **Tool calling** — If tools/functions are mentioned, they are part of the task. Never suggest removing tools. Keep tool definitions in their original state but may suggest adjustments to how they're referenced
7. **Reasoning** — For complex tasks requiring analysis, reasoning must be instructed and must appear before the final answer. If reasoning is instructed but the output format has no space for it, suggest adding one (e.g., a `reasoning` key in JSON)
8. **Examples** — Few-shot examples using `<example>` XML tags to demonstrate desired behavior, with proper variable formatting inside
9. **Remove unnecessary content** — No unnecessary markdown, emojis, XML tags, or contradictions
10. **Proper variable usage** — Variables with `{{double curly brackets}}` should only appear once near the end; earlier references should use XML tags
11. **Recap** — A one-sentence recap of the task and format at the end of the prompt

When presenting analysis to the user, reference which guideline each suggestion targets to help them understand the reasoning.

## Core Principles

### 1. Two-Step Process
This skill has two steps: **Analyze** (identify what's weak) and **Rewrite** (apply improvements). Step 1 can be skipped if the user already provides specific instructions.

### 2. Human in the Loop
Never apply an optimized prompt without user review. Always show a diff between original and optimized versions and get explicit approval.

### 3. Preserve Intent
Improve how the prompt is expressed, not what it does. Always verify the optimized prompt preserves the original intent, persona, and constraints.

## Destructive Actions

The following actions require explicit user confirmation via `AskUserQuestion` before execution:
- Creating a new prompt version with the optimized prompt
- Modifying a deployment's prompt configuration

## Steps

Follow these steps **in order**. Do NOT skip steps.

**Determine the workflow based on user input:**
- **No arguments** (`/prompt-optimizer`): Start with Phase 2 (Analyze), then Phase 3 (Rewrite)
- **With instructions** (`/prompt-optimizer make this way more assertive`): Skip Phase 2, go straight to Phase 3 using the user's instructions

### Phase 1: Fetch the Current Prompt

1. **Find and retrieve the target prompt:**
   - Use `search_entities` with `type: "prompts"` to find the target prompt
   - Use HTTP API to get full prompt details including current version text
   - Document: prompt name, current version, system message content, model, parameters

2. **Extract the system prompt text** for analysis.
   - **Important:** Template variables in the prompt must be preserved as `{{variable_name}}` literally — do NOT substitute the variable content.
   - If the prompt contains tool/function definitions, include them as-is for analysis.

### Phase 2: Analyze (Step 1)

> **Skip this phase** if the user provided specific optimization instructions.

3. **Analyze the prompt against the Prompting Guidelines Framework:**
   - Evaluate each of the 11 guidelines
   - Identify strengths and weaknesses
   - Generate up to 5 concrete, actionable suggestions for improvement

4. **Present analysis to the user:**
   ```
   ## Prompt Analysis

   **Strengths:** [what the prompt does well]

   ### Suggestions
   1. [Guideline X] — [specific suggestion]
   2. [Guideline Y] — [specific suggestion]
   3. [Guideline Z] — [specific suggestion]
   ```

5. **Ask the user which suggestions to apply:**
   - User may accept all, select specific ones, or modify suggestions
   - The accepted suggestions become the rewriting instructions for Phase 3

### Phase 3: Rewrite (Step 2)

6. **Rewrite the prompt** based on instructions:
   - If coming from Phase 2: use the accepted suggestions as directives
   - If user provided instructions directly: use those as directives
   - Apply changes while preserving the original intent, persona, and constraints
   - Keep template variables as `{{variable_name}}`
   - Keep tool/function definitions intact

7. **Present a diff to the user:**
   - Show the original and optimized prompts side by side or as a diff
   - Highlight key changes and which guidelines/instructions they address
   - Ask for user approval before proceeding

### Phase 4: Apply

8. **Create a new prompt version** (with user confirmation):
   - Use HTTP API to create a new version on the existing prompt
   - Document the version number and what was optimized
   - Keep the original version intact for rollback

9. **Recommend next steps:**
   - If trace data is available, suggest using `optimize-prompt` for deeper trace-driven iteration
   - Suggest monitoring production traces to verify improvement

## Anti-Patterns

| Anti-Pattern | Why It's Wrong | What to Do Instead |
|---|---|---|
| Applying optimized prompt without review | Rewriting can change intent or remove important constraints | Always show a diff and get user approval |
| Rewriting without understanding the issues | Blind rewriting can make prompts worse | Run analysis first (unless user has specific instructions) |
| Using optimizer instead of trace analysis | Automated optimization misses application-specific failure patterns | Use `optimize-prompt` when you have trace data showing specific failures |
| Running the optimizer repeatedly on the same prompt | Each pass can drift further from the original intent | Optimize once, validate, then iterate if needed |
| Not preserving the original version | No rollback path if the optimization regresses | Always create a new version, keep the original intact |
| Changing what the prompt does instead of how it's expressed | Optimization should improve clarity, not change behavior | Preserve intent — improve expression only |

## Open in orq.ai

After completing this skill, direct the user to the relevant platform page:

- **View/edit the prompt:** `https://my.orq.ai/prompts` — review original and optimized versions
- **View deployments:** `https://my.orq.ai/deployments` — update deployment to use the optimized prompt
