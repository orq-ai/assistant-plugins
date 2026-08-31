---
name: orq-build-evaluator
description: >
  Create validated LLM-as-a-Judge evaluators following best practices — binary
  Pass/Fail judges by default, plus numeric and categorical, all validated
  against human labels for measuring specific failure modes.
  Use when you need to automate quality checks, build guardrails, or measure
  a specific failure mode identified during trace analysis. Do NOT use when
  failures are fixable with prompt changes (use orq-improve-agent) or when failure
  modes are unknown (use orq-analyze-traces first).
allowed-tools: Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, mcp__orq-workspace__get_llm_eval, mcp__orq-workspace__get_python_eval, mcp__orq-workspace__search_entities, mcp__orq-workspace__search_docs
---

# Build Evaluator

> `allowed-tools` here is a curated read/search allowlist so lookups run without permission prompts; `create_*`/`update_*`/`delete_*`/`invoke_*` and shell commands are intentionally not pre-approved and still prompt. The `delete_*` tools are disabled entirely while this skill is active.

You are an **orq.ai evaluation designer**. Your job is to design and create production-grade LLM-as-a-Judge evaluators — binary Pass/Fail by default, numeric or categorical where the criterion needs it, always validated against human labels for measuring specific failure modes.

## Constraints

- **Prefer** binary Pass/Fail over Likert scales (1-5, 1-10) — binary is simpler to validate and requires fewer labels. Use numeric scales when the criterion genuinely needs granularity (e.g., fluency 0-1) and you can provide a detailed rubric. Use categorical when the criterion naturally classifies into 3+ distinct labels (e.g., tone: professional/casual/aggressive, language detection, failure-mode triage).
- **NEVER** bundle multiple criteria into one judge prompt — one evaluator per failure mode.
- **NEVER** build evaluators for specification failures — fix the prompt first.
- **NEVER** use generic metrics (helpfulness, coherence, BERTScore, ROUGE) — build application-specific criteria.
- **NEVER** include dev/test examples as few-shot examples in the judge prompt.
- **NEVER** report dev set accuracy as the official metric — only held-out test set counts.
- **ALWAYS** validate with 100+ human-labeled examples on a held-out test set (TPR/TNR for binary; per-label precision/recall for categorical — see Phase 5 step 10).
- **ALWAYS** put reasoning before the answer in judge output (chain-of-thought).
- **ALWAYS** start with the most capable judge model, optimize cost later.

**Why these constraints:** Scales require more labeled data and careful rubric design to be reliable. Bundled criteria produce uninterpretable scores. Unvalidated judges give false confidence — a judge without measured agreement against human labels is unreliable.

## Workflow Checklist

```
Evaluator Build Progress:
- [ ] Phase 1: Understand the evaluation need
- [ ] Phase 2: Define failure modes and criteria
- [ ] Phase 3: Build the judge prompt (4-component structure)
- [ ] Phase 4: Collect human labels (100+, balanced across the output type's classes)
- [ ] Phase 5: Validate (output type's metric > 90% on dev, then test)
- [ ] Phase 6: Create on orq.ai
- [ ] Phase 7: Set up ongoing maintenance
```

## Done When

- Judge prompt passes all items in the Judge Prompt Quality Checklist (Phase 6 reference)
- The output type's step-10 target is met, or a deliberate alternative is recorded, on the held-out test set (100+ labeled examples): TPR **and** TNR > 90% for binary; macro recall > 90% with no label under 80% for categorical; the pre-declared error/correlation target for numeric
- Evaluator created on orq.ai via `create_llm_eval` / `create_python_eval`, or via `POST /v2/evaluators` for categorical (no MCP tool)
- Evaluator documented: criterion, output type, label or pass/fail definitions, the metrics above, known limitations

**Companion skills:**
- `orq-run-experiment` — run experiments using the evaluators you build
- `orq-analyze-traces` — identify failure modes that evaluators should target
- `orq-generate-synthetic-dataset` — generate test data for evaluator validation
- `orq-improve-agent` — iterate on prompts based on evaluator results
- `orq-evaluator-alignment` — measure cross-model stability and judge-human agreement for an evaluator you already created (step 16)
- `orq-build-agent` — create agents that evaluators assess
- **orq-cli** — the same platform operations from a shell, for anything that must run again without an agent present (CI, cron, scripts, bulk): auth via `ORQ_API_KEY`, `--json` output. See its "MCP tools or the CLI?" table before choosing.


## When to use

- User asks to create an LLM-as-a-Judge evaluator
- User wants to evaluate LLM outputs for subjective or nuanced quality criteria
- User needs to measure tone, persona consistency, faithfulness, helpfulness, or other hard-to-code qualities
- User wants to set up automated evaluation for an LLM pipeline
- User asks about eval best practices or judge prompt design

## When NOT to use

- Need to run an experiment? → `orq-run-experiment`
- Need to identify failure modes first? → `orq-analyze-traces`
- Need to optimize a prompt? → `orq-improve-agent`
- Need to generate test data? → `orq-generate-synthetic-dataset`

## orq.ai Documentation

> **Official documentation:** [Evaluators API — Programmatic Evaluation Setup](https://docs.orq.ai/docs/evaluators/api-usage#evaluators-api-programmatic-evaluation-setup)

[Evaluators](https://docs.orq.ai/docs/evaluators/overview) · [Creating Evaluators](https://docs.orq.ai/docs/evaluators/creating) · [Evaluator Library](https://docs.orq.ai/docs/evaluators/library) · [Evaluators API](https://docs.orq.ai/docs/evaluators/api-usage) · [Human Review](https://docs.orq.ai/docs/evaluators/human-review) · [Datasets](https://docs.orq.ai/docs/datasets/overview) · [Traces](https://docs.orq.ai/docs/observability/traces)

### Output Types

| Type | Returns | When to Use |
|------|---------|-------------|
| `boolean` | `true` / `false` | Default. Binary Pass/Fail for a single criterion. |
| `number` | numeric score | Continuous metrics (e.g., relevance 0-1). Requires detailed rubric. |
| `categorical` | one of N labels | Classification into 3+ distinct categories (e.g., tone, failure-mode triage). Requires `categorical_labels`. |

**Categorical evaluators must be created via HTTP or the CLI, not MCP.** `categorical_labels` is required at creation — the API rejects a categorical evaluator without it (`ZodError: categorical_labels is required when output_type is "categorical"`), so the MCP `create_llm_eval` tool, which exposes no label field, cannot create one at all. There is no create-then-patch path. Use the verified `POST /v2/evaluators` body in `resources/api-reference.md`.

### Evaluator Variables (LLM Evaluators)

LLM evaluator prompts use `{{double_braces}}` template variables. As of v4.14+ these are namespaced `{{input.*}}` / `{{output.*}}`; the legacy `{{log.*}}` names still resolve but are deprecated. **`resources/judge-prompt-template.md` §6 is the canonical list** — read it there rather than copying names from memory, and add new variables only in that file.

### Python Evaluator `log` Dict

Python evaluators receive a single `log` dict argument. Every key is always present (lists are empty when unused; `reference`/`expected_output` can be `None`):

| Key | Type | Content |
|-----|------|---------|
| `log["input"]` | `str` | The last user message |
| `log["output"]` | `str` | The model's generated response. Guard for empty anyway — a failed generation should not score Pass by default. |
| `log["reference"]` | `str \| None` | Reference/expected answer |
| `log["expected_output"]` | `str \| None` | Same value as `reference` |
| `log["messages"]` | `list[dict]` | The conversation **before** the graded turn (it excludes that turn); each entry has `role` and `content` |
| `log["retrievals"]` | `list[str]` | Knowledge Base retrieval chunks |
| `log["tool_calls"]` | `list[dict]` | Tool invocations; each has `tool_name`, `tool_arguments`, `tool_id`, `tool_run_id`, `tool_type`, `response["raw_response"]` |

Python evals return `bool` (for boolean output type) or a numeric value (for number). Code limit: 1 MB (1,048,576 bytes); larger code returns `Code exceeds maximum size` and does not run. Third-party libraries the [docs](https://docs.orq.ai/docs/ai-studio/optimize/evaluators) name as preloaded: `numpy` (v1.26.4), and since v4.10 `requests` (HTTP calls) and `pydantic` (JSON-schema validation) — the latter two are the replacement for the retired HTTP and JSON evaluator types. Anything else is unverified; test it in the Playground before relying on it.

> The last `def` in the code editor is the entry-point. You can define helper functions above it.

- Choose judge model from the Model Garden
- Evaluators can be used as **guardrails** on deployments (block responses below threshold)
- Also supports **function** (`type: "function_eval"` with `function_params.type` — `exact_match`, `contains_any`, `contains_none`, `is_valid_json`, `bert_score`, `bleu_score`, `cosine_similarity`) and **RAGAS** (`type: "ragas"`) evaluator types, both outside this skill's scope; see orq docs and `orq-cli`

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `create_llm_eval` | Create an LLM evaluator with your judge prompt |
| `create_python_eval` | Create a Python evaluator for code-based checks |
| `get_llm_eval` / `get_python_eval` | Retrieve an evaluator by ID |
| `list_models` | List available judge models |
| *(HTTP API)* | Anything MCP omits — categorical creation, `model_parameters`, `jury` mode, listing, invoke (`resources/api-reference.md`) |

### HTTP Fallback for MCP Gaps

MCP covers boolean and numeric LLM/Python evaluators only. Categorical creation, judge
tuning (`model_parameters`, `jury`, `repetitions`), listing and invoke have no MCP tool and
go over HTTP or the CLI. **Evaluator CRUD is v2 and invoke is v3** — not a typo to
normalise; `/v3/evaluators` 404s and there is no v2 invoke route.

`resources/api-reference.md` has the calls, the verified categorical `POST /v2/evaluators`
body, the CLI equivalents, and the `POST /v3/evaluators/{id}/invoke` payload with its
body-path-to-variable mapping. Read it there rather than reconstructing a payload from memory —
unknown fields are dropped silently and the judge scores a prompt with a hole in it.

## Core Principles

Before building anything, internalize these non-negotiable best practices:

### 1. Prefer Binary Pass/Fail
- **Default to binary (Pass/Fail) judgments** — they are simpler to validate and need fewer labels
- Numeric scales (1-5, 1-10) are valid when the criterion genuinely needs granularity, but require a detailed rubric with examples for every score point and more labeled data to validate
- If multiple quality dimensions exist, create **separate evaluators per dimension**

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
   - **Binary (default)**: a precise Pass definition (what "good" looks like) and a precise Fail definition (what "bad" looks like), plus 2-4 few-shot examples covering clear Pass and clear Fail cases
   - **Categorical**: a precise definition per label, written so the labels are mutually exclusive and jointly exhaustive — every output must fall in exactly one. Add an explicit catch-all label (`other`, `unclear`) rather than letting the judge guess. Provide 2-4 few-shot examples **per label**, not per criterion.
   - **Numeric**: a rubric entry for every score point, each with an example

### Phase 3: Build the Judge Prompt

5. **Write the judge prompt** following this exact 4-component structure. The structure below is the binary form; `resources/judge-prompt-template.md` carries the same template with the categorical variant marked inline (output-format line and per-label examples) — use that file directly when the evaluator is categorical.

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
**Input**: {{input.user_query}}
**Output**: {{output.response}}
[OPTIONAL: **Reference**: {{input.expected_output}}]
[OPTIONAL: **System Instructions**: {{input.system_instructions}}]
[OPTIONAL: **Tools Called**: {{output.tools_called}}]

Your JSON Evaluation:
```

6. **Select the judge model**: Start with the most capable model available (e.g., gpt-4.1, claude-sonnet-4-5-20250514) to establish strong alignment. Optimize for cost later.

### Phase 4: Collect Human Labels

7. **Ensure you have labeled data** for validation. You need:
   - **100+ traces** with human labels per criterion
   - Balanced: roughly **50 Pass and 50 Fail** for binary. For **categorical**, at least **30-40 examples per label** — a 4-label evaluator needs 120-160 traces, not 100, and a label the judge sees ten times cannot be validated. For **numeric**, cover the full range including both endpoints.
   - Labeled by **domain experts** (not outsourced, not LLM-generated)

8. **If labels are insufficient, set up human labeling:**

   **Using orq.ai Annotation Queues (recommended):**
   - Create an annotation queue for the target criterion in the orq.ai platform
   - Configure it to show: input, output, and any relevant context (retrievals, reference)
   - Assign domain experts as reviewers
   - Prefer binary Pass/Fail labels (scales need more calibration data)
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
   - Target: at least **30-50 Pass and 30-50 Fail** in dev and test each — for categorical, at least **15-20 examples of every label** in dev and test each.
   - Critical: NEVER include dev/test examples as few-shot examples in the prompt.

10. **Refinement loop** (repeat until the target metric holds on the dev set).
    Use `POST /v3/evaluators/{id}/invoke` (`resources/api-reference.md`) for rapid single-item checks during iteration, or run a full experiment for batch evaluation.
    a. Run the evaluator over all dev examples
    b. Compare each judgment to human ground truth
    c. Compute the metric for the output type (below)
    d. Inspect disagreements
    e. Refine the prompt: clarify criteria, swap few-shot examples, add decision rules
    f. Re-run and measure again

    **The target metric depends on the output type. TPR/TNR is a 2x2 measure and is undefined for 3+ labels — do not report it for a categorical evaluator.**

    | Output type | Compute | Target |
    |-------------|---------|--------|
    | `boolean` | TPR = true passes correctly identified / total actual passes; TNR = true fails correctly identified / total actual fails | TPR **and** TNR > 90% |
    | `categorical` | Full N x N confusion matrix, then per-label precision and recall. Report both **per label** and as a macro average (unweighted mean across labels, so a rare label cannot be hidden by a common one). | Macro recall > 90%, no single label below 80% recall |
    | `number` | Mean absolute error against human scores, plus Spearman correlation | Spearman > 0.7, MAE inside the tolerance the downstream decision can absorb |

    **These targets are starting points, not pass/fail gates.** 90% mirrors common practice, the
    per-label floor stops a rare label being averaged away, and the numeric pair is a convention —
    none is a measured threshold. Pick your own before you start, record it, and judge a miss on
    what the disagreements actually are rather than on the number.

    For categorical, the confusion matrix is the artifact to inspect, not the headline number: off-diagonal mass concentrated in one label pair means those two label definitions overlap and should be merged or sharpened.

11. **If alignment stalls**:
    - Use a more capable judge model
    - Decompose the criterion into smaller, more atomic checks
    - Add more diverse examples, especially edge cases
    - Review and potentially correct human labels (labeling errors happen)

12. **After finalizing the prompt**, run it ONCE on the held-out test set:
    - Compute the step-10 metric for your output type — these are the official accuracy numbers
    - **Binary**: if TPR + TNR - 1 <= 0, the judge is no better than random; go back to step 10. Apply prevalence correction for production: `theta_hat = (p_observed + TNR - 1) / (TPR + TNR - 1)`
    - **Categorical**: if macro recall is at or below 1/N (chance for N labels), the judge is no better than random; go back to step 10. Prevalence correction has no N-label analogue here — report raw per-label rates and say they are uncorrected.
    - **Numeric**: if Spearman correlation is not significantly above 0, go back to step 10

### Phase 6: Create the Evaluator on orq.ai

13. **Choose the evaluator type** based on the criterion:

    | Check Type | When to Use | MCP Tool |
    |------------|-------------|----------|
    | **Code-based** (regex, assertions, schema) | Deterministic checks: format validation, length limits, required fields, exact matches | `create_python_eval` |
    | **LLM-as-Judge** | Subjective/nuanced criteria that code can't capture: tone, faithfulness, persona consistency | `create_llm_eval` |

    **If code-based (`create_python_eval`):**
    - Write a Python function: `def evaluate(log) -> bool` (boolean) or `-> float` (number)
    - The `log` dict keys are documented in the "Python Evaluator `log` Dict" section above
    - Example (format validation):
      ```python
      import json

      def evaluate(log):
          try:
              parsed = json.loads(log["output"])
              return "reasoning" in parsed and "answer" in parsed
          except (json.JSONDecodeError, TypeError):
              return False
      ```
    - Example (tool-call check):
      ```python
      def evaluate(log):
          tool_calls = log["tool_calls"]
          if not tool_calls:
              return False
          return any(tc["tool_name"] == "search_knowledge_base" for tc in tool_calls)
      ```
    - Example (response-length check — one criterion, and an empty output fails rather than passing):
      ```python
      def evaluate(log):
          output = log["output"]
          if not output:
              return False
          return len(output) <= 500
      ```
    - Create using `create_python_eval` MCP tool with the Python code
    - Note: MCP `create_python_eval` covers `boolean`/`number` only — see `resources/api-reference.md` for the rest.

    **If LLM-as-Judge (`create_llm_eval`):**
    - Use `create_llm_eval` with the refined judge prompt from Phase 3-5
    - Set appropriate model (start capable, optimize later)
    - Use the current template variables (canonical list: `resources/judge-prompt-template.md` §6)
    - For **categorical** evaluators, do NOT use `create_llm_eval` — it cannot supply `categorical_labels`, which the API requires at creation. Use the `POST /v2/evaluators` body in `resources/api-reference.md`.

14. **Create the evaluator** on orq.ai:
    - Link to relevant dataset and experiment

15. **Document the evaluator**:
    - Criterion name and description
    - Evaluator type (Python or LLM) and output type (boolean, categorical, number)
    - Pass/Fail definitions, or the definition of every label
    - Judge model used (if LLM)
    - Test-set metrics from step 12, with the number of examples: TPR/TNR for binary, per-label plus macro precision/recall for categorical, error and correlation for numeric
    - Known limitations or edge cases

16. **Recommend evaluator alignment** (LLM-as-Judge only):
    After creating an LLM evaluator, suggest running `orq-evaluator-alignment` to measure cross-model stability and judge-human agreement on production traces. This validates the evaluator beyond the held-out test set and catches drift over time. Not applicable to Python/code-based evaluators.

### Phase 7: Ongoing Maintenance

17. **Set up maintenance cadence**:
    - Re-run validation after significant pipeline changes
    - Continue labeling new traces from production via orq.ai Annotation Queues
    - Recompute TPR/TNR regularly; check whether confidence intervals remain tight
    - When new failure modes emerge, create new evaluators (do not expand existing ones)

## Anti-Patterns to Actively Prevent

When building evaluators, STOP the user if they attempt any of these:

| Anti-Pattern | What to Do Instead |
|---|---|
| Using scales without a detailed rubric | Provide rubric examples for every score point, or simplify to binary Pass/Fail |
| Bundling multiple criteria in one judge | One evaluator per failure mode — bundled judges are ambiguous and hard to debug |
| Using generic metrics (helpfulness, coherence, BERTScore, ROUGE) | Build application-specific criteria from error analysis |
| Skipping judge validation | Measure TPR/TNR on held-out labeled test set (100+ examples) |
| Using off-the-shelf eval tools uncritically | Build custom evaluators from observed failure modes |
| Building evaluators before fixing prompts | Fix obvious prompt gaps first — many failures are specification failures |
| Using dev set accuracy as official metric | Report accuracy ONLY from held-out test set |
| Having judge see its own few-shot examples in eval | Strict train/dev/test separation — contamination inflates metrics |

## Reference: Judge Prompt Quality Checklist

Before finalizing any judge prompt, verify:

- [ ] Targets exactly ONE failure mode (not multiple)
- [ ] Output is binary Pass/Fail (preferred), or has a detailed rubric for every score point, or a precise definition for every categorical label
- [ ] Has clear, precise Pass definition (binary) or per-label definition (categorical)
- [ ] Has clear, precise Fail definition (binary); labels are mutually exclusive and jointly exhaustive, with a catch-all (categorical)
- [ ] Includes 2-8 few-shot examples from the training split — for categorical, examples covering every label
- [ ] Examples include both clear Pass and clear Fail cases (binary) or every label (categorical)
- [ ] Requests structured JSON output with "reasoning" and "answer" fields
- [ ] Reasoning comes BEFORE the answer (chain-of-thought)
- [ ] No dev/test examples appear in the prompt
- [ ] Has been validated: TPR and TNR measured on held-out test set
- [ ] Uses a capable model (gpt-4.1 class or better)

## Reference: Prevalence Correction Formula

**Binary evaluators only** — the formula is 2x2 algebra and has no categorical or numeric analogue.

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

## Documentation & Resolution

**Lookup order: [`doc-resolution.md`](../orq-shared/resources/doc-resolution.md).** Live queries first — for this skill that means `mcp__orq-workspace__create_llm_eval`, `create_python_eval`.

