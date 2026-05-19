---
name: manage-skills
description: Manage orq.ai Skills — list, get, create, update, disable, or delete Skills (the platform entity, formerly Snippets) and find the prompts/agents that reference them
argument-hint: [list|get|create|update|disable|delete] [name-or-id]
allowed-tools: AskUserQuestion, mcp__orq-workspace__list_skills, mcp__orq-workspace__get_skill, mcp__orq-workspace__create_skill, mcp__orq-workspace__update_skill, mcp__orq-workspace__delete_skill, mcp__orq-workspace__search_entities, mcp__orq-workspace__get_deployment, mcp__orq-workspace__get_agent
---

# Manage Skills

Quick entry point into the `manage-skills` skill. Routes to the right phase based on the first argument, or asks if no argument is given.

## Instructions

### 1. Parse arguments

`$ARGUMENTS` may contain an action and optionally a Skill `display_name` or `skill_id`:

- `list` — Phase 1 (list / audit)
- `get <name-or-id>` — Phase 2 (inspect a Skill)
- `create` — Phase 3 (create a new Skill)
- `update <name-or-id>` — Phase 4 (edit, including `enabled` / `display_name` / `instructions`)
- `disable <name-or-id>` — Phase 4 shortcut: flip `enabled: false` (soft-retire)
- `delete <name-or-id>` — Phase 5 (reference scan + delete)

If `$ARGUMENTS` is empty, ask the user which action they want via `AskUserQuestion` and offer the six choices above.

If `$ARGUMENTS` contains an action that requires a name/id but none was provided (e.g., `get`, `update`, `disable`, `delete`), call `list_skills` first and ask the user to pick.

### 2. Delegate to `manage-skills`

Read `skills/manage-skills/SKILL.md` and execute the matching phase. Pass the parsed name/id along.

### 3. Safety rails

- **Never** auto-execute `delete_skill` from this command — always route through Phase 5's reference-scan + warn-then-confirm flow.
- **Always** offer `enabled: false` (soft disable) as the default first step when the reference scan finds consumers.
- **Always** confirm project scope before `create_skill`.
- **Always** warn before sending a `display_name` rename — it silently breaks every `{{snippet.<old-name>}}` reference.

### 4. Error handling

- **Auth errors** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **`AlreadyExists` on create** — surface the conflicting Skill (paginate `list_skills`, find by `display_name`) and offer either a renamed create or `update_skill` against the existing one.
- **Skill-tool unavailable** — "The orq MCP server doesn't expose `*_skill` tools in this workspace. Falling back to REST `/v2/skills` — confirm before proceeding."
- **MCP unreachable** — "Could not reach the orq.ai MCP server. Make sure it's configured: `claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header 'Authorization: Bearer ${ORQ_API_KEY}'`"
