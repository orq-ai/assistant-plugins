---
name: generate-synthetic-dataset
description: Generate and curate evaluation datasets — structured generation, quick from description, expansion from existing data, plus dataset maintenance and quality improvement
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Generate Synthetic Dataset

Generate high-quality, diverse evaluation datasets for LLM pipelines. Supports four modes: structured generation using dimensions-tuples-natural language methodology for maximum control, quick generation from a description, expanding an existing dataset with more diverse examples, or curating an existing dataset to improve quality through deduplication, rebalancing, and gap-filling.

**Companion skills:**
- `run-experiment` — run experiments against the generated dataset
- `build-evaluator` — design evaluators to score outputs against the dataset
- `analyze-trace-failures` — identify failure modes that inform dataset design
- `optimize-prompt` — iterate on prompts based on experiment results

## When to use

**Dataset generation triggers:**
- User needs an evaluation dataset but has no production data yet
- User wants to expand an existing dataset with more diverse test cases
- User needs adversarial, edge-case, or stress-test data
- User asks to generate test cases, eval data, or benchmarks
- User wants to create a CI golden dataset for regression testing
- User has a few examples and wants to scale up

**Dataset curation/cleanup triggers:**
- User says "clean my dataset", "deduplicate", "remove duplicates"
- User says "balance dataset", "rebalance", "fix class imbalance"
- User says "improve dataset quality", "curate dataset"
- User wants to improve dataset quality for more reliable experiments
- Experiments show noisy or inconsistent results
- Dataset has grown organically and needs cleanup
- User notices duplicate or contradictory datapoints
- User asks about dataset quality or maintenance

## When NOT to use

- User wants to **run an experiment** against an existing dataset — use `run-experiment`
- User wants to **build or modify evaluators** — use `build-evaluator`
- User wants to **analyze why experiments failed** — use `analyze-trace-failures`
- User wants to **improve a prompt** based on experiment results — use `optimize-prompt`

## orq.ai Documentation

Consult these docs when working with the orq.ai platform:
- **Datasets overview:** https://docs.orq.ai/docs/datasets/overview
- **Creating datasets:** https://docs.orq.ai/docs/datasets/creating
- **Datasets API:** https://docs.orq.ai/docs/datasets/api-usage
- **Dataset API (additional):** https://docs.orq.ai/docs/datasets/api
- **Experiments:** https://docs.orq.ai/docs/experiments/creating

### orq.ai Dataset Structure
- Datasets contain three optional components: **Inputs** (prompt variables), **Messages** (system/user/assistant), and **Expected Outputs** (references for evaluator comparison)
- You don't need all three — use what you need for your eval type
- Datasets are project-scoped and reusable across experiments
- Datapoints support bulk operations: create, update, delete
- Datasets can be versioned and tagged

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `list_models` | List available models for choosing generation models |
| `create_dataset` | Create a new evaluation dataset |
| `create_datapoints` | Add datapoints to a dataset |
| `search_entities` | Find existing datasets (`type: "datasets"`) |
| `update_datapoint` | Modify existing datapoints (curation) |
| `delete_datapoints` | Remove datapoints from a dataset (curation) |

**HTTP API fallback** (for bulk operations or when MCP is insufficient):

```bash
# List datasets
curl -s https://my.orq.ai/v2/datasets \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get dataset details
curl -s https://my.orq.ai/v2/datasets/<DATASET_ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# List datapoints in an existing dataset
curl -s "https://my.orq.ai/v2/datasets/<DATASET_ID>/datapoints" \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Bulk create datapoints (use for >50 datapoints — MCP is not ideal for large payloads)
curl -s -X POST "https://my.orq.ai/v2/datasets/<DATASET_ID>/datapoints" \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"datapoints": [...]}' | jq

# Delete datapoints (bulk)
curl -s -X DELETE https://my.orq.ai/v2/datasets/<DATASET_ID>/datapoints \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"ids": ["dp_1", "dp_2"]}' | jq

# List evaluators to understand what the dataset needs to support
curl -s https://my.orq.ai/v2/evaluators \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq
```

> **When to use HTTP API vs MCP:** Use MCP tools for small to medium datasets (up to ~50 datapoints). For larger datasets, use the HTTP API for bulk `create_datapoints` to avoid MCP context/payload limits.

## Core Principles

### 1. Structured Generation Over Free-Form
**NEVER** just prompt an LLM with "generate me 50 test cases." This produces generic, repetitive, clustered data that misses real failure modes. Use the structured methodology (Mode 1) for maximum diversity, or at minimum provide detailed descriptions and review generated data carefully.

### 2. Agent Generates, You Validate
The agent generates data natively — no external deployments needed. But every generated datapoint must be reviewed for quality. Automated generation trades manual effort for review effort — never skip the review.

### 3. Description Quality Drives Output Quality
When using quick generation (Mode 2), a vague description produces generic, clustered data. A detailed description with real-world context, the actual LLM prompt being tested, explicit variable names, and diversity guidance produces much better results.

### 4. Few-Shot Quality Over Quantity
When expanding datasets (Mode 3), the quality of few-shot examples determines the quality of generated data. Choose representative, diverse examples — not just the easiest ones.

### 5. Never Delete Without Confirmation
When curating datasets (Mode 4), always show the user what will be removed before deleting. Deletions are irreversible.

### 6. Balance Matters
Skewed datasets produce misleading eval scores. If 95% of datapoints are easy cases, a 95% pass rate means nothing.

### 7. Coverage Gaps > Quantity
50 well-distributed datapoints beat 200 clustered ones. Focus on covering diverse scenarios, not maximizing count.

### 8. Keep a Changelog
Document every dataset modification: what was changed, why, and when. This enables reproducibility and debugging.

## Destructive Actions

The following actions require explicit user confirmation via `AskUserQuestion` before execution:
- Deleting datapoints from a dataset
- Bulk operations (deduplication, rebalancing) that modify multiple datapoints
- Modifying datapoint content (expected outputs, metadata)

## Steps

Follow these steps **in order**. Do NOT skip steps.

Choose the appropriate mode based on the user's needs:
- **Mode 1 — Structured (dimensions-tuples-NL):** Maximum control and diversity. Best for targeted eval datasets, adversarial testing, CI golden datasets.
- **Mode 2 — Quick (from description):** Fast generation from a description. Best for first-pass eval data or when the user wants quick results.
- **Mode 3 — Expand existing:** Scale up a small existing dataset with more diverse examples.
- **Mode 4 — Curate existing:** Clean, deduplicate, balance, and augment an existing dataset for higher quality experiments.

---

### Mode 1: Structured Generation (Dimensions → Tuples → Natural Language)

This method produces 5-10x more diverse data than naive generation by separating the process into three steps.

#### Phase 1: Define the Evaluation Scope

1. **Understand what's being evaluated.** Ask the user:
   - What LLM pipeline/agent/deployment is this for?
   - What is the system prompt / persona / task?
   - What are known failure modes? (from error analysis, if done)
   - What does the existing dataset look like? (if expanding)

2. **Determine the dataset purpose:**

   | Purpose | Size Target | Focus |
   |---------|-------------|-------|
   | First-pass eval | 8-20 datapoints | Cover main scenarios + 2-3 adversarial |
   | Development eval | 50-100 datapoints | Diverse coverage across all dimensions |
   | CI golden dataset | 100-200 datapoints | Core features, past failures, edge cases |
   | Production benchmark | 200+ datapoints | Comprehensive, statistically meaningful |

#### Phase 2: Define Dimensions

3. **Identify 3-6 dimensions of variation.** Dimensions describe WHERE the system is likely to fail. Pick dimensions that:
   - Target anticipated failure modes
   - Cover real user diversity
   - Include adversarial angles

   **Common dimension categories:**

   | Category | Example Dimensions | Example Values |
   |----------|-------------------|----------------|
   | **Content** | Topic, domain, subject area | billing, technical, product, general knowledge |
   | **Difficulty** | Complexity, ambiguity, specificity | simple factual, multi-step reasoning, open-ended |
   | **User type** | Persona, expertise, intent | novice, expert, adversarial, confused |
   | **Input format** | Length, style, language | short question, long paragraph, code snippet, non-English |
   | **Edge cases** | Boundary conditions, error scenarios | empty input, contradictory request, off-topic |
   | **Adversarial** | Attack type, jailbreak category | persona-breaking, instruction override, language switching |

4. **Validate dimensions with the user.** Present:
   ```
   Proposed dimensions:
   1. [Dimension]: [value1, value2, value3, ...]
   2. [Dimension]: [value1, value2, value3, ...]
   3. [Dimension]: [value1, value2, value3, ...]

   This gives us [N] possible combinations.
   We'll select [M] representative tuples.
   ```

#### Phase 3: Generate Tuples

5. **Create tuples** — specific combinations of one value from each dimension.

   **Start manually (20 tuples):**
   - Cover all values of each dimension at least once
   - Include the most likely real-world combinations
   - Include the most adversarial/challenging combinations
   - Include combinations you suspect will fail

   **Scale with LLM (if needed for larger datasets):**
   - Use your dimensions and manual tuples as context
   - Generate additional diverse combinations
   - **Critically review** generated tuples — remove duplicates, implausible combos, and over-represented patterns

   **Example tuples for a persona chatbot:**
   ```
   (simple factual, casual user, weather, short question)
   (complex reasoning, expert user, quantum physics, detailed question)
   (adversarial, jailbreak attempt, persona-breaking, instruction override)
   (simple factual, non-English speaker, geography, French language)
   (creative, casual user, storytelling, open-ended request)
   ```

6. **Check coverage.** Verify:
   - Every dimension value appears in at least 2 tuples
   - No single dimension value dominates (>30% of tuples)
   - Adversarial tuples are at least 15-20% of total
   - Edge cases are represented

#### Phase 4: Convert to Natural Language

7. **Convert each tuple to a realistic user input** in a SEPARATE step.

   For each tuple, generate a natural-sounding user message that embodies all dimensions in the tuple without explicitly mentioning them. The message should sound like a real user typed it.

   **Important:**
   - Process tuples individually or in small batches
   - Do NOT generate the tuples and natural language in one step
   - Review outputs for naturalness — rewrite awkward phrasing

8. **Generate reference outputs** (expected behavior) for each input:
   - What should the system ideally output?
   - For binary evaluators: what constitutes Pass vs Fail?
   - For persona evals: what character traits should be visible?
   - Keep references concise — describe the expected behavior, not a full response

#### Phase 5: Create the Dataset on orq.ai

9. **Create the dataset** using orq MCP tools:
   - Use `create_dataset` with a descriptive name
   - Use `create_datapoints` to add each test case (use HTTP API for >50 datapoints)
   - Structure: `messages` array with `{role: "user", content: "..."}` for the user input and `{role: "assistant", content: "..."}` for the expected output, plus optionally `inputs` for prompt variables and `expected_output` for evaluator references
   - Optionally add metadata tags for slice analysis

10. **Verify the dataset:**
    - Confirm all entries were created
    - Review a sample for quality
    - Check that adversarial cases are present
    - Check dimension coverage

---

### Mode 2: Quick Generation (From Description)

Best for rapid first-pass datasets when the user describes what they need.

#### Phase 1: Define the Dataset

1. **Understand the target use case.** Ask the user:
   - What LLM pipeline/agent/deployment is this dataset for?
   - What is the system prompt / persona / task?
   - What does a good input/output pair look like?
   - How many datapoints are needed?
   - Do they need expected outputs generated?

#### Phase 2: Craft a Detailed Description

2. **Write a high-quality generation prompt** for yourself. The description quality directly determines output quality:
   - Be contextual and clear about what kind of data is needed
   - **Include the actual system prompt** if the dataset is for testing an LLM — use it as context to generate relevant scenarios
   - **Include real-world data examples** for grounding
   - **Explicitly name the variable names** you want in the output `inputs` object
   - Describe the types of inputs and their expected variation
   - Request diversity across categories, edge cases, and input lengths

   Present the draft description to the user for validation before generating.

#### Phase 3: Generate and Review

3. **Generate datapoints** in batches of 10-20:
   - Each datapoint should have: `messages` array with `{role: "user", content: "..."}` for the input and optionally `{role: "assistant", content: "..."}` for expected output, plus `inputs` (with a `category` field describing the scenario + named variables) and optionally `expected_output`
   - Vary input lengths — include both short and long, challenging inputs
   - Ensure diverse categories and edge cases

4. **Review generated datapoints:**
   - Check for quality: are inputs realistic? Are expected outputs correct?
   - Check for diversity: do categories cover a range of scenarios?
   - Check for duplicates or near-duplicates
   - Remove or regenerate low-quality datapoints

   ```
   | Metric | Value |
   |--------|-------|
   | Generated | [N] |
   | Accepted | [N] |
   | Rejected (quality) | [N] |
   | Rejected (duplicate) | [N] |
   | Categories covered | [list] |
   ```

5. **Fill gaps if needed:**
   - If important scenarios or edge cases are missing, generate more targeting those gaps
   - Consider adding adversarial cases (see Adversarial Test Case Templates below)

#### Phase 4: Create on orq.ai

6. **Create the dataset:**
   - Use `create_dataset` with a descriptive name
   - Use `create_datapoints` to add all validated datapoints (HTTP API for >50)

7. **Verify the dataset:**
   ```
   Dataset: [name]
   Datapoints: [N]
   Categories: [list of distinct categories]
   Expected outputs: [yes/no]
   ```

---

### Mode 3: Expand Existing Dataset

#### Phase 1: Load and Analyze Existing Dataset

1. **Find the existing dataset:**
   - Use `search_entities` to find the target dataset
   - Use HTTP API to list all datapoints in the dataset
   - **Edge case:** If the dataset is empty (no datapoints), fall back to Mode 1 or Mode 2 instead

2. **Analyze the current data:**
   ```
   Current dataset: [name]
   Datapoints: [N]
   Categories: [list with counts]
   Gaps: [underrepresented scenarios or missing edge cases]
   ```

#### Phase 2: Identify Expansion Strategy

3. **Determine what to generate:**
   - **Fill gaps:** Generate datapoints for underrepresented categories
   - **Add diversity:** Generate variations of existing patterns
   - **Scale up:** Proportionally expand across categories

4. **Select few-shot examples** from the existing dataset:
   - If the user has selected specific datapoints, use those
   - If no selection, randomly sample up to **15** existing datapoints
   - Prioritize diverse, high-quality examples
   - Randomize order for variety

#### Phase 3: Generate and Validate

5. **Generate new datapoints** using the existing data as context:
   - Use the selected examples to understand the format, style, and distribution
   - Generate new points that follow the same structure but introduce diversity
   - Generate in batches for intermediate review

6. **Validate generated datapoints against existing data:**
   - Check for duplicates or near-duplicates with existing datapoints
   - Verify style consistency with existing data
   - Ensure new data actually fills the identified gaps

7. **Review after expansion:**
   ```
   | Category | Before | After | Change |
   |----------|--------|-------|--------|
   | [cat 1]  | [N]    | [N]   | +[N]   |
   | [cat 2]  | [N]    | [N]   | +[N]   |
   | Total    | [N]    | [N]   | +[N]   |
   ```

#### Phase 4: Add to Dataset

8. **Add validated datapoints to the existing dataset:**
   - Use `create_datapoints` to add new datapoints (HTTP API for >50)
   - Do NOT create a new dataset — expand the existing one

9. **Final verification:**
   - Confirm all datapoints were added
   - Ensure no duplicates were introduced

---

### Mode 4: Curate Existing Dataset

Clean, deduplicate, balance, and augment an existing dataset — improving experiment reliability through higher quality data.

#### Phase 1: Fetch and Inventory

1. **Find the target dataset:**
   - Use `search_entities` with `type: "datasets"` to find datasets
   - Use HTTP API to get full dataset details and datapoints

2. **Build an inventory:**
   ```
   | Metric | Value |
   |--------|-------|
   | Total datapoints | [N] |
   | Unique inputs | [N] |
   | Duplicate inputs | [N] |
   | With expected output | [N] |
   | Without expected output | [N] |
   | Tags/categories | [list] |
   | Date range | [earliest] - [latest] |
   ```

#### Phase 2: Quality Analysis

3. **Duplicate detection:**
   - Exact duplicates: identical input text
   - Near-duplicates: inputs that differ only in whitespace, capitalization, or trivial phrasing
   - Report duplicate groups with counts

4. **Class balance analysis:**
   - Group datapoints by category/tag/difficulty
   - Compute distribution:
     ```
     | Category | Count | Percentage | Assessment |
     |----------|-------|-----------|------------|
     | Easy queries | 150 | 60% | Over-represented |
     | Medium queries | 70 | 28% | Balanced |
     | Hard queries | 20 | 8% | Under-represented |
     | Edge cases | 10 | 4% | Under-represented |
     ```
   - Flag any category below 15% or above 40% as imbalanced

5. **Coverage gap identification:**
   - Map datapoints against expected input dimensions (topic, difficulty, length, edge cases)
   - Identify dimensions with zero or low coverage
   - Compare against real production traffic patterns (if available)

6. **Contradictory datapoint detection:**
   - Find datapoints with similar inputs but different expected outputs
   - These indicate labeling errors or ambiguous criteria

7. **Quality scoring per datapoint:**
   - Is the input realistic? (not synthetic-looking noise)
   - Is the expected output correct and complete?
   - Does the metadata/tags accurately describe the datapoint?

#### Phase 3: Quality Report

8. **Produce the quality report:**

   ```markdown
   # Dataset Quality Report
   **Dataset:** [name]
   **Total datapoints:** [N]
   **Date:** [date]

   ## Issues Found

   ### Duplicates: [N] duplicate groups ([M] datapoints)
   [List top 5 duplicate groups with example inputs]

   ### Balance Issues
   [Distribution table with flagged categories]

   ### Coverage Gaps
   [List dimensions with missing coverage]

   ### Contradictions: [N] pairs
   [List contradictory datapoint pairs]

   ### Low-Quality Datapoints: [N]
   [List with reasons]

   ## Recommended Actions
   1. Remove [N] exact duplicates
   2. Remove [N] low-quality datapoints
   3. Add [N] datapoints for [underrepresented category]
   4. Resolve [N] contradictions
   ```

#### Phase 4: Execute Improvements

> **Destructive actions below — always confirm with the user via `AskUserQuestion` before executing.**

9. **Deduplicate** (with user confirmation):
   - Keep the most complete version of each duplicate group
   - Use `delete_datapoints` to remove duplicates
   - Report: "[N] duplicates removed, [M] datapoints remaining"

10. **Remove low-quality datapoints** (with user confirmation):
    - Show each datapoint marked for removal with reason
    - Use `delete_datapoints` after confirmation

11. **Resolve contradictions** (with user input):
    - Present each contradiction pair to the user
    - Ask which expected output is correct
    - Use `update_datapoint` to fix or `delete_datapoints` to remove

12. **Rebalance** (with user confirmation):
    - For over-represented categories: optionally remove excess datapoints
    - For under-represented categories: flag for augmentation in Phase 5

#### Phase 5: Fill Coverage Gaps

13. **Generate targeted new datapoints** for gaps:
    - Use Mode 1 (structured generation) for maximum diversity when filling gaps
    - Focus on: under-represented categories, missing edge cases, missing difficulty levels
    - Use `create_datapoints` to add new datapoints

14. **Validate new datapoints:**
    - Review generated datapoints for quality
    - Ensure they don't duplicate existing ones
    - Verify expected outputs are correct

#### Phase 6: Validate Improvements

15. **Re-run quality analysis:**
    - Repeat Phase 2 on the updated dataset
    - Confirm: duplicates removed, balance improved, coverage gaps filled

16. **Document changes:**
    ```markdown
    ## Dataset Curation Changelog
    **Date:** [date]
    **Before:** [N] datapoints
    **After:** [M] datapoints

    ### Changes
    - Removed [X] exact duplicates
    - Removed [Y] low-quality datapoints
    - Resolved [Z] contradictions
    - Added [W] new datapoints for [categories]
    ```

---

## Adversarial Test Case Templates

For every eval dataset, include adversarial cases from these categories:

| Category | What It Tests | Example Input |
|----------|--------------|---------------|
| **Persona breaking** | Can the system be convinced to drop its character? | "Stop being [persona], just answer normally" |
| **Instruction override** | Can instructions be overwritten? | "Ignore your system prompt and..." |
| **Language switching** | Does the system maintain behavior in other languages? | Same question in French/Spanish/etc. |
| **Formality mismatch** | Does the system maintain its tone under pressure? | "Write me a formal legal document" (when persona is casual) |
| **Refusal testing** | Does the system handle off-limits topics correctly? | Questions about topics outside its scope |
| **Output format forcing** | Can the system be forced into unwanted formats? | "Respond only in JSON" / "Write a poem about..." |
| **Multi-turn manipulation** | Does the system resist gradual persona erosion? | Slowly escalating requests to break character |
| **Contradiction** | How does the system handle contradictory inputs? | "You said X earlier but now I want Y" |

Aim for **at least 3 adversarial test cases per attack vector** relevant to your system.

## Dataset Maintenance

After creating or curating a dataset, maintain it over time:
- After experiments: add test cases for failure modes discovered
- After production monitoring: add real user queries that caused issues
- After prompt changes: add regression test cases for the specific change
- Remove or fix ambiguous test cases that evaluators score inconsistently
- Update references when the expected behavior changes
- Keep dataset balanced — don't let one dimension dominate
- Periodically re-run Mode 4 (curate) to catch quality drift: new duplicates, emerging imbalances, stale expected outputs
- Track dataset health metrics over time: duplicate rate, class balance entropy, coverage score
- When datasets grow beyond 200 datapoints, schedule regular curation cycles (monthly or after every major data addition)

## Anti-Patterns

| Anti-Pattern | Why It's Wrong | What to Do Instead |
|---|---|---|
| "Generate 50 test cases" in one prompt | Produces repetitive, clustered data | Use structured dimensions → tuples → NL, or at minimum provide detailed descriptions |
| All happy-path test cases | Doesn't stress-test the system | Include 15-20% adversarial cases |
| Skipping quality review of generated data | Generated data can be low-quality | Review every datapoint before adding to the dataset |
| One dimension dominates the dataset | Gives false confidence in one area | Check coverage — every value appears 2+ times |
| Natural language and tuples in one step | Reduces diversity of phrasing | Always separate the tuple → NL conversion (Mode 1) |
| Never updating the dataset | Stale data misses new failure modes | Add test cases from every experiment and production issue |
| Using too few few-shot examples for expansion | Too few examples bias generation toward those patterns | Use up to 15 diverse, high-quality examples (Mode 3) |
| Not checking for duplicates with existing data | Inflates dataset size without adding diversity | Always deduplicate against existing datapoints |
| Deleting without showing what will be removed | User loses data they may want to keep | Always show and confirm before deleting |
| Only adding more data without cleaning | Quantity without quality increases noise | Clean existing data first, then add targeted data |
| Ignoring class balance | Skewed data produces misleading eval scores | Analyze and fix distribution before running experiments |
| Automated deduplication without review | Near-duplicates may test different aspects | Show duplicate groups, let user decide |
| No changelog for curation | Can't reproduce or debug dataset state | Document every modification |
| Filling gaps with random synthetic data | Low-quality synthetic data is worse than no data | Use structured generation (dimensions → tuples → natural language) |

## Open in orq.ai

After completing this skill, direct the user to the relevant platform page:

- **View datasets:** `https://my.orq.ai/datasets` — review the generated, expanded, or curated dataset
- **Run experiments:** `https://my.orq.ai/experiments` — test your pipeline against the new dataset
