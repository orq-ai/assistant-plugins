# Governance Guide: Consumption, Ownership, Lifecycle

How Skills get consumed, who owns them, and how they retire.

---

## How a Skill gets consumed

A platform Skill reaches the model in exactly one way: as a **template placeholder inside a prompt or agent instruction**. Anywhere a prompt template is rendered (deployments, agent system prompts, other Skills), the placeholder

```text
{{skill.<display_name>}}
```

is replaced with the Skill's `instructions` at render time. The backward-compatible alias `{{snippet.<display_name>}}` also resolves and falls back to the Skill whose `display_name` matches — prefer `{{skill.<...>}}` in new authoring.

You can chain references: a Skill's `instructions` may itself contain `{{snippet.<other-display-name>}}` placeholders that the renderer expands recursively.

**Implications:**
- The `display_name` is load-bearing. Renaming a Skill changes the lookup key and silently breaks every existing reference. See [known-caveats.md](known-caveats.md).
- There is no per-agent attachment list. The relationship between an agent and a Skill is implicit — it lives in the agent's `instructions` text, not in a structured `skills[]` array on the agent. (The `AgentCard.skills` field is unrelated AI-generated capability metadata. See [known-caveats.md](known-caveats.md).)
- To soft-retire a Skill, add a `retired` tag via `update_skill`. There is no `enabled` field — the `retired` tag convention is visible in the Studio and reversible.

---

## Finding the consumers of a Skill

There is no `list_consumers(skill_id)` API. To find every place a Skill is referenced, you have to text-search the rendered surface:

```text
1. Enumerate candidates with search_entities (supports type="prompt", "deployment",
   "agent", and "skill"). Note: search_entities only matches metadata fields
   (display_name, key, description) — it does NOT search body text. Use it to
   enumerate entities, then always fetch bodies separately to find {{skill.X}}
   references. Also paginate list_skills for sibling Skills not caught by search.
2. For each candidate, fetch its full body with the appropriate get_* tool
   (get_deployment, get_agent, get_skill, or the prompt body returned by
   search_entities).
3. Substring-match both `{{skill.<display_name>}}` and `{{snippet.<display_name>}}` (case-sensitive) in the body.
4. Collect the matches.
```

**When to do the scan:**
- Before `delete_skill` (mandatory).
- Before renaming `display_name` (mandatory).
- When the user asks "where is this Skill used?"
- When auditing workspace-wide Skills for ownership / sunset candidates.

**Cost considerations:**
- The scan is O(N entities × body size). Cache results within the session.
- For large workspaces, prefer a synced repo grep if the team has one — much cheaper than fanning out HTTP requests.

---

## Ownership

There is no first-class "owner" field on a Skill today. Establish ownership conventions in `tags` and `description`:

- **Tag** — add an `owner:<team-or-handle>` tag (e.g., `owner:cs-team`) to workspace-wide Skills.
- **Description** — for project-scoped Skills, ownership is implicit in the project. For workspace-wide, mention the owning team in the description's trailing context if it matters for incident response.

Audit unowned workspace-wide Skills periodically: paginate `list_skills`, filter `project_id is None` client-side, then look for missing `owner:` tags.

---

## Lifecycle: Create → Iterate → Stabilize → Retire

### Create
Always start project-scoped (set `project_id`). Wire one consumer first (a single deployment or agent instruction) and verify the rendered output before broadening.

### Iterate
- Iterate on `instructions` and `description`, not `display_name`. **Renaming `display_name` breaks every `{{snippet.<old-name>}}` reference.** See [known-caveats.md](known-caveats.md) for the rename workflow.
- Sanity-check `instructions` rewrites (clarity, structure, no prose-negation anti-patterns) — see `orq-optimize-prompt` for prose heuristics, but apply judgment: Skill `instructions` are usually shorter and more capability-scoped than a system prompt.
- After each meaningful change, run `orq-run-experiment` against an agent or deployment that consumes the Skill to confirm the change improves (or at least doesn't regress) behavior.

### Stabilize
A Skill is stable when:
- It hasn't had an `instructions` change in ≥2 weeks
- It's referenced by ≥2 prompts/agents
- No open incidents tag the Skill as a contributor

At that point, consider promoting to workspace-wide if it's broadly reusable. See [authoring-guide.md](authoring-guide.md#project_id-project-scoping).

### Retire
Retire a Skill when:
- The prompts/agents using it are decommissioned, OR
- A replacement Skill covers the same capability better

**Retirement workflow:**

1. Run the reference scan (above) to identify every consumer of the Skill.
2. Decide per consumer: replace (point them at the new Skill name) or remove (drop the placeholder).
3. **Tag as retired first, delete later.** Add a `retired` tag via `update_skill` and wait at least one full traffic cycle (a day, a week — depends on how the prompts run). If nothing breaks, proceed to delete. If something breaks, remove the `retired` tag, investigate, and fix the missed reference.
4. **Wire replacements before deleting**, not after — atomicity matters.
5. Run `delete_skill`.
6. Note retirement in the workspace changelog if your team keeps one.

The platform records a semantic-version *activity log entry* on each create/update (visible in the Skill's history view). The Skill object carries a read-only `version` field stamped server-side — you cannot set it via the API, so don't design workflows that treat it as a user-settable value.

---

## Audit checklist

Periodic Skills audit (suggested quarterly):

- [ ] Any workspace-wide Skill with no `owner:` tag? — assign or move to project-scoped.
- [ ] Any Skill with no `{{skill.<display_name>}}` / `{{snippet.<display_name>}}` references in scanned entities? — candidate for tagging `retired`, then deletion.
- [ ] Any Skill tagged `retired` for >30 days with no consumer references? — candidate for deletion.
- [ ] Any prompt/agent instruction with a `{{skill.<display_name>}}` or `{{snippet.<display_name>}}` placeholder whose target Skill no longer exists? — orphan reference; either restore the Skill, point the placeholder at a replacement, or remove the placeholder.
- [ ] Any two Skills with near-duplicate `instructions`? — consolidate; rename references.
- [ ] Any Skill `instructions` containing `NEVER`, `MUST NOT`, or "you must refuse"? — prose-negation anti-pattern, replace with MCP tool gate (see [known-caveats.md](known-caveats.md#anti-pattern-never-prose-constraints-in-instructions)).
