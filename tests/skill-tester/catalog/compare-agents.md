# Catalog: compare-agents

Tests for [`skills/compare-agents/SKILL.md`](../../../skills/compare-agents/SKILL.md).
Source scenarios: [`../../skills.md`](../../skills.md) (compare-agents).

## Functional cases

### F1. Generated evaluatorq script uses correct SDK calls
- **Operation:** have the skill generate (not run) an `evaluatorq` comparison script for two orq.ai
  agents.
- **Verify (static):** script imports `from orq_ai_sdk import Orq` (not `from orq import ...`); each
  `@job` uses `agents.responses.create()` (not `agents.invoke()`); both jobs share the **same**
  evaluator; dataset is loaded via `{ dataset_id: "..." }` (Python) / `{ datasetId: "..." }` (TS),
  not created inline.
- **Drift watch:** references to `orqkit` / `evaluatorq` should match the current package
  ([github.com/orq-ai/orqkit](https://github.com/orq-ai/orqkit)). Flag renamed/removed exports.
- **Cleanup:** none (no live run; external frameworks not executed).

### F2. Seeded agent is independently invokable
- **Operation:** invoke `orq-skills-test-echo` via the responses API (shared with invoke-deployment F1).
- **Verify:** returns a response — satisfies the constraint "confirm each agent can be invoked
  independently before running the full experiment."
- **Cleanup:** none.

## Behavioural scenarios

### B1. orq.ai vs orq.ai
- **Type:** explicit
- **Trigger:** "Compare my two orq.ai agents `agent-gpt4o` and `agent-claude` head-to-head"
- **Expected routing:** compare-agents
- **PASS:** identifies both agents via `search_entities(type=agent)`; generates an evaluatorq script
  with two `@job` functions, each using `agents.responses.create()`; both jobs use the same
  evaluator; imports `from orq_ai_sdk import Orq`.
- **Anti-patterns (FAIL):** uses `agents.invoke()`; `from orq import ...`; different evaluators per
  agent.

### B2. External vs orq.ai
- **Type:** explicit
- **Trigger:** "Compare my LangGraph agent against my orq.ai agent"
- **Expected routing:** compare-agents
- **PASS:** generates one LangGraph job pattern and one orq.ai job pattern; **delegates** dataset
  creation to `generate-synthetic-dataset` and evaluator creation to `build-evaluator` (does not
  design either inline).
- **Anti-patterns (FAIL):** creates the dataset inline in the script; designs an evaluator prompt
  from scratch.

### B3. Implicit — cross-framework benchmark, unnamed
- **Type:** implicit
- **Trigger:** "I have a CrewAI bot and an OpenAI Agents SDK bot doing the same task and want to know which is better on a shared test set."
- **Expected routing:** compare-agents
- **PASS:** routes here on the description; sets up a head-to-head evaluatorq experiment across the
  two frameworks with one shared evaluator and dataset.
- **Anti-patterns (FAIL):** misroutes to run-experiment; uses different evaluators per framework.

## Negative controls (must NOT fire compare-agents)

### N1. Single orq.ai config A/B → run-experiment
- **Type:** negative
- **Trigger:** "I want to A/B test two system prompts on the same orq.ai model against my dataset."
- **Expected routing:** run-experiment — compare-agents must not fire (no external agents; this is a
  config comparison, per the skill's own "Do NOT use" note).
- **PASS:** routes to run-experiment.
- **Fired = FAIL:** spinning up an evaluatorq cross-framework script for a single-config A/B.
