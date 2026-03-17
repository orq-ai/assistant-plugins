---
name: build-evaluator
description: Create validated LLM-as-a-Judge evaluators following best practices — binary Pass/Fail judges with TPR/TNR validation for measuring specific failure modes
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Build Evaluator

**Companion skills:**
- `run-experiment` — run experiments using the evaluators you build
- `analyze-trace-failures` — identify failure modes that evaluators should target
- `generate-synthetic-dataset` — generate test data for evaluator validation
- `optimize-prompt` — iterate on prompts based on evaluator results
- `build-agent` — create agents that evaluators assess
- `prompt-learning` — uses evaluator scores as AI feedback to generate prompt rules

Design and create production-grade LLM evaluators on the orq.ai platform, grounded in evaluation best practices.

## When to use

- User asks to create an LLM-as-a-Judge evaluator
- User wants to evaluate LLM outputs for subjective or nuanced quality criteria
- User needs to measure tone, persona consistency, faithfulness, helpfulness, or other hard-to-code qualities
- User wants to set up automated evaluation for an LLM pipeline
- User asks about eval best practices or judge prompt design

## When NOT to use

- Need to run an experiment? → `run-experiment`
- Need to identify failure modes first? → `analyze-trace-failures`
- Need to optimize a prompt? → `optimize-prompt`
- Need to generate test data? → `generate-synthetic-dataset`

## orq.ai Documentation

When building evaluators on the orq.ai platform, consult these docs pages as needed:
- **Evaluators overview:** https://docs.orq.ai/docs/evaluators/overview
- **Creating evaluators:** https://docs.orq.ai/docs/evaluators/creating
- **Evaluator library (pre-built):** https://docs.orq.ai/docs/evaluators/library
- **Evaluators API:** https://docs.orq.ai/docs/evaluators/api-usage
- **Human review for evaluators:** https://docs.orq.ai/docs/evaluators/human-review
- **Datasets overview:** https://docs.orq.ai/docs/datasets/overview
- **Traces (for error analysis):** https://docs.orq.ai/docs/observability/traces

### orq.ai LLM Evaluator Details
- orq.ai supports **LLM evaluators** with Boolean or Number output types
- Available template variables: `{{log.input}}`, `{{log.output}}`, `{{log.messages}}`, `{{log.retrievals}}`, `{{log.reference}}`
- Choose judge model from the Model Garden
- Evaluators can be used as **guardrails** on deployments (block responses below threshold)
- Also supports **Python evaluators** (Python 3.12, numpy, nltk, re, json) and **JSON schema evaluators** for code-based checks

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `create_llm_eval` | Create an LLM evaluator with your judge prompt |
| `create_python_eval` | Create a Python evaluator for code-based checks |
| `list_models` | List available judge models |

**HTTP API fallback** (for operations not yet in MCP):

```bash
# List existing evaluators
curl -s https://my.orq.ai/v2/evaluators \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get evaluator details
curl -s https://my.orq.ai/v2/evaluators/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Test-invoke an evaluator against a sample output
curl -s https://my.orq.ai/v2/evaluators/<ID>/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"output": "The LLM output to evaluate", "query": "The original input", "reference": "Expected answer"}' | jq
```

## Core Principles

Before building anything, internalize these non-negotiable best practices:

### 1. Binary Pass/Fail over Likert Scales
- **ALWAYS default to binary (Pass/Fail) judgments**, not numeric scores (1-5, 1-10)
- Likert scales introduce subjectivity, middle-value defaulting, and require larger sample sizes
- If multiple quality dimensions exist, create **separate binary evaluators per dimension**
- Exception: only use finer scales when explicitly justified and you provide detailed rubric examples for every point

### 2. One Evaluator per Failure Mode
- **NEVER bundle multiple criteria into a single judge prompt**
- Each evaluator targets ONE specific, well-scoped failure mode
- Example: instead of "is this response good?", ask "does this response maintain the cowboy persona? (Pass/Fail)"

### 3. Fix Specification Before Measuring Generalization
- If the LLM fails because instructions were ambiguous, fix the prompt first
- Only build evaluators for **generalization failures** (LLM had clear instructions but still failed)
- Do NOT build evaluators for every failure mode -- prefer code-based checks (regex, assertions) when possible

### 4. Prefer Code-Based Checks When Possible
Cost hierarchy (cheapest to most expensive):
1. Simple assertions and regex checks
2. Reference-based checks (comparing against known correct answers)
3. LLM-as-Judge evaluators (most expensive -- use only when 1 and 2 cannot capture the criterion)

### 5. Require Validation Against Human Labels
- A judge without measured TPR/TNR is unvalidated and unreliable
- Need **100+ labeled examples** minimum, split into train/dev/test
- Measure True Positive Rate and True Negative Rate on held-out test set
- Use prevalence correction to estimate true success rates from imperfect judges

## Steps

Follow these steps **in order**. Do NOT skip steps.

### Phase 1: Understand the Evaluation Need

1. **Ask the user** what they want to evaluate. Clarify:
   - What is the LLM pipeline / application being evaluated?
   - What does "good" vs "bad" output look like?
   - Are there existing failure modes identified through error analysis?
   - Is there labeled data available (human-annotated Pass/Fail examples)?

2. **Determine if LLM-as-Judge is the right approach.** Challenge the user:
   - Can this be checked with code (regex, JSON schema validation, execution tests)?
   - Is this a specification failure (fix the prompt) or a generalization failure (needs eval)?
   - If code-based checks suffice, recommend those instead and stop here.

### Phase 2: Define Failure Modes and Criteria

3. **If the user has NOT done error analysis**, guide them through it:
   - Collect or generate ~100 diverse traces
   - Use structured synthetic data generation: define dimensions, create tuples, convert to natural language
   - Read traces and apply open coding (freeform notes on what went wrong)
   - Apply axial coding (group into structured, non-overlapping failure modes)
   - For each failure mode, decide: code-based check or LLM-as-Judge?

4. **For each failure mode that needs LLM-as-Judge**, define:
   - A clear, one-sentence criterion description
   - A precise Pass definition (what "good" looks like)
   - A precise Fail definition (what "bad" looks like)
   - 2-4 few-shot examples (clear Pass and clear Fail cases)

### Phase 3: Build the Judge Prompt

5. **Write the judge prompt** following this exact 4-component structure:

```
You are an expert evaluator assessing outputs from [SYSTEM DESCRIPTION].

## Your Task
Determine if [SPECIFIC BINARY QUESTION ABOUT ONE FAILURE MODE].

## Evaluation Criterion: [CRITERION NAME]

### Definition of Pass/Fail
- **Fail**: [PRECISE DESCRIPTION of when the failure mode IS present]
- **Pass**: [PRECISE DESCRIPTION of when the failure mode is NOT present]

[OPTIONAL: Additional context, persona descriptions, domain knowledge]

## Output Format
Return your evaluation as a JSON object with exactly two keys:
1. "reasoning": A brief explanation (1-2 sentences) for your decision.
2. "answer": Either "Pass" or "Fail".

## Examples

### Example 1:
**Input**: [example input]
**Output**: [example LLM output]
**Evaluation**: {"reasoning": "[explanation]", "answer": "Fail"}

### Example 2:
**Input**: [example input]
**Output**: [example LLM output]
**Evaluation**: {"reasoning": "[explanation]", "answer": "Pass"}

[2-6 more examples, drawn from labeled training set]

## Now evaluate the following:
**Input**: {{input}}
**Output**: {{output}}
[OPTIONAL: **Reference**: {{reference}}]

Your JSON Evaluation:
```

6. **Select the judge model**: Start with the most capable model available (e.g., gpt-4.1, claude-sonnet-4-5-20250514) to establish strong alignment. Optimize for cost later.

### Phase 4: Collect Human Labels

7. **Ensure you have labeled data** for validation. You need:
   - **100+ traces** with binary human Pass/Fail labels per criterion
   - Balanced: roughly **50 Pass and 50 Fail**
   - Labeled by **domain experts** (not outsourced, not LLM-generated)

8. **If labels are insufficient, set up human labeling:**

   **Using orq.ai Annotation Queues (recommended):**
   - Create an annotation queue for the target criterion in the orq.ai platform
   - Configure it to show: input, output, and any relevant context (retrievals, reference)
   - Assign domain experts as reviewers
   - Use binary Pass/Fail labels only (no scales)
   - See: https://docs.orq.ai/docs/administer/annotation-queue

   **Using orq.ai Human Review:**
   - Attach human review directly to individual spans in traces
   - Reviewers see full trace context (not just input/output summaries)
   - See: https://docs.orq.ai/docs/evaluators/human-review

   **Labeling guidelines for reviewers:**
   - Provide the exact Pass/Fail definition from the evaluator criterion
   - Include 3-5 example traces with correct labels as calibration
   - If uncertain, label as "Defer" and have a second expert review
   - Track inter-annotator agreement if multiple labelers (aim for >85%)

### Phase 5: Validate the Evaluator (TPR/TNR)

9. **Split labeled data into three disjoint sets**:
   - **Training set (10-20%)**: Source of few-shot examples for the prompt. Clear-cut cases.
   - **Dev set (40-45%)**: Used during prompt refinement. NEVER appears in the prompt itself.
   - **Test set (40-45%)**: Held out until the prompt is finalized. Gives unbiased TPR/TNR estimate.
   - Target: at least **30-50 Pass and 30-50 Fail** in dev and test each.
   - Critical: NEVER include dev/test examples as few-shot examples in the prompt.

10. **Refinement loop** (repeat until TPR and TNR > 90% on dev set):
    a. Run the evaluator over all dev examples
    b. Compare each judgment to human ground truth
    c. Compute TPR = (true passes correctly identified) / (total actual passes)
    d. Compute TNR = (true fails correctly identified) / (total actual fails)
    e. Inspect disagreements (false passes and false fails)
    f. Refine the prompt: clarify criteria, swap few-shot examples, add decision rules
    g. Re-run and measure again

11. **If alignment stalls**:
    - Use a more capable judge model
    - Decompose the criterion into smaller, more atomic checks
    - Add more diverse examples, especially edge cases
    - Review and potentially correct human labels (labeling errors happen)

12. **After finalizing the prompt**, run it ONCE on the held-out test set:
    - Compute final TPR and TNR — these are the official accuracy numbers
    - If TPR + TNR - 1 <= 0, the judge is no better than random; go back to step 10
    - Apply prevalence correction for production: `theta_hat = (p_observed + TNR - 1) / (TPR + TNR - 1)`

### Phase 6: Create the Evaluator on orq.ai

13. **Create the evaluator** using the orq MCP tools:
    - Use `create_llm_eval` with the refined judge prompt
    - Set appropriate model (start capable, optimize later)
    - Map variables: `{{log.input}}`, `{{log.output}}`, `{{log.reference}}` as needed
    - Link to relevant dataset and experiment

14. **Document the evaluator**:
    - Criterion name and description
    - Pass/Fail definitions
    - Judge model used
    - TPR and TNR on test set (with number of examples)
    - Known limitations or edge cases

### Phase 7: Ongoing Maintenance

15. **Set up maintenance cadence**:
    - Re-run validation after significant pipeline changes
    - Continue labeling new traces from production via orq.ai Annotation Queues
    - Recompute TPR/TNR regularly; check whether confidence intervals remain tight
    - When new failure modes emerge, create new evaluators (do not expand existing ones)

## Anti-Patterns to Actively Prevent

When building evaluators, STOP the user if they attempt any of these:

| Anti-Pattern | Why It's Wrong | What to Do Instead |
|---|---|---|
| Using 1-10 or 1-5 scales | Introduces subjectivity, requires more data, hides uncertainty | Use binary Pass/Fail per criterion |
| Bundling multiple criteria in one judge | Ambiguous, hard to debug, low agreement | One evaluator per failure mode |
| Using generic metrics (helpfulness, coherence, BERTScore, ROUGE) | "Creates an illusion of confidence that is unjustified" | Build application-specific criteria from error analysis |
| Skipping judge validation | Judge may not reflect actual quality criteria | Measure TPR/TNR on held-out labeled test set |
| Using off-the-shelf eval tools uncritically | "All you get is you don't know what they actually do" | Build custom evaluators from observed failure modes |
| Building evaluators before fixing prompts | Many failures are specification failures, not generalization | Fix obvious prompt gaps first |
| Using dev set accuracy as official metric | Overfitting during iterative refinement | Report accuracy ONLY from held-out test set |
| Having judge see its own few-shot examples in eval | Data contamination inflates metrics | Strict train/dev/test separation |

## Reference: Judge Prompt Quality Checklist

Before finalizing any judge prompt, verify:

- [ ] Targets exactly ONE failure mode (not multiple)
- [ ] Output is binary Pass/Fail (not a scale)
- [ ] Has clear, precise Pass definition
- [ ] Has clear, precise Fail definition
- [ ] Includes 2-8 few-shot examples from the training split
- [ ] Examples include both clear Pass and clear Fail cases
- [ ] Requests structured JSON output with "reasoning" and "answer" fields
- [ ] Reasoning comes BEFORE the answer (chain-of-thought)
- [ ] No dev/test examples appear in the prompt
- [ ] Has been validated: TPR and TNR measured on held-out test set
- [ ] Uses a capable model (gpt-4.1 class or better)

## Reference: Prevalence Correction Formula

To estimate true success rate from an imperfect judge:

```
theta_hat = (p_observed + TNR - 1) / (TPR + TNR - 1)    [clipped to 0-1]
```

Where:
- `p_observed` = fraction judged as "Pass" on new unlabeled data
- `TPR` = judge's true positive rate (from test set)
- `TNR` = judge's true negative rate (from test set)

If `TPR + TNR - 1 <= 0`, the judge is no better than random.

## Reference: Structured Synthetic Data Generation

When the user lacks real traces for error analysis:

1. **Define 3+ dimensions** of variation (e.g., topic, difficulty, edge case type)
2. **Generate tuples** of dimension combinations (20 by hand, then scale with LLM)
3. **Convert tuples to natural language** in a SEPARATE LLM call
4. **Human review** at each stage

This two-step process produces more diverse data than asking an LLM to "generate test cases" directly.

