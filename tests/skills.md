# Skill Smoke Tests

Conversational tests for each skill. Trigger each with a simple scenario and verify the first phase responds correctly. **No pre-existing resources are modified.**

Requires `setup.md` to have run first (seed data for `run-experiment` test).

---

## `build-agent`

- Ask: "Build a simple FAQ agent for a pizza restaurant"
- Verify: asks clarifying questions about purpose, users, success criteria
- Verify: calls `list_models` when selecting model

## `build-evaluator`

- Ask: "Build an evaluator that checks if output is valid JSON"
- Verify: recommends code-based evaluator (`create_python_eval`), not LLM judge
- Ask: "Build an evaluator for tone and helpfulness"
- Verify: suggests splitting into separate evaluators (one per criterion)

## `generate-synthetic-dataset`

- Ask: "Generate 5 test cases for a customer support chatbot"
- Verify: proposes dimensions of variation OR generates diverse cases
- Verify: calls `create_dataset` + `create_datapoints` with `orq-skills-test-` prefix

## `optimize-prompt`

- Provide a simple prompt inline: "You are a helpful assistant. Answer questions."
- Verify: analyzes against the 11-dimension framework
- Verify: produces concrete suggestions

## `analyze-trace-failures`

- Ask: "Analyze recent trace failures"
- Verify: calls `list_traces`, attempts to read spans
- Verify: describes sampling strategy

## `run-experiment`

- Ask: "Run an experiment using orq-skills-test-dataset with orq-skills-test-eval-length"
- Verify: calls `create_experiment` with correct references

---

## Critical Files

- `skills/build-agent/SKILL.md`
- `skills/build-evaluator/SKILL.md`
- `skills/generate-synthetic-dataset/SKILL.md`
- `skills/optimize-prompt/SKILL.md`
- `skills/analyze-trace-failures/SKILL.md`
- `skills/run-experiment/SKILL.md`
