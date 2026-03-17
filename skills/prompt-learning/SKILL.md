---
name: prompt-learning
description: Automatically improve prompts by collecting feedback, generating "If [TRIGGER] then [ACTION]" rules via a meta-prompt, and validating with multi-judge experiments
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Prompt Learning

Automatically improve prompts through feedback-driven rule generation. Collects human or AI feedback, normalizes it to a shared representation, generates targeted "If [TRIGGER] then [ACTION]" rules via a meta-prompt, and appends them to a `### LEARNED_RULES` section — then validates with multi-judge experiments.

This skill is **automated and feedback-driven**, distinct from `optimize-prompt` (which is manual/trace-driven). The pipeline is: Collect → Normalize → Meta-Prompt → Aggregate → Apply → Validate.

**Companion skills:**
- `optimize-prompt` — manual trace-driven prompt refinement (complementary approach)
- `run-experiment` — run A/B experiments comparing prompt versions
- `build-evaluator` — create evaluators to measure prompt improvements
- `analyze-trace-failures` — deep-dive into traces to understand failure modes

## When to use

- User has feedback (human or AI evaluator) and wants automated prompt improvement
- User wants to learn rules from production feedback patterns
- User asks "how do I automatically improve my prompt from feedback?"
- Action plan recommends feedback-driven prompt improvement
- User wants to close the loop between feedback collection and prompt updates
- User has evaluator scores and wants to turn failures into prompt rules
- The target prompt serves a **focused domain** (email writing, code review, customer support, data extraction)

## When NOT to use

- **Broad/general tasks** — prompt learning shows 0% significant improvement on general helpfulness or open-ended chat (RES-205: 0/70 configs significant on MTBench helpfulness)
- **Top-tier models already at ceiling** — models scoring >4.5/5 on baseline show no improvement (GPT-4o at 4.75/5 had zero gain)
- **Without multi-judge validation** — single-judge evaluation overestimates improvement by 40-60%. If you cannot set up 3+ diverse judge models, results will be unreliable
- Use `optimize-prompt` instead for manual, trace-driven refinement on any domain type

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

### 1. Focused Domains Only
Prompt learning works on **narrow, well-defined tasks** (email writing, code review, customer support, data extraction). It does not work on broad/general helpfulness — RES-205 showed 0% significant improvement across 70 configurations on general helpfulness tasks. Always verify the target prompt serves a focused domain before proceeding.

### 2. Feedback Is Fuel, Not Truth
Human and AI feedback use the **same core method** — only preprocessing differs. Normalize all feedback to a shared representation (verdict + severity + issue tags + expected behavior) before processing. Never trust a single piece of feedback in isolation.

### 3. Only Recurring Patterns Get Rules
Require 2+ occurrences of a pattern before generating a rule. One-off issues are noise, not signal. The meta-prompt explicitly skips single-occurrence patterns.

### 4. Rules Are Additive, Never Destructive
Rules are appended to a `### LEARNED_RULES` section in the prompt. Never rewrite or remove existing prompt instructions. Rules augment the prompt — they don't replace it.

### 5. Multi-Judge Validation Is Mandatory
Never validate with a single judge model. Single-judge evaluation overestimates improvement by 40-60% (RES-205: single-judge showed +40%, multi-judge showed +6%). Always use 3+ diverse judge models for validation experiments.

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

Research-validated configuration (RES-205):

| Parameter | Default | Range | Notes |
|-----------|---------|-------|-------|
| Failures per batch (f) | 10 | 5-15 | f=10 is optimal; f=5 too few to find patterns |
| Positives per batch (p) | 0 | 0-5 | P=0 outperforms P=3-5 on focused domains |
| Iterations | 1 | 1-2 | 1 iteration gives best results; 2 for consistency. More causes prompt bloat |
| Occurrence threshold | 2+ | — | One-offs are skipped |
| Rules per iteration | 1-5 | — | Prioritized by frequency × severity |
| Total rule cap | 10 | — | Across all iterations |
| Validation judges | 3+ | 3-5 | Diverse models required; single-judge overestimates by 40-60% |

**Expected effect size:** +0.4 to +0.9 on a 5-point scale for focused domains. Set expectations accordingly.

**Model tier matters:**

| Model Tier | Recommendation |
|------------|----------------|
| Small (Claude Haiku, Gemini Flash) | Best candidate — +40% improvement observed |
| Mid-tier (GPT-4o-mini) | Good candidate — room to improve |
| Top-tier (GPT-4o, Claude Sonnet) | Skip — likely at ceiling (>4.5/5 baseline), -3% to -10% observed |

**Model-specific optimal configs:**

| Model Family | F | P | Iterations | Notes |
|-------------|---|---|------------|-------|
| Claude | 10 | 0 | 3 | Small models (Haiku) learn best |
| Gemini | 15 | 0 | 1 | Higher failure count, single iteration |
| GPT | 10-15 | 3-5 | 3 | Benefits from positive anchors unlike others |
| Other / unknown | 10 | 0 | 1 | Conservative defaults; not experimentally validated — monitor closely |

**Split model strategy (recommended):** Use a cheap model as the learner (the model being improved) and a powerful model as the generator (the model running the meta-prompt). RES-205 showed +20% win rate with split models vs +13% with same-model — the powerful generator produces better rules when analyzing a smaller model's failures.

## Steps

Follow these steps **in order**. Do NOT skip steps.

### Phase 1: Identify Target and Assess Feasibility

1. **Identify the target prompt/deployment:**
   - Use `search_entities` with `type: "prompts"` to find the target prompt
   - Use HTTP API to get full prompt details including current version
   - Document: system message, user template, model, parameters

2. **Assess feasibility — check domain and model tier:**
   - **Domain check:** Is this a focused task (email, code review, support, extraction) or a broad/general task (open chat, helpfulness)?
     - If broad/general → **stop**. Inform user that prompt learning is not effective for this domain type. Suggest `optimize-prompt` instead.
   - **Model check:** What is the baseline quality score?
     - If baseline > 4.5/5 → **warn** the user that top-tier models show ceiling effects and prompt learning may not yield improvement.
   - **Present assessment** to the user before proceeding.

3. **Collect feedback data:**
   - Use `list_traces` to pull traces with feedback for the target deployment
   - Collect at least 50 traces (more is better) from a meaningful time period
   - Separate into: negative feedback traces and positive feedback traces
   - Identify the feedback source: human (thumbs up/down, corrections, free-text) or AI evaluator scores
   - **Prefer freetext feedback** when available — RES-205 showed +26.7% improvement from freetext vs +6.7% from categorical feedback. If the user only has thumbs up/down, recommend enriching with freetext explanations.

4. **Verify sufficient data:**
   - Need at least f=10 failure traces to proceed
   - If insufficient, inform user and suggest collecting more feedback before proceeding

### Phase 2: Normalize Feedback (Normalize)

5. **Normalize feedback to shared representation:**

   All feedback — human or AI — gets normalized to this shape:
   ```json
   {
     "verdict": "fail" | "pass" | "borderline",
     "severity": 1-5,
     "issue_tags": ["<from taxonomy>"],
     "expected_behavior": "what should have happened"
   }
   ```

   **Severity mapping:**

   | Condition | Severity |
   |-----------|----------|
   | Score on 1-5 scale (5 best) | `severity = 6 - score` |
   | Score on 1-10 scale (10 best) | `severity = ceil((10 - score) / 2)` |
   | Policy/safety violation or hallucination | 5 |
   | Wrong answer or missed key requirement | 4 |
   | Partial/incomplete response | 3 |
   | Minor format/tone/verbosity issue | 2 |
   | Nitpick/stylistic preference | 1 |

   **For human feedback:**
   - Thumbs down → `{"verdict": "fail", "severity": 3, "issue_tags": [], "expected_behavior": ""}`
   - Free-text correction → extract issue tags and expected behavior from the text
   - Numerical rating (e.g., 1-5) → map to verdict (1-2: fail, 3: borderline, 4-5: pass), use severity mapping above

   **For AI evaluator feedback:**
   - Boolean false → `{"verdict": "fail", "severity": 3, "issue_tags": [], "expected_behavior": "<from evaluator explanation>"}`
   - Categorical/numerical → map to verdict based on scale, carry explanation through

   If raw feedback lacks explanations (e.g., bare thumbs-down), use the LLM to enrich: pass the input/output pair and ask for a brief failure analysis to populate `issue_tags` and `expected_behavior`.

6. **Sample the batch:**
   - Sample f=10 representative failures (diverse issue types, not all the same failure)
   - If more failures exist, prioritize diversity across issue types

### Phase 3: Generate Rules (Meta-Prompt)

7. **Build the meta-prompt** by filling in the template below with collected data:
   - `PROMPT_TYPE`: "agent" or "evaluator" based on target
   - `CURRENT_PROMPT`: full text of the current prompt version
   - `ITERATION`: current iteration number (starts at 1)
   - `FEEDBACK_SOURCE`: "human" or "ai_eval"
   - `FAILURE_EXAMPLES`: the f=10 sampled failures with normalized feedback

8. **Follow the meta-prompt process below** with the variables filled in from the collected data:

   ~~~
   You are a prompt engineer improving a prompt based on feedback from multiple examples.

   GOAL: Analyze a batch of feedback failures and produce minimal, high-impact rules that fix recurring failure patterns.

   INPUTS:
   1) PROMPT_TYPE: "agent" | "evaluator"
   2) CURRENT_PROMPT: The prompt to improve
   3) ITERATION: Current iteration number
   4) FEEDBACK_SOURCE: "human" | "ai_eval"
   5) FAILURE_EXAMPLES (10 samples with negative feedback):
      [{"user_input": "...", "model_output": "...", "feedback": <see shapes below>}, ...]

   FEEDBACK SHAPES:
   - Human categorical: "fail" | "pass" | "borderline"
   - Human numerical: 3 (just the number)
   - Human free text: "The response was too vague..."
   - AI eval boolean: {"value": true|false, "explanation": "..."}
   - AI eval categorical: {"value": "A"|"B"|"C", "explanation": "..."}
   - AI eval numerical: {"value": 6, "scale": "1-10", "explanation": "..."}
   - Enriched normalized: {"verdict": "fail", "severity": 4, "issue_tags": ["missing_requirement"], "expected_behavior": "..."}

   PROCESS:

   STEP 1 — ANALYZE FAILURE PATTERNS:
   Group failures by issue type. Identify recurring patterns (2+ occurrences).
   Issue taxonomy: accuracy, missing_requirement, policy, safety, formatting, verbosity, tone, tool_use, reasoning, hallucination.
   Output: {"patterns": [{"issue_tag": "...", "count": N, "severity": 1-5, "examples": [indices], "root_cause": "..."}], "one_off_issues": [...]}

   STEP 2 — GENERATE RULES (only for recurring patterns):
   Create 1-5 rules. Format: "If [TRIGGER], then [ACTION]."
   Prioritize by: frequency × severity.
   Skip: one-offs, patterns too vague to test.

   STEP 3 — FORMAT RULES_TO_APPEND:
   Text block for ### LEARNED_RULES section.

   STEP 4 — GENERATE REGRESSION TESTS:
   Create 5-10 test cases: 3-5 "should_now_pass" + 2-5 "should_still_pass".

   STEP 5 — ITERATION GUIDANCE:
   Recommend "stop" (default after iteration 1) or "continue" (only if major patterns remain unfixed).

   OUTPUT FORMAT:
   A) PATTERN_ANALYSIS — JSON with patterns and one_off_issues
   B) RULES — numbered list
   C) RULES_TO_APPEND — text block for the prompt
   D) REGRESSION_TESTS — JSON array of test cases
   E) ITERATION_GUIDANCE — {"recommendation": "continue"|"stop", "reason": "...", "remaining_issues": N}

   NOW PROCESS THE ACTUAL INPUT.
   ~~~

9. **Produce the structured output** (sections A through E) from the analysis above.

10. **Review the output** with the user:
    - Show the identified patterns and their frequency/severity
    - Show the generated rules
    - Present ITERATION_GUIDANCE recommendation

### Phase 4: Aggregate and Apply

11. **Aggregate rules** across iterations (if iteration > 1):
    - Merge new rules with existing `### LEARNED_RULES` section
    - Remove duplicates or conflicting rules
    - Enforce total rule cap of 10

12. **Apply rules to the prompt** — **ask user confirmation first:**
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

### Phase 5: Validate (Multi-Judge Experiment)

13. **Set up a multi-judge validation experiment:**
    - Use `create_experiment` to compare baseline (no rules) vs variant (with rules)
    - Use the same dataset for both runs (10-50 examples)
    - **Configure 3+ diverse judge models** (e.g., Gemini, GPT, Claude) — single-judge overestimates by 40-60%
    - Include evaluators that measure the targeted failure types
    - Include the regression tests from the meta-prompt output

14. **Run the experiment and analyze results:**
    - Use `list_experiment_runs` to monitor progress
    - Use `get_experiment_run` to fetch results
    - Compare across **all judges**:
      ```
      | Judge Model | Evaluator | Baseline | Variant | Delta |
      |-------------|-----------|----------|---------|-------|
      | [judge 1]   | [metric]  | X%       | Y%      | +Z%   |
      | [judge 2]   | [metric]  | X%       | Y%      | +Z%   |
      | [judge 3]   | [metric]  | X%       | Y%      | +Z%   |
      ```

15. **Decision framework:**
    - **Clear win** — majority of judges show improvement, no regression → Promote variant
    - **Mixed results** — judges disagree → Investigate, may be noise
    - **No improvement** — most judges show no change → Re-examine feedback, try different samples
    - **Regression** — any judge shows regression → Revert, rules may be too aggressive
    - **Single judge shows large gain but others don't** → Discard. This is the 40-60% overestimation pattern.

### Phase 6: Iterate (Usually Stop at 1)

16. **Check iteration guidance:**
    - **Default: stop after iteration 1.** Research shows 1 iteration gives best results; more iterations cause prompt bloat and diminishing returns.
    - If significant failure patterns remain AND iteration < 2:
      - Return to Phase 2 Step 6 with updated prompt (now including rules)
      - Use remaining unprocessed failures
      - Increment iteration counter
    - If meta-prompt recommends `"stop"` OR iteration = 2:
      - Present final summary to user
      - If validated, **ask user confirmation** to promote to production deployment

17. **Final summary:**
    ```
    ## Prompt Learning Summary
    - **Target:** [prompt/deployment name]
    - **Domain type:** [focused domain description]
    - **Iterations completed:** [N]
    - **Rules generated:** [N]
    - **Feedback source:** human | ai_eval
    - **Key patterns addressed:** [list]
    - **Multi-judge validation:** [pass/fail with per-judge metrics]
    - **Effect size:** [delta on 5-point scale]
    - **Status:** [promoted / pending promotion / reverted]
    ```

## Anti-Patterns

| Anti-Pattern | Why It's Wrong | What to Do Instead |
|---|---|---|
| Using on broad/general tasks | 0% significant improvement on helpfulness (RES-205) | Only use on focused domains (email, code review, support) |
| Validating with a single judge | Overestimates improvement by 40-60% | Always use 3+ diverse judge models |
| Acting on single-occurrence feedback | Noise, not signal | Require 2+ occurrences before generating rules |
| Rewriting the whole prompt with rules | Destroys existing instructions | Only append to `### LEARNED_RULES` section |
| Running more than 2 iterations | Prompt bloat, diminishing returns | Stop at 1 iteration (2 max) |
| Treating human and AI feedback differently in the pipeline | Research shows same method works for both | Normalize to shared representation, then process identically |
| Deploying rules without multi-judge validation | No reliable evidence the rules actually help | Always validate with 3+ judges before promoting |
| Applying to top-tier models at ceiling | Models scoring >4.5/5 show no improvement | Check baseline score first; skip if already high |
| Adding reference comparisons to the meta-prompt | CriSPO-style references made results worse (-21% vs -14%) | Use failure-only analysis without reference comparison |

## Open in orq.ai

After completing this skill, direct the user to the relevant platform page:

- **View/edit the prompt:** `https://my.orq.ai/prompts` — review the prompt with `### LEARNED_RULES` section
- **Check traces with feedback:** `https://my.orq.ai/traces` — inspect traces that provided the feedback signal
- **View experiment results:** `https://my.orq.ai/experiments` — review the multi-judge validation experiment
- **Feedback overview:** `https://my.orq.ai/feedback` — monitor ongoing feedback collection
