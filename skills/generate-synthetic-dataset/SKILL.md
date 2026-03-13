---
name: generate-synthetic-dataset
description: Generate structured, diverse evaluation datasets — from scratch using dimensions-tuples methodology, from a description, or by expanding existing datasets
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Generate Synthetic Dataset

Generate high-quality, diverse evaluation datasets for LLM pipelines. Supports three modes: structured generation using dimensions-tuples-natural language methodology for maximum control, quick generation from a description, or expanding an existing dataset with more diverse examples.

**Companion skills:**
- `curate-dataset` — clean, deduplicate, and validate generated datasets
- `run-experiment` — run experiments against the generated dataset
- `build-evaluator` — design evaluators to score outputs against the dataset

## When to use

- User needs an evaluation dataset but has no production data yet
- User wants to expand an existing dataset with more diverse test cases
- User needs adversarial, edge-case, or stress-test data
- User asks to generate test cases, eval data, or benchmarks
- User wants to create a CI golden dataset for regression testing
- User has a few examples and wants to scale up

## orq.ai Documentation

Consult these docs when working with the orq.ai platform:
- **Datasets overview:** https://docs.orq.ai/docs/datasets/overview
- **Creating datasets:** https://docs.orq.ai/docs/datasets/creating
- **Datasets API:** https://docs.orq.ai/docs/datasets/api-usage

### orq.ai Dataset Structure
- Datasets contain three optional components: **Inputs** (prompt variables), **Messages** (system/user/assistant), and **Expected Outputs** (references for evaluator comparison)
- You don't need all three — use what you need for your eval type
- Datasets are project-scoped and reusable across experiments

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `list_models` | List available models for choosing generation models |
| `create_dataset` | Create a new evaluation dataset |
| `create_datapoints` | Add datapoints to a dataset |
| `search_entities` | Find existing datasets |

**HTTP API fallback** (for bulk operations or when MCP is insufficient):

```bash
# List datapoints in an existing dataset
curl -s "https://my.orq.ai/v2/datasets/<DATASET_ID>/datapoints" \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Bulk create datapoints (use for >50 datapoints — MCP is not ideal for large payloads)
curl -s -X POST "https://my.orq.ai/v2/datasets/<DATASET_ID>/datapoints" \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"datapoints": [...]}' | jq

# List datasets
curl -s https://my.orq.ai/v2/datasets \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

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

## Steps

Follow these steps **in order**. Do NOT skip steps.

Choose the appropriate mode based on the user's needs:
- **Mode 1 — Structured (dimensions-tuples-NL):** Maximum control and diversity. Best for targeted eval datasets, adversarial testing, CI golden datasets.
- **Mode 2 — Quick (from description):** Fast generation from a description. Best for first-pass eval data or when the user wants quick results.
- **Mode 3 — Expand existing:** Scale up a small existing dataset with more diverse examples.

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
   - Structure: `input` (user message), `reference` (expected behavior)
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
   - Each datapoint should have: `inputs` (with a `category` field describing the scenario + named variables) and optionally `expected_output`
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

After creating a dataset, maintain it over time:
- After experiments: add test cases for failure modes discovered
- After production monitoring: add real user queries that caused issues
- After prompt changes: add regression test cases for the specific change
- Remove or fix ambiguous test cases that evaluators score inconsistently
- Update references when the expected behavior changes
- Keep dataset balanced — don't let one dimension dominate

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

## Open in orq.ai

After completing this skill, direct the user to the relevant platform page:

- **View datasets:** `https://my.orq.ai/datasets` — review the generated or expanded dataset
- **Run experiments:** `https://my.orq.ai/experiments` — test your pipeline against the new dataset
