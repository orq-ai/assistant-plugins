# Catalog: optimize-prompt

Tests for [`skills/optimize-prompt/SKILL.md`](../../../skills/optimize-prompt/SKILL.md).
Source scenarios: [`../../skills.md`](../../skills.md) (optimize-prompt).

## Functional cases

> optimize-prompt is primarily analytical and creates no live resource until the user approves a new
> version, so functional coverage is light. Most validation is behavioural.

### F1. Analysis produces structured suggestions
- **Operation:** give the skill an inline prompt and let it analyze (no apply).
- **Verify:** the analysis is structured against the skill's prompting-guidelines framework and yields
  concrete, specific suggestions (not vague advice). No template variables were substituted with
  literal content.
- **Cleanup:** none (no version applied).

## Behavioural scenarios

### B1. Analyze a weak prompt
- **Type:** explicit
- **Trigger:** "Optimize this prompt: You are a helpful assistant. Answer questions."
- **Provide:** the inline prompt above.
- **Expected routing:** optimize-prompt
- **PASS:** analyzes against the structured guidelines framework (multiple dimensions); produces
  concrete improvement suggestions; offers to show a diff and asks for approval before applying;
  recommends `run-experiment` to validate afterward; preserves the original for rollback.
- **Anti-patterns (FAIL):** applies a rewrite without showing a diff or asking; changes what the
  prompt does (not just how it's expressed); substitutes `{{variables}}` with literal content;
  removes tool/function definitions.

### B2. Implicit — a need, not the artifact
- **Type:** implicit
- **Trigger:** "My assistant's answers are rambling and inconsistent — can you make the system instructions better?"
- **Expected routing:** optimize-prompt
- **PASS:** routes here on the description; analyzes the prompt against the framework; shows a diff and
  asks before applying.
- **Anti-patterns (FAIL):** misroutes to analyze-trace-failures (no production traces mentioned) or
  build-agent; rewrites silently.

## Negative controls (must NOT fire optimize-prompt)

### N1. Performance problem ≠ prompt problem
- **Type:** negative
- **Trigger:** "My app is slow and uses too much memory — can you profile it and speed it up?"
- **Expected routing:** none (ordinary performance work) — optimize-prompt must not fire.
- **PASS:** treats it as general engineering; does not invoke the prompt-optimization framework.
- **Fired = FAIL:** running prompt analysis on an unrelated performance request.

### N2. Production failures → analyze first
- **Type:** negative
- **Trigger:** "My deployment's outputs are wrong in production a lot — where do I start?"
- **Expected routing:** analyze-trace-failures — optimize-prompt must not fire first (the skill's own
  "Do NOT use when production traces show failures").
- **PASS:** routes to analyze-trace-failures.
- **Fired = FAIL:** jumping straight to prompt rewriting before error analysis.
