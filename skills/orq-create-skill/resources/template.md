# Output skill template

The contract you produce follows this skeleton. Every section has a purpose; cut sections that do not apply, but do not invent new ones without reason.

```markdown
---
name: <kebab-case-name>
description: >
  <What the skill covers, when to use it, when NOT to use it.
  This routes skill selection, so be specific enough that the agent
  picks this skill and not a neighbour.>
allowed-tools: <curated list>
---

# <Title>

<Purpose statement: who you are and what you do when this skill is active.>

## Gotchas (read first)

<Numbered list. Each gotcha is a verified surprise that wastes time
if you do not know it. Grounded in a real test or doc review.>

## When to use

<Distinct branches that should trigger this skill.>

## When NOT to use

<Boundaries. Name the neighbouring skill for each exclusion.>

## Constraints

<Rules that hold across every step: what must be verified, what must never
be written, what needs user approval. Omit if the steps carry them.>

## <Grouped capability sections>

<For each logical group of operations: what it does, the exact invocation
pattern (tested, not pseudocode), the response shape that matters, and
the expected behaviour or pass criterion.>

## Workflows

<Common multi-step sequences. Happy path first, then error recovery.>

## Cleanup / Safety

<What to clean up after use. Which operations are destructive.>

## Done When

<Aggregate completion criteria for the whole skill, independent of per-step
criteria. What must be true before the agent reports the work as finished.>

## Companion skills

<Related skills and the boundary with each. Lets the agent route
without blind forwarding.>
```

## Rules

- **Gotchas lead.** The agent reads them first and avoids the traps.
- **Tested invocations only.** Every example call in the skill must have been run (thorough) or confirmed against docs (fast).
- **Frontmatter is closed.** Only `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`. Record the verification date in the CHANGELOG entry, not in a custom frontmatter field, and mark operations that could not be tested as `[unverified: reason]`.
