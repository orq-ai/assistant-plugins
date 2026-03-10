# orq.ai Agent Skills

> Compatible with Claude Code, Cursor, Gemini CLI, OpenAI Codex, and OpenCode

Agent Skills for building, deploying, evaluating, and improving LLM pipelines on the [orq.ai](https://orq.ai) platform.

These skills encode best practices into repeatable workflows that any AI engineer can invoke directly from their agent of choice — covering the full lifecycle from building agents and prompts, through evaluation, to production monitoring and feedback.

## New to Evals? Start Here

Start with the `audit-evaluation-pipeline` skill. Give Claude Code these instructions:

> Run /orq:audit-evaluation-pipeline on my eval pipeline. Investigate each diagnostic area using a separate subagent in parallel, then synthesize the findings into a single report.

The audit catches common evaluation problems and recommends which skills to use next.

## Quick Start

The fastest way to get set up — one command detects your agents, collects your API key, and configures everything:

```bash
npx @orq-ai/setup
```

Or non-interactively:

```bash
npx @orq-ai/setup --api-key $ORQ_API_KEY --agents claude-code,cursor --non-interactive
```

---

## Manual Setup

### Prerequisites

1. **orq.ai account** — sign up at [orq.ai](https://orq.ai)
2. **API key** — get yours from [Workspace Settings → API Keys](https://my.orq.ai/settings/api-keys)

### Step 1: Connect the orq MCP server

The skills require the orq MCP server to access your workspace (traces, datasets, evaluators, experiments). Set it up for your agent:

<details>
<summary><strong>Claude Code</strong></summary>

```bash
# Set your API key
export ORQ_API_KEY="your-api-key"

# Add the orq MCP server
claude mcp add --transport http orq https://my.orq.ai/v2/mcp \
  --header "Authorization: Bearer ${ORQ_API_KEY}"

# Verify it's connected
claude mcp list
```

</details>

<details>
<summary><strong>Cursor</strong></summary>

1. Open **Cursor → Settings → Tools & MCP → New MCP Server**
2. Paste this configuration:

```json
{
  "mcpServers": {
    "orq": {
      "url": "https://my.orq.ai/v2/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_ORQ_API_KEY"
      }
    }
  }
}
```

3. Replace `YOUR_ORQ_API_KEY` with your actual key
4. Save — a green indicator confirms the connection

</details>

<details>
<summary><strong>OpenAI Codex</strong></summary>

1. Open **Codex Settings → MCP Servers → Connect to a custom MCP**
2. Configure:
   - **Name:** `Orq.ai`
   - **Connection Type:** Streamable HTTP
   - **URL:** `https://my.orq.ai/v2/mcp`
3. Add environment variable:
   - **Key:** `AUTHORIZATION`
   - **Value:** `Bearer YOUR_ORQ_API_KEY`
4. Save

</details>

<details>
<summary><strong>Gemini CLI</strong></summary>

The `gemini-extension.json` in this repo configures the MCP server automatically:

```bash
export ORQ_API_KEY="your-api-key"
gemini extensions install https://github.com/orq-ai/orq-skills --consent
```

</details>

For full MCP integration docs, see: [orq.ai Code Assistants](https://docs.orq.ai/docs/integrations/code-assistants/overview)

### Step 2: Install the skills

<details>
<summary><strong>Claude Code</strong></summary>

```bash
# Register the plugin repository
/plugin marketplace add orq-ai/orq-skills

# Install the plugin
/plugin install orq@orq-ai-plugins
```

To upgrade:

```bash
/plugin update orq@orq-ai-plugins
```

</details>

<details>
<summary><strong>Cursor</strong></summary>

Install from the repository URL:

```
https://github.com/orq-ai/orq-skills
```

</details>

<details>
<summary><strong>Gemini CLI</strong></summary>

Already installed in Step 1 — the extension includes both the MCP server and skills.

</details>

<details>
<summary><strong>OpenAI Codex</strong></summary>

Copy or symlink skills into your Codex agents directory:

```bash
git clone https://github.com/orq-ai/orq-skills.git
ln -s orq-skills/skills .agents/skills
```

Codex will also read the bundled `agents/AGENTS.md` as a fallback.

</details>

<details>
<summary><strong>OpenCode</strong></summary>

OpenCode supports the [Agent Skills](https://agentskills.io) standard:

```bash
git clone https://github.com/orq-ai/orq-skills.git
# Point OpenCode to the skills/ directory
```

</details>

<details>
<summary><strong>npx Skills CLI</strong></summary>

```bash
# Install all skills
npx skills add https://github.com/orq-ai/orq-skills

# Install a single skill
npx skills add https://github.com/orq-ai/orq-skills --skill audit-evaluation-pipeline

# Check for updates
npx skills check
npx skills update
```

</details>

### Step 3: Verify

Ask your agent: *"Can you list the available models from Orq?"*

If configured correctly, the agent will use the `list_models` MCP tool and return your workspace's available models.

### Optional: HTTP API fallback

Some operations (knowledge base management, agent invocation) are not yet available via MCP. For these, set the environment variable:

```bash
export ORQ_API_KEY="your-api-key"
```

Skills will use `curl` with `Authorization: Bearer $ORQ_API_KEY` as a fallback.

### Optional: Linear MCP server

The `action-plan` skill can file tickets directly to Linear. See [Linear MCP setup](https://github.com/jerhadf/linear-mcp-server) if you want this integration.

## Available Skills

<!-- BEGIN_SKILLS_TABLE -->
| Skill | What It Does | Documentation |
|-------|-------------|---------------|
| **action-plan** | Generate a prioritized action plan from LLM experiment results following the Analyze-Measure-Improve lifecycle | [SKILL.md](skills/action-plan/SKILL.md) |
| **annotation-workflow** | Set up human annotation queues for evaluator validation, labeling, and quality assurance | [SKILL.md](skills/annotation-workflow/SKILL.md) |
| **audit-evaluation-pipeline** | Audit an existing LLM eval pipeline and produce a prioritized diagnostic report with concrete next steps | [SKILL.md](skills/audit-evaluation-pipeline/SKILL.md) |
| **build-agent** | Design, create, and configure an orq.ai Agent with tools, instructions, knowledge bases, and memory | [SKILL.md](skills/build-agent/SKILL.md) |
| **build-evaluator** | Create validated LLM-as-a-Judge evaluators following evaluation best practices | [SKILL.md](skills/build-evaluator/SKILL.md) |
| **curate-dataset** | Clean, deduplicate, balance, and augment existing evaluation datasets for higher quality experiments | [SKILL.md](skills/curate-dataset/SKILL.md) |
| **evaluate-agent** | Evaluate agentic and tool-calling LLM pipelines with stage-specific graders and transition failure matrices | [SKILL.md](skills/evaluate-agent/SKILL.md) |
| **evaluate-conversation** | Evaluate multi-turn conversation quality with session-level, turn-level, and memory/coherence checks | [SKILL.md](skills/evaluate-conversation/SKILL.md) |
| **evaluate-rag** | Evaluate RAG pipelines by separately measuring retrieval quality and generation faithfulness | [SKILL.md](skills/evaluate-rag/SKILL.md) |
| **feedback-loop** | Set up and analyze user feedback collection to drive data-informed improvement cycles | [SKILL.md](skills/feedback-loop/SKILL.md) |
| **generate-synthetic-dataset** | Generate structured, diverse evaluation datasets using the dimensions-tuples-natural language method | [SKILL.md](skills/generate-synthetic-dataset/SKILL.md) |
| **knowledge-base** | Manage orq.ai Knowledge Bases — create, configure, upload files, chunk content, search, and maintain RAG data sources via API | [SKILL.md](skills/knowledge-base/SKILL.md) |
| **list-models** | List available models and systematically compare them on cost, latency, and quality to find the optimal choice for your use case | [SKILL.md](skills/list-models/SKILL.md) |
| **manage-deployment** | Configure, version, and manage orq.ai deployments — model selection, prompt linking, fallbacks, caching, and guardrails | [SKILL.md](skills/manage-deployment/SKILL.md) |
| **manage-memory** | Create and configure orq.ai Memory Stores for persistent context in conversational agents | [SKILL.md](skills/manage-memory/SKILL.md) |
| **monitor-production** | Analyze production trace data for anomalies, cost trends, latency regressions, and emerging failure modes | [SKILL.md](skills/monitor-production/SKILL.md) |
| **optimize-prompt** | Systematically iterate on a prompt deployment using trace data, A/B testing, and structured refinement techniques | [SKILL.md](skills/optimize-prompt/SKILL.md) |
| **prompt-learning** | Automatically improve prompts by collecting feedback, generating "If [TRIGGER] then [ACTION]" rules via a meta-prompt, and validating with A/B experiments | [SKILL.md](skills/prompt-learning/SKILL.md) |
| **regression-test** | Run a quick regression check against a golden dataset to verify recent changes haven't degraded quality | [SKILL.md](skills/regression-test/SKILL.md) |
| **run-experiment** | End-to-end LLM evaluation workflow — error analysis, dataset creation, experiment execution, result analysis, and ticket filing | [SKILL.md](skills/run-experiment/SKILL.md) |
| **scaffold-integration** | Generate SDK integration code (Python or Node) for orq.ai agents, deployments, and knowledge bases in the user's codebase | [SKILL.md](skills/scaffold-integration/SKILL.md) |
| **setup-ci-eval** | Set up continuous evaluation in CI/CD pipelines that runs regression tests on every prompt or agent change | [SKILL.md](skills/setup-ci-eval/SKILL.md) |
| **trace-analysis** | Systematic trace reading and failure taxonomy building using open coding and axial coding | [SKILL.md](skills/trace-analysis/SKILL.md) |
<!-- END_SKILLS_TABLE -->

Invoke a skill with `/orq:<skill-name>`, e.g., `/orq:trace-analysis`.

## The Full Lifecycle

Skills cover the complete **Build → Evaluate → Deploy → Monitor → Improve** lifecycle:

```
  BUILD                        EVALUATE                      DEPLOY & MONITOR
  ─────                        ────────                      ────────────────

  ┌────────────────┐     ┌───────────────────────────┐     ┌──────────────────────┐
  │  build-agent   │     │  audit-evaluation-pipeline │     │  manage-deployment   │
  │                │     │                           │     │                      │
  │  optimize-     │     └─────────────┬─────────────┘     │  scaffold-           │
  │  prompt        │                   │                   │  integration         │
  │                │     ┌─────────────▼─────────────┐     └──────────┬───────────┘
  │  manage-       │     │     trace-analysis        │                │
  │  memory        │     └─────────────┬─────────────┘     ┌──────────▼───────────┐
  │                │                   │                   │  monitor-production  │
  │  knowledge-    │     ┌─────────────▼─────────────┐     │                      │
  │  base          │     │  build-evaluator          │     │  regression-test     │
  └────────────────┘     │  evaluate-agent           │     │                      │
                         │  evaluate-conversation    │     │  setup-ci-eval       │
                         │  evaluate-rag             │     │                      │
                         │  curate-dataset           │     │  feedback-loop       │
                         │  generate-synthetic-      │     │                      │
                         │  dataset                  │     │  annotation-         │
                         └─────────────┬─────────────┘     │  workflow            │
                                       │                   └──────────────────────┘
                         ┌─────────────▼─────────────┐
                         │     run-experiment        │
                         └─────────────┬─────────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │     action-plan           │──── repeat ────┐
                         └───────────────────────────┘                │
                                                                      ▲
                                                                      │
                         ┌───────────────────────────┐                │
                         │     list-models           │────────────────┘
                         └───────────────────────────┘
```

## How Skills Connect

Skills hand off to each other automatically — each SKILL.md lists its companion skills.

**The eval cycle:** **audit** → **trace-analysis** → **build-evaluator** → **run-experiment** → **action-plan** → repeat

**Build flow:** **build-agent** → **scaffold-integration** → **manage-deployment** → **monitor-production**

**Data quality:** **generate-synthetic-dataset** → **curate-dataset** → **run-experiment**

**Production signals:** **monitor-production** → **feedback-loop** → **trace-analysis** → **optimize-prompt**

**Eval infrastructure:** **setup-ci-eval** + **regression-test** for automated quality gates, **annotation-workflow** for human labeling

Specialized skills (`evaluate-rag`, `evaluate-agent`, `evaluate-conversation`, `list-models`, `knowledge-base`, `manage-memory`) plug into these flows wherever relevant.

## Quick Start Examples

### 1. Audit an existing eval pipeline

```
You: "Is my eval setup any good? What should I fix?"
→ Claude Code uses: audit-evaluation-pipeline
```

### 2. Evaluate an existing agent

```
You: "My support agent is giving bad answers, help me figure out why"
→ Claude Code uses: trace-analysis → build-evaluator → run-experiment → action-plan
```

### 3. Build an eval dataset from scratch

```
You: "I need to create test cases for my product recommendation agent"
→ Claude Code uses: generate-synthetic-dataset
```

### 4. Evaluate a RAG pipeline

```
You: "My knowledge base answers are sometimes wrong, evaluate the pipeline"
→ Claude Code uses: evaluate-rag
```

### 5. Compare models for cost optimization

```
You: "I'm using gpt-4.1 but it's expensive, can I use something cheaper?"
→ Claude Code uses: list-models
```

### 6. Evaluate a multi-turn chatbot

```
You: "My chatbot forgets context after a few turns"
→ Claude Code uses: evaluate-conversation
```

### 7. Build a new agent

```
You: "I need a customer support agent with access to our order database"
→ Claude Code uses: build-agent → scaffold-integration
```

### 8. Improve a prompt

```
You: "My prompt keeps breaking character, help me fix it"
→ Claude Code uses: optimize-prompt
```

### 9. Set up CI evaluation

```
You: "I want to automatically test my prompts on every PR"
→ Claude Code uses: setup-ci-eval
```

### 10. Monitor production health

```
You: "Are there any issues with my deployed agent?"
→ Claude Code uses: monitor-production → feedback-loop
```

## Key Best Practices Encoded

These skills enforce evaluation best practices throughout the workflow:

- **Binary Pass/Fail** over Likert scales — less noise, more actionable
- **One evaluator per failure mode** — never bundle criteria
- **Trace analysis before automation** — read 50-100 traces before building evaluators
- **Fix prompts before switching models** — most failures are specification issues
- **Validate evaluators with TPR/TNR** — unvalidated evaluators are unreliable
- **Structured synthetic data** — dimensions → tuples → natural language for 5-10x diversity
- **Separate retrieval from generation** in RAG evals — know what to fix
- **Grade outcomes, not paths** in agent evals — agents find valid alternative approaches
- **Session-level before turn-level** in conversation evals — don't over-invest in per-turn analysis
- **Capability evals start hard** — 100% pass rate means the eval is too easy
- **Tool descriptions matter** — vague tool descriptions are the #1 cause of agent failures
- **Always A/B test prompt changes** — never "vibes check", always experiment
- **Feedback is signal, not ground truth** — correlate with evaluator scores before acting
- **Regression test before deploying** — run golden dataset checks on every change
- **Version everything** — prompts, deployments, baselines, never modify in-place

## orq.ai Platform Integration

All skills integrate with the orq.ai platform through:

| Interface | Best For | When to Use |
|-----------|----------|-------------|
| **orq MCP tools** | Integrated workflows within Claude Code (datasets, experiments, evaluators, traces, agents, models) | Primary interface — use for all supported operations |
| **HTTP API** | Operations not yet available via MCP (agent invoke, evaluator invoke, knowledge, memory, tools) | Fallback — use `curl` with `Authorization: Bearer $ORQ_API_KEY` |

## File Structure

```
skills/
├── action-plan/
│   └── SKILL.md
├── annotation-workflow/
│   └── SKILL.md
├── audit-evaluation-pipeline/
│   └── SKILL.md
├── build-agent/
│   ├── SKILL.md
│   └── resources/
│       ├── tool-description-guide.md
│       └── system-instruction-template.md
├── build-evaluator/
│   ├── SKILL.md
│   └── resources/
│       ├── judge-prompt-template.md
│       ├── validation-checklist.md
│       └── data-split-guide.md
├── curate-dataset/
│   └── SKILL.md
├── evaluate-agent/
│   └── SKILL.md
├── evaluate-conversation/
│   └── SKILL.md
├── evaluate-rag/
│   └── SKILL.md
├── feedback-loop/
│   └── SKILL.md
├── generate-synthetic-dataset/
│   └── SKILL.md
├── knowledge-base/
│   └── SKILL.md
├── list-models/
│   └── SKILL.md
├── manage-deployment/
│   └── SKILL.md
├── manage-memory/
│   └── SKILL.md
├── monitor-production/
│   └── SKILL.md
├── optimize-prompt/
│   └── SKILL.md
├── regression-test/
│   └── SKILL.md
├── run-experiment/
│   └── SKILL.md
├── scaffold-integration/
│   ├── SKILL.md
│   └── resources/
│       ├── python-patterns.md
│       └── node-patterns.md
├── setup-ci-eval/
│   ├── SKILL.md
│   └── resources/
│       ├── github-actions-template.yml
│       └── baseline-config.md
└── trace-analysis/
    └── SKILL.md
```

## License

MIT
