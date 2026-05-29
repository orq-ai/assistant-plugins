---
name: manage-skills
description: >
  Manage orq.ai Skills (the platform entity, formerly called Snippets) end-to-end —
  list, get, create, update, and delete Skills, plus authoring
  guidance (display name, description, tags, project scoping, path placement),
  and how Skills get consumed (the `{{skill.<display_name>}}` template placeholder
  inside prompts and agent instructions). Use when the user wants to create,
  audit, edit, retire, or hook up orq.ai Skills.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, mcp__orq-workspace__list_skills, mcp__orq-workspace__get_skill, mcp__orq-workspace__create_skill, mcp__orq-workspace__update_skill, mcp__orq-workspace__delete_skill, mcp__orq-workspace__search_entities, mcp__orq-workspace__get_deployment, mcp__orq-workspace__get_agent
---

# Manage Skills

You are an **orq.ai Skills lifecycle specialist**. Your job is the full CRUD workflow for the **Skills entity on the orq.ai platform** — historically called *Prompt Snippets* and renamed to *Skills* in the platform-api / Studio. Skills are modular, reusable instruction blocks intended to be inlined into prompts and agent instructions via the `{{skill.<display_name>}}` template placeholder (with `{{snippet.<display_name>}}` as a backward-compatible alias).

## Production-readiness notice (verify before relying on this skill)

The `/v2/skills` REST surface and `*_skill` MCP tools are still rolling out — **run the preflight check** in [Prerequisites](#prerequisites) before any phase. If the MCP tools or REST endpoints are unavailable, fall back to the legacy `/v2/prompts/snippets` controller which is still mounted and provides full CRUD. Both `{{skill.<display_name>}}` and `{{snippet.<display_name>}}` placeholders resolve in the renderer once the skills service is deployed; verify by running a test render after creating a Skill before promoting it to production.

One known migration behavior to surface when relevant:

1. **Snippet→Skill migration is one-way and asynchronous.** Existing Prompt Snippets are migrated to Skills via a backend cronjob; Skills created via the new API are *not* back-propagated to the snippet representation.

## Disambiguation: which "Skill" are we talking about?

This skill manages the **platform Skill entity on orq.ai** (`/v2/skills`, surfaced as Skills in the Studio, formerly Prompt Snippets). It is *not*:

- **Orq Skills (this repo):** code-assistant skills like `manage-skills` itself, distributed via the `assistant-plugins` marketplace and documented at <https://docs.orq.ai/docs/integrations/code-assistants/skills>. Those live in `skills/<name>/SKILL.md` files in this repo.
- **Anthropic / Agent Skills standard:** the cross-vendor SKILL.md format (same shape as the repo skills above; unrelated to the platform entity).
- **The A2A `AgentCard.skills` array on agents:** that field is AI-generated capability metadata, not a list of platform-Skill references. Deleting a platform Skill does **not** orphan anything in `AgentCard.skills`.

When the user says "create a Skill" without context, ask which one they mean. The rest of this document is exclusively about the platform entity.

> **Template-placeholder note:** Both `{{skill.<display_name>}}` (canonical) and `{{snippet.<display_name>}}` (backward-compatible alias, falls back to the Skill whose `display_name` matches) resolve at render time. The lookup key is `display_name` in both cases. Prefer `{{skill.<display_name>}}` in new authoring. If a referenced Skill renders to empty, verify the `display_name` matches exactly (case-sensitive).

## When to use

- "List the Skills in my workspace" / "audit my Skills"
- "Create a Skill called X" / "make a snippet for Y"
- "Update / rename / re-tag this Skill"
- "Retire / tag as retired" or "delete this Skill"
- "How do I reference a Skill from a prompt or agent instruction?"
- "I deleted a Skill — what breaks?"

## When NOT to use

- **Build the agent itself?** → `build-agent`
- **Invoke a deployment or agent?** → `invoke-deployment`
- **Evaluate an agent that uses the Skill?** → `run-experiment`
- **Improve the prose inside `instructions`?** → `optimize-prompt` is tuned for system prompts; reuse its clarity heuristics but apply judgment — Skill `instructions` are typically shorter and more capability-scoped.
- **Debug why a referenced Skill isn't rendering?** → `analyze-trace-failures`

## Companion Skills

- `build-agent` — author the agents whose instructions reference these Skills via `{{skill.<key>}}`
- `optimize-prompt` — review prose quality for `instructions`
- `run-experiment` — verify a Skill change improves downstream behavior
- `analyze-trace-failures` — diagnose Skills that aren't producing the expected output in production

## Constraints

- **ALWAYS** confirm the project scope (`project_id` set vs. workspace-wide) before `create_skill`. Default to project-scoped unless the user is explicit.
- **ALWAYS** read the current Skill with `get_skill` before `update_skill` — never blind-overwrite tags, description, or instructions.
- **ALWAYS** before `delete_skill`, find places that may reference the Skill via `{{skill.<display_name>}}` or `{{snippet.<display_name>}}` (other Skills' `instructions`, deployment prompts, agent instructions) and warn the user — those references will silently render to empty/missing content after the Skill is gone.
- **ALWAYS** offer tagging the Skill with `retired` (via `update_skill`) as an alternative to `delete_skill`. This is a reversible soft-retire signal visible in the Studio; `delete_skill` is permanent.
- **NEVER** rely on `+NEVER+` (or any prose negation) inside `instructions` as a hard guardrail. Skill instructions are *soft* hints to the model; hard constraints belong in **MCP tool gates** (refuse the call at the tool layer). See [resources/known-caveats.md](resources/known-caveats.md).

## orq.ai Documentation

> **Snippets (the entity, now also called Skills) overview:** <https://docs.orq.ai/docs/prompt-snippets/overview>
> **Using snippets in agent instructions:** <https://docs.orq.ai/docs/agents/agent-studio> (see the Snippets section)
> **Code-assistant Orq Skills (disambiguation):** <https://docs.orq.ai/docs/integrations/code-assistants/skills>

### orq MCP Tools

| Tool | Purpose |
|------|---------|
| `list_skills` | List Skills in the workspace; cursor-paginated, **no server-side filters beyond pagination** — see Pagination & Filtering below |
| `get_skill` | Fetch a single Skill by `skill_id` (returns full Skill object) |
| `create_skill` | Create a new Skill (`display_name`, `description`, `tags`, `path`, `project_id`, `instructions`). Returns `AlreadyExists` if the `display_name` is taken in the workspace — handle that error rather than pre-checking. |
| `update_skill` | Patch an existing Skill by `skill_id` (any of: `display_name`, `description`, `tags`, `path`, `instructions`, `project_id`). PATCH semantics — only sent fields change. |
| `delete_skill` | Permanently delete a Skill by `skill_id`. Does not scrub references in prompts/agent instructions — see Phase 5. |
| `search_entities` | Used to find deployments/agents that may inline the Skill via `{{skill.<display_name>}}` or `{{snippet.<display_name>}}`; combine with `get_deployment` / `get_agent` for the actual reference scan. |

> **Tool discovery:** Before the first run, list the connected MCP server's tools (`/mcp` in Claude Code, or inspect via the client) and confirm the `*_skill` tools above exist. Tool names sometimes vary by workspace or MCP server version.
>
> **REST fallback:** All five tools are backed by `/v2/skills` REST endpoints — `GET /v2/skills` (list, cursor-paginated), `GET /v2/skills/{skill_id}`, `POST /v2/skills`, `PATCH /v2/skills/{skill_id}`, `DELETE /v2/skills/{skill_id}`. Use these directly with `Authorization: Bearer ${ORQ_API_KEY}` if the MCP tools aren't exposed.

### Pagination & Filtering

`GET /v2/skills` (and the `list_skills` MCP tool) accepts **only** cursor-pagination parameters: `limit` (server default 25, max 200), `starting_after`, `ending_before`. **There is no server-side filter for `project_id`, `tags`, `display_name`, or free text.** Filter by those facets **client-side** after pagination, or use `search_entities` if it indexes Skills.

**Pagination loop (pseudocode):**

```text
cursor = None
all_skills = []
while True:
    page = list_skills(limit=200, starting_after=cursor)
    all_skills.extend(page.data)
    if not page.has_more:
        break
    cursor = page.data[-1].id  # the response uses "id", not "skill_id" (it's the same value)
```

After collecting all Skills, filter in memory:

```text
project_skills = [s for s in all_skills if s.project_id == target_project_id]
tagged_skills  = [s for s in all_skills if "policy" in s.tags]
```

### Field reference

| Field | Direction | Notes |
|------|------|------|
| `display_name` | create / update / read | Human-facing label and the **lookup key** used by `{{skill.<display_name>}}` (and `{{snippet.<display_name>}}`). Regex: `^[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*$`, max 255 chars. Must be unique within the workspace; `create_skill` returns `AlreadyExists` on conflict. |
| `description` | create / update / read | Short explanation of what the Skill does. Surfaces in the Studio's Skill picker. |
| `tags` | create / update / read | Array of strings. Filtering is client-side (see above). |
| `path` | create / update / read | Finder-style location, e.g. `Default/Skills` or `cs/policies`. Defaults to project's default skill folder. |
| `project_id` | create / update / read | Optional — omit for workspace-wide. |
| `instructions` | create / update / read | The actual Skill body — modular markdown that gets inlined wherever the Skill is referenced. |
| `skill_id` | read / update / delete | Server-generated id. **The list/get response surfaces it as `id`** but the update/delete inputs take it as `skill_id`. Same value. |
| `workspace_id` | read only | Audit. |
| `created_at`, `updated_at`, `created_by_id`, `updated_by_id` | read only | Audit metadata. |

> **Note on versioning:** `version` is a **read-only, server-stamped** field on the Skill object. The platform assigns it on each `create_skill` / `update_skill` call (visible in the Skill's history in the Studio). You cannot set `version` via the API — do not ask the user "is this a major/minor/patch change?" and do not include `version` in update payloads.

> **Template placeholders:** The primary way Skills get consumed is by referencing them inside any prompt template or agent instruction. Both `{{skill.<display_name>}}` (canonical) and `{{snippet.<display_name>}}` (backward-compatible alias) resolve at render time — the placeholder is replaced with the Skill's `instructions`. The `snippet.` form is retained for backward compatibility with entities originally created as Prompt Snippets. Prefer `{{skill.<display_name>}}` in new authoring.

## Resources

- **Authoring guide** (display name, description, tags, project scoping, path): See [resources/authoring-guide.md](resources/authoring-guide.md)
- **Governance** (consumption patterns, ownership, lifecycle): See [resources/governance-guide.md](resources/governance-guide.md)
- **Known caveats** (template-reference scrubbing on delete, prose-negation anti-pattern): See [resources/known-caveats.md](resources/known-caveats.md)

## Prerequisites

- The orq.ai MCP server is connected (run the `quickstart` skill / `/orq:quickstart` to verify in Claude Code, or the equivalent onboarding flow in your assistant).
- `ORQ_API_KEY` is set.
- The user knows which **project** the Skill belongs to (run `search_directories` if not).
- **Preflight: confirm the Skills API is available.** Try `list_skills` once at session start. If the tool is unknown OR returns "method not found" against `/v2/skills`, the workspace's backend doesn't expose the new entity yet. Two options:
  1. Tell the user and fall back to managing the entity under its legacy name (Prompt Snippet via `/v2/prompts/snippets`).
  2. Ask the user whether to proceed anyway against any partial endpoints they have.

---

## Workflow

Pick the phase that matches the user's intent. Most sessions are a single phase; the **delete** phase always pairs with a reference scan.

### Phase 1: List / audit

Use when the user wants visibility into existing Skills.

1. Call `list_skills` and **paginate to completion** (see Pagination & Filtering above). Default `limit=200` to minimize round-trips.
2. **Apply user filters client-side** — `list_skills` does not accept `project_id` / `tags` / `q` / `display_name` filters. Examples:
   - "Skills in the `cs` project" → filter `project_id == <cs-project-id>` (resolve project key → id via `search_directories` first if needed).
   - "Skills tagged `policy`" → filter `"policy" in s.tags`.
   - "Skills whose name contains `refund`" → substring match on `display_name`.
3. Present a scannable table:
   ```
   Skills (12)
     - customer-support-tone (cs, [tone, voice], path: cs/style)
     - extract-receipt-fields (finance, [extraction], path: Default/Skills)
     - refund-policy (workspace-wide, [policy, cs, retired], path: Default/Skills)
     ...
   ```
4. For each Skill, surface: `display_name`, project (or "workspace-wide"), `tags`, `path`. **Reference counts are expensive** — they require text-searching prompts/agent instructions for `{{skill.<display_name>}}` / `{{snippet.<display_name>}}`. Compute them lazily on user request, not for every row. (See Phase 5 for the reference-scan pattern.)

### Phase 2: Get / inspect

Use before any update or delete, and whenever the user asks "what does Skill X do?"

1. Call `get_skill(skill_id=...)`.
2. Display: `display_name`, `description`, `tags`, `project_id` (or "workspace-wide"), `path`, `version`, `instructions` (truncated). Mention how it's likely consumed: `{{skill.<display_name>}}` (or `{{snippet.<display_name>}}`) inside prompts or agent instructions.
3. If the user asks "where is this used?", run a reference scan (see Phase 5 step 1).

### Phase 3: Create

Use when the user wants a new Skill.

1. **Gather inputs** via `AskUserQuestion`:
   - **`display_name`** — short, descriptive, regex `^[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*$`, ≤255 chars on the platform. This repo's recommended convention is kebab-case ≤50 chars (recommend rather than enforce). See [authoring-guide](resources/authoring-guide.md).
   - **`description`** — one sentence describing *when to apply the Skill*. Used by humans (Studio picker); not a runtime trigger.
   - **`tags`** — at least one functional tag; reuse existing tags where possible (paginate `list_skills` first).
   - **`project_id`** — the target project's id, OR omit for workspace-wide. Default to **project-scoped**; confirm before going workspace-wide. If the user gives a project key, resolve it to an id via `search_directories`.
   - **`path`** — finder location for the Skill, e.g. `Default/Skills` or `policies/refunds`. Default to the project's standard Skill folder.
   - **`instructions`** — the actual content that will be inlined wherever the Skill is referenced. Keep it focused on one capability.
2. **Validate** before submitting:
   - Description starts with "Use when…" or describes a trigger condition.
   - `instructions` does NOT rely on `+NEVER+` / "always refuse" prose for hard guardrails — link the user to [known-caveats](resources/known-caveats.md) and recommend an MCP tool gate instead.
3. Call `create_skill` with the validated payload.
   - **Error: `AlreadyExists`** — the `display_name` is already taken in the workspace. Show the conflicting Skill (paginate `list_skills`, find by `display_name`) and offer either a renamed create or an `update_skill` against the existing one.
   - **Error: project / path validation failure** — the API will return a `CodeInvalidArgument`. Re-ask for `project_id` / `path` and retry.
4. Echo back the new Skill's `id`, `path`, and a one-line summary. Tell the user how to consume it: `{{skill.<display_name>}}` inside any prompt template or agent instruction (or the backward-compat `{{snippet.<display_name>}}`).

### Phase 4: Update

Use when the user wants to edit an existing Skill.

1. **Always `get_skill` first.** Show the current state and confirm the diff the user is about to apply.
2. **Patch fields explicitly.** Only send the fields being changed (`update_skill` is a patch — don't echo back unchanged tags or `instructions`).
3. **`display_name` rename — DANGER.** The `display_name` IS the lookup key for `{{skill.<display_name>}}` and `{{snippet.<display_name>}}`. Renaming it silently breaks every prompt or agent instruction that references the old name. Before sending a rename, run the reference scan from Phase 5 step 1 and warn the user. Offer to update the references in the same session.
4. **Soft-retire pattern** — adding a `retired` tag via `update_skill` is the recommended first step before deletion. It's visible in the Studio, reversible, and leaves a clear signal for audits. Reserve `delete_skill` for actual cleanup after the Skill has been confirmed unused.
5. **`instructions` changes:** if the user is rewriting the body, run a clarity pass first — reuse `optimize-prompt`'s heuristics (clarity, structure, no soft-constraint anti-patterns) but adapt; Skill `instructions` are typically shorter and capability-scoped, not full system prompts.
6. **Verify** by calling `get_skill` post-update and confirming the change landed.

### Phase 5: Delete (with reference scan)

Use when the user wants to permanently retire a Skill. **`delete_skill` is irreversible** and does not scrub `{{skill.<display_name>}}` / `{{snippet.<display_name>}}` references elsewhere — those references silently fail to resolve after delete. Always offer tagging the Skill with `retired` first (Phase 4 step 4) and only proceed to delete when the user is sure.

1. **Reference scan.** Find places that may reference the Skill by its `display_name`:
   - Run `search_entities` to enumerate `prompt`, `deployment`, and `agent` candidates (`search_entities` does **not** expose a `skill` type — sibling Skills are enumerated separately).
   - Paginate `list_skills` to enumerate every other Skill in the workspace whose `instructions` could contain the reference.
   - For each candidate, fetch its full body (`get_deployment` for deployments; `get_agent` for agents; `get_skill` for sibling Skills' `instructions`; prompt bodies come back from `search_entities`/the prompt-fetch tool) and grep the body for both `{{skill.<display_name>}}` and `{{snippet.<display_name>}}` (case-sensitive — match the Skill's exact `display_name`).
   - Note: this scan can be expensive in large workspaces. Cache results within the session.
   - If the user has a faster way to grep their workspace (e.g., a synced repo of prompts), prefer that.
2. **Warn and confirm.** Show the user:
   - The Skill's `display_name`, `id`, project scope, and tags (including any `retired` tag).
   - The list of references found (or "no references found in scanned entities — but the scan only covers prompts/agents/Skills surfaced via `search_entities`; manual checks may be needed").
   - **The two-option choice:** *"(a) Tag as `retired` now and revisit in N days (reversible), or (b) hard-delete and accept that any reference I missed will silently fail to render?"* Default to (a) when the scan found references; default to (b) only when the scan was comprehensive AND empty AND the user has confirmed.
3. **If the user picks delete:** call `delete_skill(skill_id=...)`. Confirm the API success.
4. **Report.** Summarize: Skill deleted (or disabled); references that the user should manually check or update; recommended follow-up if any.

See [resources/known-caveats.md](resources/known-caveats.md) for the full caveat context.

---

## Done When

- The user's intent (list / get / create / update / retire / delete) is fully resolved.
- Any `delete_skill` was preceded by a reference scan AND an explicit choice to delete-rather-than-disable.
- `display_name` renames are gated behind a reference scan and the user understands the breakage risk.
- `instructions` changes were sanity-checked for clarity and for prose-negation anti-patterns before save.
- New or updated Skills have a non-empty `description`, at least one tag, an explicit project scope, and a sensible `path`.
- The user has a clear pointer to how the Skill is (or will be) consumed: `{{skill.<display_name>}}` (or `{{snippet.<display_name>}}`) inside prompts or agent instructions.

## Open in orq.ai

- **Skills index:** [my.orq.ai](https://my.orq.ai/) → Skills
- **Studio:** [my.orq.ai](https://my.orq.ai/) → Studio (Skills appear in the snippet/skill picker when authoring prompts and agents)

When this skill conflicts with live API responses or docs.orq.ai, trust the API.
