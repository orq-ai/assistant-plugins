# Catalog: run-experiment

Tests for [`skills/run-experiment/SKILL.md`](../../../skills/run-experiment/SKILL.md).
Source scenarios: [`../../skills.md`](../../skills.md) (run-experiment) and
[`../../mcp-tools.md`](../../mcp-tools.md) (experiment tools).

> Requires the seeded `orq-skills-test-dataset` and `orq-skills-test-eval-length` from
> [`../../setup.md`](../../setup.md).

## Functional cases

### F1. Create an experiment against seeded dataset + evaluator
- **Operation:** `create_experiment` with key `orq-skills-test-experiment`, referencing the seeded
  dataset and the seeded length evaluator.
- **Verify:** the experiment is created and references resolve (no "dataset/evaluator not found").
  Confirms the experiment-creation path the skill drives works against the latest release.
- **Cleanup:** experiment — `delete_entity(type=experiment, id=…)`.

### F2. List experiment runs
- **Operation:** `list_experiment_runs`.
- **Verify:** returns an array including (or consistent with) the experiment from F1.
- **Cleanup:** none (read-only).

## Behavioural scenarios

### B1. Run with explicit references
- **Type:** explicit
- **Trigger:** "Run an experiment using orq-skills-test-dataset with orq-skills-test-eval-length"
- **Expected routing:** run-experiment
- **PASS:** calls `create_experiment` with the correct dataset + evaluator references; uses binary
  Pass/Fail criteria; does not invent a generic "helpfulness" evaluator; treats a 100% pass rate as a
  sign the eval is too easy (target ~70-85%).
- **Anti-patterns (FAIL):** runs without a dataset; bundles 5+ criteria into one evaluator; jumps to a
  model upgrade before prompt fixes.

### B2. Implicit — a need, not the artifact
- **Type:** implicit
- **Trigger:** "I changed my prompt and want to know if it's actually better than before on my test set."
- **Expected routing:** run-experiment
- **PASS:** routes here on the description; sets up an experiment over the dataset with binary
  evaluators comparing the two configs.
- **Anti-patterns (FAIL):** misroutes to optimize-prompt (which edits, not measures); runs without a
  dataset.

## Negative controls (must NOT fire run-experiment)

### N1. External framework agents → compare-agents
- **Type:** negative
- **Trigger:** "Compare my LangGraph agent against my orq.ai agent on the same questions."
- **Expected routing:** compare-agents — run-experiment must not fire (external framework involved,
  per the skill's own "Do NOT use for cross-framework comparisons").
- **PASS:** routes to compare-agents.
- **Fired = FAIL:** setting up a native orq.ai experiment for a cross-framework comparison.
