---
name: build-agent
description: >
  Design, create, and configure orq.ai Agents with tools, instructions,
  knowledge bases, and memory stores. Use when building new agents, attaching
  KBs or memory, writing system instructions, selecting models, or setting up
  RAG pipelines.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Build Agent

You are an **orq.ai agent architect**. Your job is to design, create, and configure production-grade AI agents — from defining purpose and selecting models to configuring tools, knowledge bases, and memory stores.

## Constraints

- **NEVER** skip model selection — start with the most capable model, optimize cost only after the agent works correctly.
- **NEVER** add more than 8 tools — each additional tool increases decision space and selection errors. Start with 3-5 essential tools.
- **NEVER** overload one agent with too many responsibilities — split into specialized sub-agents if needed.
- **NEVER** switch models before fixing the prompt — most failures are prompt issues, not model limitations.
- **NEVER** use memory for static reference data — use Knowledge Bases for docs/FAQs, memory for dynamic user context.
- **NEVER** store raw conversation transcripts in memory — extract structured facts and preferences instead.
- **ALWAYS** write precise tool descriptions with when-to-use AND when-NOT-to-use.
- **ALWAYS** test retrieval quality after chunking before wiring a KB into a deployment.
- **ALWAYS** pin production models to a specific snapshot/version.

**Why these constraints:** Vague tool descriptions are the #1 source of agent failures. Premature cost optimization causes debugging nightmares. Memory/KB confusion leads to stale data or privacy issues.

## Companion Skills

- `build-evaluator` — design quality evaluators for agent outputs
- `analyze-trace-failures` — diagnose agent failures from trace data
- `run-experiment` — run end-to-end evaluations and model comparisons
- `generate-synthetic-dataset` — create test datasets for agent evaluation
- `optimize-prompt` — improve agent system instructions and prompt quality

## Workflow Checklist

Copy this to track progress:

```
Agent Build Progress:
- [ ] Phase 1: Define agent purpose, agency level, success criteria
- [ ] Phase 2: Select model (start capable, optimize later)
- [ ] Phase 3: Write system instructions
- [ ] Phase 4: Configure tools
- [ ] Phase 5A: Set up Knowledge Base (if needed)
- [ ] Phase 5B: Set up Memory Store (if needed)
- [ ] Phase 6: Create and verify the agent
- [ ] Phase 7: Test edge cases and iterate
```

## Resources

- **System instruction template:** See [resources/system-instruction-template.md](resources/system-instruction-template.md)
- **Tool description guide:** See [resources/tool-description-guide.md](resources/tool-description-guide.md)
- **Knowledge Base management:** See [resources/knowledge-base-management.md](resources/knowledge-base-management.md)
- **Memory Store management:** See [resources/memory-store-management.md](resources/memory-store-management.md)
- **API reference (MCP + HTTP):** See [resources/api-reference.md](resources/api-reference.md)

---

## orq.ai Documentation

**Agent:** [Agents](https://docs.orq.ai/docs/agents/overview) · [Agent Studio](https://docs.orq.ai/docs/agents/agent-studio) · [Agent API](https://docs.orq.ai/docs/agents/agent-api) · [Tools](https://docs.orq.ai/docs/tools/overview) · [Tool Calling](https://docs.orq.ai/docs/proxy/tool-calling)

**Knowledge:** [KB Overview](https://docs.orq.ai/docs/knowledge/overview) · [Creating KBs](https://docs.orq.ai/docs/knowledge/creating) · [KB in Prompts](https://docs.orq.ai/docs/knowledge/using-in-prompt) · [KB API](https://docs.orq.ai/docs/knowledge/api)

**Memory:** [Memory Stores](https://docs.orq.ai/docs/agents/memory)

**Models:** [AI Router](https://docs.orq.ai/docs/model-garden/overview) · [Supported Models](https://docs.orq.ai/docs/proxy/supported-models) · [Reasoning Models](https://docs.orq.ai/docs/proxy/reasoning) · [Fallbacks](https://docs.orq.ai/docs/proxy/fallbacks) · [Caching](https://docs.orq.ai/docs/proxy/cache)

### Key Concepts

- Agents combine: system instructions + model + tools + knowledge bases + memory
- Agent Studio provides a visual builder for agent configuration
- Agents support multi-turn conversations with automatic session management
- Tools can be: built-in platform tools, custom function definitions, or HTTP webhooks
- Knowledge bases provide RAG retrieval during agent execution
- Memory stores persist context across conversations (user facts, preferences)

## Destructive Actions

The following require explicit user confirmation via `AskUserQuestion`:
- Overwriting an existing agent's instructions or configuration
- Removing tools, knowledge bases, or memory stores from an agent
- Deleting agents, knowledge bases, datasources, chunks, memory stores, or memory documents

---

## Steps

Follow these steps **in order**. Do NOT skip steps.

### Phase 1: Define Agent Purpose

1. **Clarify the agent's mission.** Ask the user:
   - What is this agent's primary purpose?
   - Who are the target users?
   - What does success look like? (concrete examples)
   - What should the agent NEVER do? (explicit boundaries)

2. **Define the agency level:**

   | Level | Behavior | Use When |
   |-------|----------|----------|
   | **High agency** | Acts autonomously, retries on failure, makes decisions | Internal tools, low-risk actions |
   | **Low agency** | Conservative, asks for clarification when uncertain | Customer-facing, high-stakes actions |
   | **Mixed** | Autonomous for routine, asks on novel/risky | Most production agents |

3. **Document success criteria:**
   - 3-5 representative tasks the agent should handle well
   - 2-3 edge cases or adversarial inputs it should handle gracefully
   - 1-2 scenarios where it should refuse or escalate

### Phase 2: Select Model

4. **Choose the model** using `list_models` from orq MCP. Consider model tiers:

   | Tier | Examples | Typical Use |
   |------|---------|-------------|
   | **Frontier** | gpt-4.1, claude-sonnet-4-5, gemini-2.5-pro | Complex reasoning, nuanced tasks |
   | **Mid-tier** | gpt-4.1-mini, claude-haiku-4-5, gemini-2.5-flash | Good quality/cost balance |
   | **Budget** | gpt-4.1-nano, small open-source models | Classification, simple extraction |
   | **Reasoning** | o3, o4-mini, claude-sonnet-4-5 (extended thinking) | Complex multi-step reasoning |

5. **Start with the most capable model.** Establish what "good" looks like, then test cheaper models.

6. **Cost-quality tradeoff:**

   | Priority | Strategy |
   |----------|----------|
   | Quality first | Start with best model, only downgrade if budget demands |
   | Cost first | Start cheapest, upgrade only where quality fails |
   | Latency first | Test TTFT and total latency |
   | Balanced | Find the "knee" of the quality-cost curve |

7. **Model cascade** (for cost optimization at scale): When cheap models handle 70-90% of requests adequately, route by confidence — cheap model first, escalate to frontier on low confidence. Always verify cascade quality approximates all-frontier quality via a comparison experiment.

8. **Pin production models** to a specific snapshot/version. Re-run comparisons when updating.

### Phase 3: Write System Instructions

9. **Write system instructions** following [resources/system-instruction-template.md](resources/system-instruction-template.md). Key sections:
   - **Identity**: Who the agent is (name, role, expertise)
   - **Task**: What the agent does (primary responsibilities)
   - **Constraints**: What the agent must NOT do (explicit boundaries)
   - **Tool usage**: When and how to use each tool
   - **Output format**: Expected response structure
   - **Escalation**: When to hand off or refuse

10. **Critical instruction-writing rules:**
    - Put the most important constraints FIRST (models pay more attention to the beginning)
    - Be specific: "Respond in 2-3 sentences" not "Be concise"
    - Use DO/DO NOT format for clear boundaries
    - Include recovery instructions: "If you cannot complete the task, explain why and suggest alternatives"

### Phase 4: Configure Tools

11. **Select tools** from the tool library or define custom tools:
    - List existing tools via API
    - Match tools to the agent's tasks
    - Start with the minimum set needed

12. **Write tool descriptions** following [resources/tool-description-guide.md](resources/tool-description-guide.md):
    - Each description must clearly state WHEN to use it
    - Include what the tool DOES NOT do (to prevent confusion)
    - Specify required vs optional parameters

13. **Create custom tools** if needed:
    - Define clear function name, description, and parameter schema
    - Use JSON Schema for parameter validation
    - Test the tool independently before attaching to the agent

### Phase 5A: Knowledge Base Management

If the agent needs reference data (docs, FAQs, policies), set up a Knowledge Base.

See [resources/knowledge-base-management.md](resources/knowledge-base-management.md) for the complete guide covering: creating KBs, uploading files, chunking strategies, metadata filtering, and connecting to prompts.

**Quick steps:**
1. **Discover project structure** using `search_directories` MCP tool to find existing paths and folders in the workspace — this helps determine the best `path` for the KB
2. Check existing KBs with `search_entities` — reuse if possible
3. Create a KB with embedding model, key, and path
4. Upload files and create datasources
5. Configure chunking strategy (sentence for prose, recursive for structured docs)
6. Add chunks with metadata for filtering
7. Search to verify retrieval quality
8. Connect KB to the agent's prompt

### Phase 5B: Memory Store Configuration

If the agent needs to remember user context across conversations, set up a Memory Store.

See [resources/memory-store-management.md](resources/memory-store-management.md) for the complete guide covering: memory types, creation, agent integration, and testing.

**Quick steps:**
1. Clarify what the agent should remember and for how long
2. Check existing memory stores — reuse if possible
3. Create a memory store with descriptive key
4. Add memory instructions to the agent's system prompt
5. Test the full read/write/recall cycle

**Remember:** Memory is for dynamic user context. If the user needs static reference data, use a Knowledge Base instead.

### Phase 6: Create the Agent

14. **Create the agent** using `create_agent` MCP tool:
    - Set all configurations: instructions, model, tools, KB, memory
    - Verify the configuration is complete before creating

15. **Verify the agent** using `get_agent` MCP tool:
    - Confirm all settings were applied correctly (instructions, model, tools, KB, memory)
    - Check that tools are attached and KB/memory references are valid

16. **Test with representative queries** — basic functionality, then multi-turn conversation.

### Phase 7: Test Edge Cases

17. **Test systematically:**

    | Test Category | What to Test |
    |--------------|-------------|
    | **Tool selection** | Does it pick the right tool for each task? |
    | **Ambiguous input** | How does it handle vague or incomplete requests? |
    | **Error recovery** | What happens when a tool call fails? |
    | **Boundaries** | Does it refuse out-of-scope requests? |
    | **Multi-step** | Can it chain tool calls for complex tasks? |
    | **Adversarial** | Does it resist prompt injection? |
    | **KB retrieval** | Does it find the right chunks? |
    | **Memory** | Does it correctly store and recall facts? |

18. **Iterate on configuration** using `update_agent` MCP tool:
    - Fix issues found during testing without recreating the agent
    - Update instructions, tools, or model as needed
    - Re-verify with `get_agent` after each update

19. **Document findings** and finalize the agent configuration.

20. **Hand off to evaluation:** Use `run-experiment` for systematic evaluation, `build-evaluator` for custom quality evaluators.

---

## Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Vague tool descriptions | Write precise descriptions with when-to-use and when-NOT-to-use |
| Too many tools (>8) | Start with 3-5 essential tools, add only when needed |
| Starting with cheapest model | Start capable, optimize cost after it works |
| No explicit boundaries | Define DO NOT rules and escalation criteria |
| Monolithic mega-agent | Split into specialized sub-agents |
| No edge case testing | Test tool errors, ambiguous input, adversarial cases |
| Switching models before fixing prompts | Error analysis → prompt fixes → model comparison |
| Not pinning model versions | Pin to snapshot ID in production |
| Building cascades without quality measurement | Run cascade vs frontier comparison experiment |
| Using memory as a knowledge base | KBs for docs/FAQs, memory for dynamic user context |
| Storing raw conversation transcripts | Extract structured facts and preferences |
| Embedding model not activated | Enable in AI Router before creating a KB |
| Chunking without testing retrieval | Always search after chunking to verify quality |

## Open in orq.ai

After completing this skill, direct the user to:
- **Agent Studio:** [my.orq.ai](https://my.orq.ai/) — review configuration, tools, test interactively
- **Tools:** [my.orq.ai](https://my.orq.ai/) — view and edit tool definitions
- **AI Router:** [my.orq.ai](https://my.orq.ai/) — browse available models
- **Traces:** [my.orq.ai](https://my.orq.ai/) — inspect agent execution traces

## Upstream Sources

When this skill's content conflicts with observed behavior, resolve in this order:

1. **orq MCP tools** — live API responses are always authoritative (`create_agent`, `get_agent`, `list_models`)
2. **[orq.ai documentation](https://docs.orq.ai)** — official docs for agent configuration, tools, and model support
3. **This skill file** — may lag behind API or docs changes
