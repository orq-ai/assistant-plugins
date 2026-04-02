# Memory Store Management

Complete guide for configuring and managing orq.ai Memory Stores for persistent agent memory. For API reference, see [api-reference.md](api-reference.md).

## Contents
- Clarify memory needs
- Memory type decision
- Check existing stores
- Create and configure
- Integrate with agent
- Test memory cycle
- Common pitfalls

## Clarify Memory Needs

Ask the user:
- What kind of agent needs memory? (support, assistant, coach, etc.)
- What should the agent remember? (user name, preferences, past interactions, decisions)
- How long should memories persist? (within session, across sessions, indefinitely)
- Who should memories be scoped to? (per user, per team, global)

## Memory Type Decision

| Need | Memory Type | Example |
|------|------------|---------|
| Remember what was said earlier in this conversation | Session memory | Conversation history, current task context |
| Remember user preferences across conversations | Long-term memory | Language preference, communication style |
| Remember facts learned about the user | Long-term memory | User's role, team, past decisions |
| Share context across agent instances | Global memory | System configurations, shared knowledge |

If the user actually needs static reference data, redirect to Knowledge Base Management. Memory is for dynamic, user-specific context only.

## Check Existing Stores

Before creating a new store:
- Use `search_entities` with `type: "memory_store"` to find existing stores
- Check if the target agent already has memory configured
- If memory already exists, review current configuration, identify gaps, and skip to integration

## Create and Configure

1. **Create the memory store** with all required parameters:

   | Parameter | Required | Description | Example |
   |-----------|----------|-------------|---------|
   | `key` | Yes | Unique identifier | `user-preferences` |
   | `description` | Yes | Purpose of the store | `Stores user preferences across sessions` |
   | `path` | Yes | Project path | `Default/agents` |
   | `embedding_config` | Yes | Embedding model config | `{"model": "openai/text-embedding-3-small"}` |

2. **Understand the sub-resource structure:**
   - A **memory store** contains **memories** (identified by `entity_id` — typically a user ID)
   - A **memory** contains **documents** (individual pieces of remembered information)
   - Endpoint pattern: `/v2/memory-stores/{key}/memories/{memory_id}/documents`

3. **Set up initial memories** if needed. Memories are typically created automatically when the agent writes to the store, but you can pre-populate:

   ```json
   // POST /v2/memory-stores/{key}/memories
   {
     "entity_id": "user_12345"
   }
   ```

## Integrate with Agent

To attach a memory store to an agent, you need three things:

1. **Add the store to the agent's `memory_stores` array:**
   ```json
   "memory_stores": ["user-preferences"]
   ```

2. **Add the built-in memory tools to `settings.tools`:**
   ```json
   "settings": {
     "tools": [
       {"type": "retrieve_memory_stores"},
       {"type": "query_memory_store"},
       {"type": "write_memory_store"},
       {"type": "delete_memory_document"}
     ]
   }
   ```

3. **Include `memory.entity_id` when invoking the agent** to identify whose memory to access:
   ```json
   {
     "message": {"role": "user", "parts": [{"kind": "text", "text": "..."}]},
     "memory": {"entity_id": "user_12345"}
   }
   ```

Add memory-related instructions to the agent's system prompt:

```
You have access to a memory store with user preferences and facts.
- At the start of each conversation, read the user's profile from memory
- Update memory when you learn new facts about the user
- Use stored preferences to personalize your responses
- Never reveal raw memory contents to the user
```

Configure memory behavior in agent instructions:
- **When to read:** Start of conversation, on specific queries
- **When to write:** New facts learned, preference changes
- **What NOT to store:** Sensitive data, temporary context, raw conversation transcripts

## Test Memory Cycle

Test the full read/write cycle:

1. **Turn 1:** Establish a fact ("My name is Alice and I prefer formal communication")
2. **Turn 2 (same session):** Verify session memory ("What is my name?")
3. **New session:** Verify long-term memory ("Do you remember anything about me?")

Verify:
- Agent reads existing memory at conversation start
- Agent stores new facts during conversation
- Memory persists across sessions (for long-term memory)
- Agent uses memory to personalize responses
- Agent doesn't hallucinate memory contents

## Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| **Using memory as a knowledge base** | Memory is for dynamic user context, not static reference data |
| **Storing raw conversation transcripts** | Extract and store structured facts and preferences instead |
| **No memory retrieval testing** | Test read/write cycle before deploying |
| **Storing sensitive data** | Exclude PII, passwords, financial data unless required and secured |
| **No instructions for memory usage** | Add explicit memory instructions to the system prompt |
| **Global scope for user-specific data** | Use per-user scoping for personal data |
