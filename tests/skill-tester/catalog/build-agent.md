# Catalog: build-agent

Tests for [`skills/build-agent/SKILL.md`](../../../skills/build-agent/SKILL.md).
Source scenarios: [`../../skills.md`](../../skills.md) (build-agent) and
[`../../mcp-tools.md`](../../mcp-tools.md) (agent tools).

## Functional cases

### F1. Create + read back an agent
- **Operation:** `create_agent` with key `orq-skills-test-crud-agent`, model `openai/gpt-4.1-mini`,
  simple instructions; then `get_agent(key=orq-skills-test-crud-agent)`.
- **Verify:** read-back config matches what was created (model + instructions).
- **Cleanup:** agent — `delete_entity(type=agent, id=…)`. (Prefer reusing the seeded
  `orq-skills-test-echo` where a case allows, to avoid extra resources.)

### F2. Update an agent
- **Operation:** `update_agent(key=orq-skills-test-crud-agent)` changing instructions; read back.
- **Verify:** the update is reflected.
- **Cleanup:** same agent as F1.

### F3. Model selection is actionable
- **Operation:** `list_models(modelType=chat)`.
- **Verify:** non-empty; supports the constraint "start with the most capable model" and pinning a
  snapshot.
- **Cleanup:** none.

## Behavioural scenarios

### B1. Simple FAQ agent
- **Type:** explicit
- **Trigger:** "Build a simple FAQ agent for a pizza restaurant"
- **Expected routing:** build-agent
- **PASS:** asks clarifying questions about purpose, users, and success criteria before building;
  calls `list_models` when selecting a model; starts with a capable model.
- **Anti-patterns (FAIL):** jumps straight to creating the agent with no discovery; adds >8 tools;
  picks a model without consulting `list_models`.

### B2. Implicit — a need, not the artifact
- **Type:** implicit
- **Trigger:** "I want something that can automatically answer customer questions about our pizza menu and hours."
- **Expected routing:** build-agent
- **PASS:** routes here on the description; runs discovery (purpose/users/success) before building;
  consults `list_models`.
- **Anti-patterns (FAIL):** misroutes to invoke-deployment; builds with no discovery.

## Negative controls (must NOT fire build-agent)

### N1. Existing agent misbehaving → debug, not build
- **Type:** negative
- **Trigger:** "My support agent keeps giving wrong answers in production — help me figure out why."
- **Expected routing:** analyze-trace-failures — build-agent must not fire (debug an existing agent,
  not build a new one).
- **PASS:** routes to analyze-trace-failures.
- **Fired = FAIL:** starting a new-agent design flow for an existing, misbehaving agent.
