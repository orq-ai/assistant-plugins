# Catalog: generate-synthetic-dataset

Tests for [`skills/generate-synthetic-dataset/SKILL.md`](../../../skills/generate-synthetic-dataset/SKILL.md).
Source scenarios: [`../../skills.md`](../../skills.md) (generate-synthetic-dataset).

## Functional cases

### F1. Create dataset + datapoints, verify rows
- **Operation:** `create_dataset` → `orq-skills-test-crud-dataset`; `create_datapoints` with 3 rows
  (inputs + expected_output); `list_datapoints`.
- **Verify:** `list_datapoints` returns 3 rows with the inputs/expected_output supplied.
- **Cleanup:** dataset + datapoints — **auto-deletable** via `delete_dataset` (datapoints removed
  with the parent). Note the known `delete_dataset` output-validation bug — verify deletion by
  re-calling `list_datapoints`.

### F2. Update a datapoint
- **Operation:** `update_datapoint` on one row.
- **Verify:** the change is reflected by `list_datapoints`.
- **Cleanup:** same dataset as F1.

## Behavioural scenarios

### B1. 5 test cases for a support chatbot
- **Type:** explicit
- **Trigger:** "Generate 5 test cases for a customer support chatbot"
- **Expected routing:** generate-synthetic-dataset
- **PASS:** proposes **dimensions of variation** (and ideally the dimensions→tuples→natural-language
  process) rather than naive "generate 5"; produces diverse cases including some adversarial ones;
  creates them via `create_dataset` + `create_datapoints` using an `orq-skills-test-` prefix.
- **Anti-patterns (FAIL):** just prompts "generate 5 cases" with no dimensions; generates one clustered
  theme; generates tuples and natural language in a single step.

### B2. Implicit — a need, not the artifact
- **Type:** implicit
- **Trigger:** "I need a bunch of varied example conversations to stress-test my support bot before launch."
- **Expected routing:** generate-synthetic-dataset
- **PASS:** routes here on the description; designs dimensions and includes adversarial coverage;
  persists via `create_dataset` + `create_datapoints`.
- **Anti-patterns (FAIL):** misroutes to run-experiment; produces a clustered, low-diversity set.

## Negative controls (must NOT fire generate-synthetic-dataset)

### N1. Real production data already exists → analyze it
- **Type:** negative
- **Trigger:** "I already have 500 real production traces — help me understand the failure patterns."
- **Expected routing:** analyze-trace-failures — generate-synthetic-dataset must not fire (the skill's
  own "Do NOT use when sufficient real production data exists").
- **PASS:** routes to analyze-trace-failures.
- **Fired = FAIL:** generating synthetic data when ample real data is already available.
