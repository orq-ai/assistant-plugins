# Prompting Guidelines

The 11 dimensions an agent's `instructions` are checked against. `orq-improve-agent` rewrites against them; `orq-review-agent` reports against them. Correct a guideline here once, so the review and the fix never disagree.

Each guideline is a dimension to evaluate: identify what is missing or weak, then improve it.

1. **Role assignment & expertise** — clear, emphasized role with specific domain expertise
2. **Task definition** — clear explanation of what the system will do
3. **Stress induction** — emphasis on the importance and criticality of the task
4. **Guidelines** — the task broken into clear guidelines: task explanation, behavioral constraints, communication style, knowledge boundaries
5. **Output format** — specified and stressed. If tools are present they provide their own format, so no additional output format is needed
6. **Tool calling** — tools are part of the task. Never suggest removing them. Keep definitions in their original state; adjustments to *how they are referenced* are fair game
7. **Reasoning** — for complex tasks, reasoning must be instructed and must appear before the final answer. If reasoning is instructed but the output format has no space for it, add one (e.g. a `reasoning` key in JSON)
8. **Examples** — few-shot examples in `<example>` XML tags, with proper variable formatting inside
9. **Remove unnecessary content** — no gratuitous markdown, emojis, XML tags, or contradictions
10. **Proper variable usage** — `{{double curly brackets}}` should appear once near the end; earlier references use XML tags
11. **Recap** — a one-sentence recap of the task and format at the end

**Ground each suggestion in a trace.** With an error-analysis artifact in hand, name the failure mode and the guideline together: *"Mode 1 (persona drift, 12.5%) → guideline 1: the role is stated once and never reinforced."* A guideline finding with no failing trace behind it is a **best-practice suggestion**, not a fix. Say which it is.
