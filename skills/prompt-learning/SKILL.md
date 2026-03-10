---
name: prompt-learning
description: Automatically improve prompts by collecting feedback, generating "If [TRIGGER] then [ACTION]" rules via a meta-prompt, and validating with A/B experiments
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, mcp__linear-server__*, orq*
---

# Prompt Learning

Automatically improve prompts through feedback-driven rule generation. Collects human or AI feedback, normalizes it to a shared representation, generates targeted "If [TRIGGER] then [ACTION]" rules via a meta-prompt, and appends them to a `### LEARNED_RULES` section — then validates with A/B experiments.

This skill is **automated and feedback-driven**, distinct from `optimize-prompt` (which is manual/trace-driven). The pipeline is: Collect → Normalize → Meta-Prompt → Aggregate → Apply → Validate.

**Companion skills:**
- `feedback-loop` — set up feedback collection and analyze feedback patterns
- `optimize-prompt` — manual trace-driven prompt refinement (complementary approach)
- `run-experiment` — run A/B experiments comparing prompt versions
- `build-evaluator` — create evaluators to measure prompt improvements
- `trace-analysis` — deep-dive into traces to understand failure modes

## When to use

- User has feedback (human or AI evaluator) and wants automated prompt improvement
- User wants to learn rules from production feedback patterns
- User asks "how do I automatically improve my prompt from feedback?"
- Action plan recommends feedback-driven prompt improvement
- User wants to close the loop between feedback collection and prompt updates
- User has evaluator scores and wants to turn failures into prompt rules

## orq.ai Documentation

Consult these docs when working with the orq.ai platform:
- **Prompts overview:** https://docs.orq.ai/docs/prompts/overview
- **Prompt management:** https://docs.orq.ai/docs/prompts/management
- **Prompt versioning:** https://docs.orq.ai/docs/prompts/versioning
- **Deployments overview:** https://docs.orq.ai/docs/deployments/overview
- **Experiments:** https://docs.orq.ai/docs/experiments/creating
- **Traces:** https://docs.orq.ai/docs/observability/traces
- **Feedback:** https://docs.orq.ai/docs/feedback/overview
- **Evaluators:** https://docs.orq.ai/docs/evaluators/overview

### orq.ai Prompt Capabilities
- Prompts are versioned — each edit creates a new version, previous versions are preserved
- Deployments link to specific prompt versions and model configurations
- Experiments can compare two prompt versions on the same dataset
- Template variables: `{{log.input}}`, `{{log.output}}`, `{{log.messages}}`, `{{log.retrievals}}`, `{{log.reference}}`
- Rules are appended to a `### LEARNED_RULES` section — the rest of the prompt remains untouched

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `search_entities` | Find prompts (`type: "prompts"`) and deployments |
| `list_traces` | Pull recent traces with feedback data |
| `list_spans` | List spans within a trace |
| `get_span` | Get detailed span information |
| `create_experiment` | Run A/B experiment comparing prompt versions |
| `list_experiment_runs` | Check experiment progress |
| `get_experiment_run` | Get experiment results |

**HTTP API fallback** (for operations not yet in MCP):

```bash
# List prompts
curl -s https://my.orq.ai/v2/prompts \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get prompt details with versions
curl -s https://my.orq.ai/v2/prompts/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a new prompt version
curl -s -X POST https://my.orq.ai/v2/prompts/<ID>/versions \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages": [...], "model": "...", "parameters": {...}}' | jq
```

## Core Principles

### 1. Feedback Is Fuel, Not Truth
Human and AI feedback use the **same core method** — only preprocessing differs. Normalize all feedback to a shared representation (verdict + severity + issue tags + explanation) before processing. Never trust a single piece of feedback in isolation.

### 2. Only Recurring Patterns Get Rules
Require 2+ occurrences of a pattern before generating a rule. One-off issues are noise, not signal. The meta-prompt explicitly skips single-occurrence patterns.

### 3. Rules Are Additive, Never Destructive
Rules are appended to a `### LEARNED_RULES` section in the prompt. Never rewrite or remove existing prompt instructions. Rules augment the prompt — they don't replace it.

### 4. Positive Anchors Prevent Regression
Always include p=3 positive traces (regression anchors) in each meta-prompt batch. These ensure generated rules don't break what already works. Skipping anchors leads to over-correction.

### 5. Validate Before Promoting
Never deploy rules without running an A/B experiment. The validation experiment compares the prompt with rules against the baseline on the same dataset. No vibes-checking.

## Issue Taxonomy

The meta-prompt classifies failures into these types:

| Issue Type | Description |
|------------|-------------|
| `accuracy` | Factually incorrect or imprecise outputs |
| `missing_requirement` | Fails to address part of the user's request |
| `policy` | Violates organizational policies or guidelines |
| `safety` | Produces harmful, biased, or inappropriate content |
| `formatting` | Wrong output structure, missing fields, schema violations |
| `verbosity` | Too long or too short for the context |
| `tone` | Inappropriate register, persona drift |
| `tool_use` | Wrong tool selected, incorrect arguments, misinterpreted results |
| `reasoning` | Flawed logic, incorrect deductions |
| `hallucination` | Fabricated facts, citations, or capabilities |

## Destructive Actions

The following actions require explicit user confirmation via `AskUserQuestion` before execution:
- Applying generated rules to a prompt (creating a new version with `### LEARNED_RULES`)
- Promoting a rule-enhanced prompt version to a production deployment
- Removing or modifying existing learned rules

## Defaults

> **Note:** These defaults are preliminary. RES-205 experiments are still running — values will be updated once results are final.

| Parameter | Default | Range | Notes |
|-----------|---------|-------|-------|
| Failures per batch (f) | 10 | 5-15 | Representative sample, not exhaustive |
| Positives per batch (p) | 3 | 2-5 | Regression anchors to prevent over-correction |
| Iterations | 2 | 1-5 | 2 for GPT/Claude; up to 5 for Gemini (pending validation) |
| Occurrence threshold | 2+ | — | One-offs are skipped |
| Rules per iteration | 1-5 | — | Prioritized by frequency × severity |
| Total rule cap | 10 | — | Across all iterations |

## Steps

Follow these steps **in order**. Do NOT skip steps.

### Phase 1: Identify Target and Collect Feedback (Collect)

1. **Identify the target prompt/deployment:**
   - Use `search_entities` with `type: "prompts"` to find the target prompt
   - Use HTTP API to get full prompt details including current version
   - Document: system message, user template, model, parameters

2. **Collect feedback data:**
   - Use `list_traces` to pull traces with feedback for the target deployment
   - Collect at least 50 traces (more is better) from a meaningful time period
   - Separate into: negative feedback traces and positive feedback traces
   - Identify the feedback source: human (thumbs up/down, corrections, free-text) or AI evaluator scores

3. **Verify sufficient data:**
   - Need at least f=10 failure traces to proceed
   - Need at least p=3 positive traces for regression anchors
   - If insufficient, inform user and suggest using `feedback-loop` to set up collection first

### Phase 2: Normalize Feedback (Normalize)

4. **Normalize feedback to shared representation:**

   All feedback — human or AI — gets normalized to this shape:
   ```json
   {
     "verdict": "fail" | "pass" | "borderline",
     "severity": 1-5,
     "issue_tags": ["<from taxonomy>"],
     "explanation": "..."
   }
   ```

   **For human feedback:**
   - Thumbs down → `{"verdict": "fail", "severity": 3, "issue_tags": [], "explanation": ""}`
   - Free-text correction → extract issue tags and explanation from the text
   - Numerical rating (e.g., 1-5) → map to verdict (1-2: fail, 3: borderline, 4-5: pass)

   **For AI evaluator feedback:**
   - Boolean false → `{"verdict": "fail", "severity": 3, "issue_tags": [], "explanation": "<from evaluator>"}`
   - Categorical/numerical → map to verdict based on scale, carry explanation through

   If raw feedback lacks explanations (e.g., bare thumbs-down), use the LLM to enrich: pass the input/output pair and ask for a brief failure analysis to populate `issue_tags` and `explanation`.

5. **Sample the batch:**
   - Sample f=10 representative failures (diverse issue types, not all the same failure)
   - Sample p=3 positive traces as regression anchors
   - If more failures exist, prioritize diversity across issue types

### Phase 3: Generate Rules (Meta-Prompt)

6. **Build the meta-prompt** by filling in the template below with collected data:
   - `PROMPT_TYPE`: "agent" or "evaluator" based on target
   - `CURRENT_PROMPT`: full text of the current prompt version
   - `ITERATION`: current iteration number (starts at 1)
   - `FEEDBACK_SOURCE`: "human" or "ai_eval"
   - `FAILURE_EXAMPLES`: the f=10 sampled failures with normalized feedback
   - `POSITIVE_EXAMPLES`: the p=3 sampled positive traces

7. **Execute the following meta-prompt** (send it to the LLM with the variables filled in):

   ~~~
   You are a prompt engineer improving a prompt based on feedback from multiple examples.

   GOAL: Analyze a batch of feedback (failures + positive anchors) and produce minimal, high-impact rules that:
   1. Fix recurring failure patterns
   2. Don't break existing good behavior (regression anchors)

   INPUTS:
   1) PROMPT_TYPE: "agent" | "evaluator"
   2) CURRENT_PROMPT: The prompt to improve
   3) ITERATION: Current iteration number
   4) FEEDBACK_SOURCE: "human" | "ai_eval"
   5) FAILURE_EXAMPLES (5-15 samples with negative feedback):
      [{"user_input": "...", "model_output": "...", "reference": "..." (optional), "feedback": <see shapes below>}, ...]
   6) POSITIVE_EXAMPLES (2-5 regression anchors):
      [{"user_input": "...", "model_output": "...", "feedback": "pass" | {"value": true, ...}}, ...]

   FEEDBACK SHAPES:
   - Human categorical: "fail" | "pass" | "borderline"
   - Human numerical: 3 (just the number)
   - Human free text: "The response was too vague..."
   - AI eval boolean: {"value": true|false, "explanation": "..."}
   - AI eval categorical: {"value": "A"|"B"|"C", "explanation": "..."}
   - AI eval numerical: {"value": 6, "scale": "1-10", "explanation": "..."}
   - Enriched normalized: {"verdict": "fail", "severity": 4, "issue_tags": ["missing_requirement"], "explanation": "..."}

   PROCESS:

   STEP 1 — ANALYZE FAILURE PATTERNS:
   Group failures by issue type. Identify recurring patterns (2+ occurrences).
   Issue taxonomy: accuracy, missing_requirement, policy, safety, formatting, verbosity, tone, tool_use, reasoning, hallucination.
   Output: {"patterns": [{"issue_tag": "...", "count": N, "severity": 1-5, "examples": [indices], "root_cause": "..."}], "one_off_issues": [...]}

   STEP 2 — CHECK AGAINST POSITIVE ANCHORS:
   For each pattern, verify the fix won't break positive examples.
   Output: {"anchor_conflicts": [{"pattern": "...", "conflicting_anchor": index, "conflict_reason": "..."}]}

   STEP 3 — GENERATE RULES (only for recurring patterns without conflicts):
   Create 1-5 rules. Format: "If [TRIGGER], then [ACTION]."
   Prioritize by: frequency × severity.
   Skip: one-offs, anchor conflicts, patterns too vague to test.

   STEP 4 — FORMAT RULES_TO_APPEND:
   Text block for ### LEARNED_RULES section.

   STEP 5 — GENERATE REGRESSION TESTS:
   Create 5-10 test cases: 3-5 "should_now_pass" + 2-5 "should_still_pass".

   STEP 6 — ITERATION GUIDANCE:
   Recommend "continue" (significant patterns remain) or "stop" (diminishing returns).

   OUTPUT FORMAT:
   A) PATTERN_ANALYSIS — JSON with patterns and one_off_issues
   B) ANCHOR_CHECK — JSON with anchor_conflicts and safe_to_patch list
   C) RULES — numbered list
   D) RULES_TO_APPEND — text block for the prompt
   E) REGRESSION_TESTS — JSON array of test cases
   F) ITERATION_GUIDANCE — {"recommendation": "continue"|"stop", "reason": "...", "remaining_issues": N, "expected_next_iteration_gain": "high"|"medium"|"low"}

   NOW PROCESS THE ACTUAL INPUT.
   ~~~

8. **Collect the structured output** (sections A through F).

9. **Review the output** with the user:
   - Show the identified patterns and their frequency/severity
   - Show the generated rules
   - Highlight any anchor conflicts
   - Present ITERATION_GUIDANCE recommendation

### Phase 4: Aggregate and Apply

10. **Aggregate rules** across iterations (if iteration > 1):
    - Merge new rules with existing `### LEARNED_RULES` section
    - Remove duplicates or conflicting rules
    - Enforce total rule cap of 10

11. **Apply rules to the prompt** — **ask user confirmation first:**
    - Create a new prompt version with `### LEARNED_RULES` section appended
    - Use HTTP API to create the new version
    - Document what rules were added and which patterns they address

    Format of the appended section:
    ```
    ### LEARNED_RULES
    - If [TRIGGER], then [ACTION].
    - If [TRIGGER], then [ACTION].
    ...
    ```

### Phase 5: Validate (A/B Experiment)

12. **Set up a validation experiment:**
    - Use `create_experiment` to compare baseline (no rules) vs variant (with rules)
    - Use the same dataset for both runs
    - Include evaluators that measure the targeted failure types
    - Include the regression tests from the meta-prompt output

13. **Run the experiment and analyze results:**
    - Use `list_experiment_runs` to monitor progress
    - Use `get_experiment_run` to fetch results
    - Compare:
      ```
      | Evaluator | Baseline | Variant | Delta |
      |-----------|----------|---------|-------|
      | [target metric] | X% | Y% | +Z% |
      | [regression metric] | X% | Y% | +Z% |
      ```

14. **Decision framework:**
    - **Clear win** (>5% improvement on target, no regression) → Promote variant
    - **Mixed results** (improvement + regression elsewhere) → Investigate, iterate
    - **No improvement** → Re-examine feedback normalization, try different samples
    - **Regression** → Revert, rules may be too aggressive

### Phase 6: Iterate

15. **Check iteration guidance:**
    - If meta-prompt recommends `"continue"` AND iteration < max (2 for GPT/Claude, 5 for Gemini):
      - Return to Phase 1 Step 2 with updated prompt (now including rules)
      - Collect fresh feedback or use remaining unprocessed failures
      - Increment iteration counter
    - If meta-prompt recommends `"stop"` OR max iterations reached:
      - Present final summary to user
      - If validated, **ask user confirmation** to promote to production deployment

16. **Final summary:**
    ```
    ## Prompt Learning Summary
    - **Target:** [prompt/deployment name]
    - **Iterations completed:** [N]
    - **Rules generated:** [N]
    - **Feedback source:** human | ai_eval
    - **Key patterns addressed:** [list]
    - **Validation result:** [pass/fail with metrics]
    - **Status:** [promoted / pending promotion / reverted]
    ```

## Anti-Patterns

| Anti-Pattern | Why It's Wrong | What to Do Instead |
|---|---|---|
| Acting on single-occurrence feedback | Noise, not signal | Require 2+ occurrences before generating rules |
| Rewriting the whole prompt with rules | Destroys existing instructions | Only append to `### LEARNED_RULES` section |
| Skipping positive anchors | Rules may break what already works | Always include p=3 positive traces in each batch |
| Running more than 5 iterations | Diminishing returns, overfitting risk | Stop at 2 iterations (5 for Gemini) |
| Treating human and AI feedback differently in the pipeline | Research shows same method works for both | Normalize to shared representation, then process identically |
| Deploying rules without validation experiment | No evidence the rules actually help | Always A/B test before promoting |
| Generating rules from too few failures | Insufficient pattern evidence | Wait for f=10 failures minimum per batch |

## Open in orq.ai

After completing this skill, direct the user to the relevant platform page:

- **View/edit the prompt:** `https://my.orq.ai/prompts` — review the prompt with `### LEARNED_RULES` section
- **Check traces with feedback:** `https://my.orq.ai/traces` — inspect traces that provided the feedback signal
- **View experiment results:** `https://my.orq.ai/experiments` — review the A/B validation experiment
- **Feedback overview:** `https://my.orq.ai/feedback` — monitor ongoing feedback collection
