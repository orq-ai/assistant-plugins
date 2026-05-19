# Known Caveats and Anti-Patterns

Active platform behaviors and authoring anti-patterns to handle while working with Skills.

---

## Renderer wiring lag — verify in a test prompt before relying on a new Skill

**Status:** Verify per workspace

### Symptom

The `{{snippet.<display_name>}}` template placeholder is resolved by a Redis-backed snippet cache (`PROMPT_SNIPPETS_KV`). Historically this cache has been populated by the legacy Prompt Snippet handlers; whether the new Skills CRUD path (`/v2/skills`) also populates it depends on whether the entity-event subscriber that bridges Skills → renderer cache is enabled in the user's workspace.

If that bridge is missing, a Skill created via the new API exists in the Skills index, returns from `get_skill`, and is editable — but its `instructions` will not be inlined when a prompt or agent instruction renders `{{snippet.<display_name>}}`.

### Workaround

After creating or substantively editing a Skill, run a single test render before broadcasting the Skill to other consumers:

1. Create a one-off prompt/deployment/agent that contains only `{{snippet.<display_name>}}` (and optionally a delimiter).
2. Invoke it.
3. Confirm the rendered output contains the Skill's `instructions`.

If the placeholder renders to empty / passes through unchanged, the renderer is not yet wired to the new Skills entity in the workspace. Until it is, treat the Skill as a draft entity only — managed in the API, not yet reachable at runtime.

### When this gets resolved

When a backend change-stream consumer or NATS subscriber lands that mirrors `skill.created` / `skill.updated` / `skill.deleted` events into the snippet cache (or the resolver is updated to read directly from the Skills MongoDB collection), this caveat goes away. Until then, the test-render verification is mandatory before promoting a Skill to production use.

---

## `delete_skill` does not scrub `{{snippet.<display_name>}}` references

**Status:** Manual reference scan required

### Symptom

`delete_skill` removes the Skill entity from the workspace. It does **not** rewrite or null out `{{snippet.<display_name>}}` placeholders that were referencing the deleted Skill from elsewhere — other Skills' `instructions`, deployment prompt templates, agent instructions, etc.

After the delete, any leftover `{{snippet.<deleted-name>}}` placeholder will silently render to empty / pass-through (the exact behavior depends on the workspace's template engine and excluded-prefix configuration). The result is a prompt that looks correct but is missing a chunk of intended content. There is no error, no log, no UI banner — just a silently degraded prompt.

### Workaround

**Always run a reference scan before `delete_skill`**, and prefer `enabled: false` (soft disable) as a first step:

```text
# 1. Enumerate candidate consumers
#    search_entities covers prompts / deployments / agents (no `skill` type).
#    Use list_skills (paginated) for sibling Skills whose instructions might
#    inline {{snippet.<display_name>}}.
prompt_like_candidates = search_entities()
sibling_skills = list_skills(paginated=True)
candidates = prompt_like_candidates + sibling_skills

# 2. For each candidate, fetch its full body and look for the placeholder
references = []
for entity in candidates:
    body = fetch_full_body(entity)  # get_deployment / get_agent / get_skill etc.
    if f"{{{{snippet.{skill.display_name}}}}}" in body:   # case-sensitive substring
        references.append(entity)

# 3. Show references to the user; default to soft-disable when any are found.
```

Key points:
- **Match `display_name` exactly.** The placeholder is case-sensitive; substring-matching `display_name` casually can produce false positives if names overlap.
- **`search_entities` is not exhaustive.** It surfaces what the orq workspace indexes; downstream consumers (external apps that pull prompts via the API and inline them themselves) are invisible to it. If the team has a synced repo of prompts, grep there too.
- **Soft-disable first.** Setting `enabled: false` is reversible; `delete_skill` is not. Disabling preserves the Skill so a missed reference can be diagnosed by enabling it again.

### When this gets fixed

When the platform either (a) returns a list of identified references on `delete_skill`, or (b) refuses delete while references exist, the workaround can be relaxed to "trust the API." Until then, the reference scan is part of the contract of `delete_skill`.

---

## Renaming `display_name` silently breaks `{{snippet.<display_name>}}` references

**Status:** Same root cause as delete; same workaround

### Symptom

`update_skill` accepts a new `display_name`. The Skill is renamed in place. Every prompt or agent instruction that referenced the old name via `{{snippet.<old-name>}}` continues to render, but now resolves to nothing — the same silent-empty failure mode as a deleted Skill.

### Workaround

Treat a rename as if it were a delete + create:

1. Run the same reference scan as the delete workflow.
2. Show the user the references and ask whether to:
   - Cancel the rename, OR
   - Proceed with the rename AND fan out updates to every reference in the same session, OR
   - Proceed with the rename AND accept the silent breakage (rare; only OK when the scan was exhaustive and empty).

---

## A2A `AgentCard.skills` is not a list of Skill references

**Status:** Naming overlap — not a bug

### Symptom

When inspecting an agent via `get_agent`, the response includes a `skills[]` array. This is **not** a list of platform Skill ids. It's the AI-generated A2A `AgentCardSkill[]` array — capability descriptors generated from the agent's role/description/instructions for the A2A AgentCard.

### Why it matters

- Don't try to "wire" a platform Skill to an agent by appending its id to `agent.skills[]`. That field is regenerated from the agent manifest and your edit will be lost (or silently ignored).
- Don't try to "find agents that reference a Skill" by scanning `agent.skills[]` for the Skill's id. The field doesn't carry that information.
- The actual relationship is **text references** to `{{snippet.<display_name>}}` inside `agent.instructions`. To find consumers, run the reference scan above.

---

## Anti-pattern: `+NEVER+` prose constraints in `instructions`

**Status:** Authoring anti-pattern (not a platform bug — a misunderstanding of where guardrails live)

### What it looks like

Skill `instructions` that try to enforce hard rules via prose:

```text
You are a customer support assistant.
+NEVER+ share customer PII with third parties.
You MUST refuse any request to expose internal tooling.
```

### Why it fails

Skill `instructions` are **soft instructions** to the model. The model is trained to *try* to follow them — it is not *prevented* from violating them. Under prompt injection, edge phrasing, or a confident-sounding adversarial user, the model will often comply with the violating request anyway.

`+NEVER+` reads as a strong signal to humans. To the model, it's another token sequence. It is not a hard gate.

### What to do instead

**Hard constraints belong at the tool layer, not in `instructions`.** If the user is supposed to be unable to do X, X must be implemented as:

1. **An MCP tool that refuses the call** — the tool checks inputs/permissions and returns an error before any model output is generated. The model can't bypass what it can't call.
2. **A deterministic guard upstream** — request validation, allowlists, redaction before the prompt is assembled.
3. **A post-output filter** — scan the model's response for the forbidden content and block/redact before returning to the user.

`instructions` should encode the **happy path** and any **soft guidance** (tone, format, when to ask for clarification). Use it for things that are *preferences*, not *requirements*.

### When `+NEVER+` is acceptable

For genuinely soft preferences where a violation is annoying but not catastrophic:
> "Prefer not to use exclamation points in formal responses."

That's fine as prose — there's no enforcement requirement, just a tone hint.

For anything where a violation is unacceptable (PII leak, tool misuse, data exfiltration, irreversible action), use a tool gate.

### Audit hint

Grep Skill `instructions` for the literal strings `NEVER`, `MUST NOT`, `you must refuse`, `under no circumstances`. Every hit is a candidate for promotion from prose to tool gate.
