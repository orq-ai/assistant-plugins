---
name: orq-manage-skills
description: Manage orq.ai Skills — list, get, create, update, retire (tag as retired), or delete Skills (the platform entity, formerly Snippets) and find the prompts/agents that reference them
argument-hint: [list|get|create|update|retire|delete] [name-or-id]
allowed-tools: AskUserQuestion, Read, Grep, Glob, mcp__orq-workspace__list_skills, mcp__orq-workspace__get_skill, mcp__orq-workspace__create_skill, mcp__orq-workspace__update_skill, mcp__orq-workspace__delete_skill, mcp__orq-workspace__search_entities, mcp__orq-workspace__get_deployment, mcp__orq-workspace__get_agent
---

# Manage Skills

Quick entry point into the `orq-manage-skills` skill. Routes to the right phase based on the first argument, or asks if no argument is given.

## Instructions

### 1. Parse arguments

`$ARGUMENTS` may contain an action and optionally a Skill `display_name` or `skill_id`:

- `list` — Phase 1 (list / audit)
- `get <name-or-id>` — Phase 2 (inspect a Skill)
- `create` — Phase 3 (create a new Skill)
- `update <name-or-id>` — Phase 4 (edit `display_name`, `description`, `tags`, `instructions`, `project_id`, `path`)
- `retire <name-or-id>` — Phase 4 shortcut: tag with `retired` (soft-retire — reversible, no `enabled` field exists)
- `delete <name-or-id>` — Phase 5 (reference scan + delete)

If `$ARGUMENTS` is empty, ask the user which action they want via `AskUserQuestion` and offer the six choices above.

If `$ARGUMENTS` contains an action that requires a name/id but none was provided (e.g., `get`, `update`, `retire`, `delete`), call `list_skills` first and ask the user to pick.

### 2. Delegate to `orq-manage-skills`

Read `skills/orq-manage-skills/SKILL.md` and execute the matching phase. Pass the parsed name/id along.

### 3. Safety rails

- **Never** auto-execute `delete_skill` from this command — always route through Phase 5's reference-scan + warn-then-confirm flow.
- **Always** offer tagging with `retired` (soft-retire via `update_skill`) as the default first step when the reference scan finds consumers. There is no `enabled` field on a Skill.
- **Always** confirm project scope before `create_skill`.
- **Always** warn before sending a `display_name` rename — it silently breaks every `{{skill.<old-name>}}` and `{{snippet.<old-name>}}` reference.

### 4. Error handling

- **Auth errors** — "Authentication failed. Check that your `ORQ_API_KEY` is valid."
- **`AlreadyExists` on create** — surface the conflicting Skill (paginate `list_skills`, find by `display_name`) and offer either a renamed create or `update_skill` against the existing one.
- **Skill-tool unavailable** — "The orq MCP server doesn't expose `*_skill` tools in this workspace. Falling back to REST `/v2/skills` — confirm before proceeding."
- **MCP unreachable** — "Could not reach the orq.ai MCP server. Make sure it's configured: `claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header 'Authorization: Bearer ${ORQ_API_KEY}'`"
