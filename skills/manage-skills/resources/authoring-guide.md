# Authoring Guide: Display Name, Description, Tags, Project Scope, Path

How to author an orq.ai Skill so it's discoverable, scoped correctly, and renders cleanly wherever it's referenced.

---

## `display_name` (the lookup key)

`display_name` is both the human-facing label AND the lookup key used by `{{snippet.<display_name>}}` placeholders. Pick it carefully — renaming it after consumers exist silently breaks every reference. See [known-caveats.md](known-caveats.md).

**Platform constraints (enforced):**
- Regex: `^[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*$` (alphanumeric with optional single dash/underscore separators)
- Max 255 characters
- Must be unique within the workspace — `create_skill` returns `AlreadyExists` on conflict

**This repo's recommended convention** (a stricter subset that keeps lists scannable and placeholders readable):
- **kebab-case**, lowercase, ASCII only — e.g., `extract-receipt-fields`
- **≤50 characters** — long names get truncated in Studio tables and bloat placeholders
- **Verb-noun preferred** — `summarize-ticket`, `classify-intent`, `extract-pii`
- **Avoid generic verbs alone** — `handle-thing`, `do-task`, `process` say nothing
- **No version suffixes** — `summarize-ticket-v2` is an anti-pattern; treat the Skill itself as the unit of change and rely on the activity log for history

These are recommendations, not enforced by the API. Diverge if a stronger convention already exists in the workspace, but stay consistent.

**Good (recommended convention):**
- `extract-invoice-line-items` → referenced as `{{snippet.extract-invoice-line-items}}`
- `redact-pii-from-transcript`
- `format-currency-eur`

**Bad:**
- `helper` (too vague)
- `the-skill-that-handles-customer-support-emails-with-tone-checking` (too long; ugly in placeholders)
- `summarize-ticket-v2` (version belongs in the activity log)

---

## `description`

`description` is human-facing copy shown in the Studio's Skill picker and audit views. **It is not a runtime trigger** — Skills are inlined wherever a `{{snippet.<display_name>}}` placeholder exists in a prompt/agent instruction; the model doesn't pick them based on description.

**Rules:**
- **One sentence.** Keep it scannable.
- **Lead with what the Skill does**, not how. Implementation detail belongs in `instructions`.
- **Mention the intended consumer** if it's not obvious from the name — e.g., "Reusable PII redaction block for customer-support agents."
- **Avoid "always" / "never" / "must"** — those are constraints, not descriptions. Hard rules belong in tool gates, not in description text.

**Good:**
> Reusable receipt-extraction snippet — extracts merchant, total, tax, and line items into structured JSON. Inline in any prompt that processes receipt images or PDFs.

**Bad:**
> This skill is a powerful tool that helps you handle receipts in many different formats using OCR.
> *(no concrete output, marketing voice, implementation leak)*

---

## `tags`

Tags group Skills in the Studio and let callers narrow `list_skills` output **client-side** (`GET /v2/skills` does not accept a `tags` filter — paginate, then filter in memory). Good tagging makes a workspace navigable; bad tagging makes Skills invisible.

**Rules:**
- **At least one tag.** Untagged Skills are easy to lose in long lists.
- **Reuse existing tags.** Paginate `list_skills` and see which tags are already in use before inventing a new one. Tag sprawl is the silent killer of Skill discoverability.
- **Two axes of tagging are usually enough:**
  - **Functional** — what the Skill *does*: `extraction`, `summarization`, `classification`, `formatting`, `tone`, `policy`
  - **Domain** — where it applies: `finance`, `cs` (customer support), `legal`, `internal`
- **Avoid consumer-specific tags.** A tag like `used-by-checkout-agent` becomes wrong the moment a second consumer adopts the Skill — use the reference scan in [governance-guide.md](governance-guide.md#finding-the-consumers-of-a-skill) to find consumers on demand.
- **Lowercase, kebab-case** for consistency.

**Recommended tag count:** 1–4 tags per Skill. More than 5 tags usually means the Skill is doing too many things.

---

## `project_id` (project scoping)

Every Skill is either **project-scoped** (`project_id` set to a project's id) or **workspace-wide** (`project_id` omitted). Workspace-wide Skills are visible to every consumer across the workspace.

**Default to project-scoped.** Workspace-wide Skills are shared infrastructure — every workspace member can see them, every prompt can reference them, and a bad edit affects everyone.

**When project-scoped is right:**
- The Skill encodes project-specific business logic (e.g., a refund policy that only applies to the EU project)
- The Skill is still being iterated on and shouldn't be discoverable across teams yet
- Different projects need different versions of the same idea (e.g., `extract-receipt-fields` per region)

**When workspace-wide is right:**
- The Skill is genuinely reusable across teams and projects (e.g., `redact-pii`, `format-currency`)
- The Skill has stabilized — no recent breaking changes, used by ≥2 consumers
- Ownership is clear (named owner in the description or `owner:` tag)

**How to choose:**

1. Start project-scoped (set `project_id`).
2. After the Skill has been stable for ≥2 weeks and used by ≥2 consumers in the same project, ask: "would another project benefit from this?"
3. If yes, **create a copy** with `project_id` omitted (workspace-wide). Don't move — existing references still point at the project-scoped `display_name`. Sunset the original after consumers are re-pointed.

> **Resolving project keys → ids:** if the user gives you a project key/name, run `search_directories` to convert it to the `project_id` value the API expects.

---

## `path`

`path` is the finder-style location of the Skill inside its project (e.g., `Default/Skills`, `cs/policies`, `finance/extraction`). It controls where the Skill appears in the Studio's folder tree.

**Rules:**
- **Default to the project's standard Skill folder** (often `Default/Skills`) unless the team has an explicit folder convention.
- **Mirror existing folders.** Paginate `list_skills` and reuse paths already in the target project — divergent paths fragment the Studio.
- **Use slashes, not backslashes**, and keep segment names short and descriptive.
- **Group by purpose, not by owner.** Folder-by-team becomes wrong the moment a Skill moves teams; folder-by-purpose ages better.

---

## `enabled`

`enabled` is a boolean that defaults to `true` on create. When `false`, the Skill is preserved in the workspace but `{{snippet.<display_name>}}` references stop resolving (verify the exact render behavior in your workspace — empty, pass-through, or skip).

**When to seed `enabled: false`:**
- You're staging the Skill for review before any consumer points at it.
- You're setting up parallel versions for a controlled cutover.

In practice you almost always create with `enabled: true` (the default) and use `enabled` later as the soft-retirement lever (see [governance-guide.md](governance-guide.md#retire)).

---

## `instructions` (the Skill body)

`instructions` is the actual content that gets inlined wherever the Skill is referenced. Keep it:
- **Focused on one capability.** If you find yourself writing "and also…", split into two Skills.
- **Specific.** Include 1–2 input/output examples.
- **Free of hard constraints expressed as prose.** Don't write "NEVER do X" or "you MUST refuse Y" — those are soft hints, not enforcement. See [known-caveats.md](known-caveats.md#anti-pattern-never-prose-constraints-in-instructions).
- **Sanity-checked before save.** Reuse `optimize-prompt`'s clarity heuristics, but apply judgment — Skill `instructions` are typically shorter and more capability-scoped than a system prompt.
