# Documentation & Resolution — the lookup order

Shared by every orq skill. When you need an orq.ai platform detail, check in
this order and trust the source higher in the list:

1. **Live queries** — the orq MCP read tools and `orq` CLI read verbs. API
   responses are always authoritative. Each skill names the specific calls that
   answer its own questions first.
2. **orq.ai documentation MCP** — `search_orq_ai_documentation` /
   `get_page_orq_ai_documentation` to look up platform docs programmatically.
3. **[docs.orq.ai](https://docs.orq.ai)** — browse the official documentation
   directly. Each skill's own `## orq.ai Documentation` section carries the deep
   links for its domain.
4. **The skill file** — may lag behind API or docs changes.

When a skill's content conflicts with live API behaviour or the official docs,
the higher source wins. Say which source you used when they disagree; a skill
that has drifted is worth reporting, not just working around.

A skill that ships its own verified reference — `orq-analyze-traces` and
`orq-improve-agent` both use [`trace-queries.md`](trace-queries.md) — inserts it
directly below live queries, because it records probe results the docs do not.
