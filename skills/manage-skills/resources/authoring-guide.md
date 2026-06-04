# Authoring Guide: Display Name, Description, Tags, Project Scope, Path

How to author an orq.ai Skill so it's discoverable, scoped correctly, and renders cleanly wherever it's referenced.

---

## `display_name` (the lookup key)

`display_name` is both the human-facing label AND the lookup key used by `{{skill.<display_name>}}` (and the backward-compatible `{{snippet.<display_name>}}`) placeholders. Pick it carefully — renaming it after consumers exist silently breaks every reference. See [known-caveats.md](known-caveats.md).

**Platform constraints (enforced):**
- Regex: `^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*$` — must start with a letter, underscores only as word separators (no hyphens, no dots — names are used as template variables and dashes are not allowed)
- Max 255 characters
- Must be unique within the workspace — `create_skill` returns `AlreadyExists` on conflict

**This repo's recommended convention** (a stricter subset that keeps lists scannable and placeholders readable):
- **snake_case**, lowercase, ASCII only — e.g., `extract_receipt_fields`
- **≤50 characters** — long names get truncated in Studio tables and bloat placeholders
- **Verb-noun preferred** — `summarize_ticket`, `classify_intent`, `extract_pii`
- **Avoid generic verbs alone** — `handle_thing`, `do_task`, `process` say nothing
- **No version suffixes** — `summarize_ticket_v2` is an anti-pattern; treat the Skill itself as the unit of change and rely on the activity log for history

These are recommendations, not enforced by the API. Diverge if a stronger convention already exists in the workspace, but stay consistent.

**Good (recommended convention):**
- `extract_invoice_line_items` → referenced as `{{skill.extract_invoice_line_items}}`
- `redact_pii_from_transcript`
- `format_currency_eur`

**Bad:**
- `helper` (too vague)
- `the_skill_that_handles_customer_support_emails_with_tone_checking` (too long; ugly in placeholders)
- `summarize_ticket_v2` (version belongs in the activity log)
- `extract-receipt-fields` (hyphens rejected by the API — use underscores)

---

## `description`

`description` is human-facing copy shown in the Studio's Skill picker and audit views. **It is not a runtime trigger** — Skills are inlined wherever a `{{skill.<display_name>}}` (or `{{snippet.<display_name>}}`) placeholder exists in a prompt/agent instruction; the model doesn't pick them based on description.

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
- Different projects need different versions of the same idea (e.g., `extract_receipt_fields` per region)

**When workspace-wide is right:**
- The Skill is genuinely reusable across teams and projects (e.g., `redact_pii`, `format_currency`)
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

## `instructions` (the Skill body)

`instructions` is the actual content that gets inlined wherever the Skill is referenced. Keep it:
- **Focused on one capability.** If you find yourself writing "and also…", split into two Skills.
- **Specific.** Include 1–2 input/output examples.
- **Free of hard constraints expressed as prose.** Don't write "NEVER do X" or "you MUST refuse Y" — those are soft hints, not enforcement. See [known-caveats.md](known-caveats.md#anti-pattern-never-prose-constraints-in-instructions).
- **Sanity-checked before save.** Reuse `optimize-prompt`'s clarity heuristics, but apply judgment — Skill `instructions` are typically shorter and more capability-scoped than a system prompt.
