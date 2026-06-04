# Skill Smoke Tests

Conversational tests for each skill. Trigger each with a simple scenario and verify the first phase responds correctly. **No pre-existing resources are modified.**

Requires `setup.md` to have run first (seed data for `run-experiment` test).

---

## `setup-observability`

### Scenario 1: Python OpenAI app — AI Router path

- Provide: a small Python file using `openai.OpenAI()` with no existing tracing
- Ask: "Add orq.ai tracing to my app"
- Verify Phase 1: scans the project, identifies OpenAI SDK, reports no existing tracing
- Verify Phase 2: recommends **AI Router** mode (framework supports it, fastest path)
- Verify Phase 3: changes `base_url` to `https://api.orq.ai/v2/router`, uses `provider/model` format (e.g., `openai/gpt-4o`)
- Verify: does NOT use `from orq_ai_sdk.tracing import traced` (wrong import path)
- Verify: does NOT hardcode `service.name=my-app`

### Scenario 2: LangChain app — Observability path

- Provide: a Python file using `langchain_openai.ChatOpenAI()` calling a provider directly
- Ask: "I want to add tracing but keep my existing LLM calls"
- Verify Phase 2: recommends **Observability** mode (user wants to keep existing calls)
- Verify Phase 3: sets OTEL env vars, installs OpenInference instrumentor
- Verify: instrumentor is initialized BEFORE framework client creation
- Verify: warns about existing OTEL config if any `OTEL_*` vars already exist

### Scenario 3: Verify code correctness

- Ask: "Show me how to use the @traced decorator"
- Verify: import path is `from orq_ai_sdk.traced import traced` or `from orq_ai_sdk import traced`
- Verify: parameters shown are `name`, `type`, `capture_input`, `capture_output`, `attributes`
- Verify: does NOT show `user_id` as a direct `@traced` parameter (should be in `attributes={}`)
- Verify: does NOT use `orq_traced_input()` or `orq_traced_output()` (these don't exist)
- Verify: `capture_input` / `capture_output` defaults documented as `True`

### Scenario 4: Sensitive data handling

- Provide: a Python function that takes `card_number` and `user_email` as arguments
- Ask: "Add tracing to this function"
- Verify: uses `capture_input=False` and/or `capture_output=False`
- Verify: explains that defaults are `True` (all inputs/outputs sent to orq.ai unless disabled)

### Scenario 5: Existing OTEL configuration

- Provide: a project with existing `OTEL_EXPORTER_OTLP_ENDPOINT` pointing to Datadog
- Ask: "Add orq.ai observability"
- Verify: detects existing OTEL configuration in Phase 1
- Verify: warns about overwriting before setting new env vars
- Verify: asks user for confirmation before proceeding

---

## `invoke-deployment`

### Scenario 1: Deployment invocation (happy path)

- Ask: "Invoke my deployment `customer-support` with variable `customer_name` set to 'Jane'"
- Verify Phase 1: calls `search_entities` with `type: "deployment"` to confirm key
- Verify Phase 2: identifies `{{customer_name}}` as a required input, maps it
- Verify Phase 3: generates Python SDK code using `client.deployments.invoke(key=..., inputs={...})`
- Verify: code uses `os.environ["ORQ_API_KEY"]`, never hardcodes the key
- Verify: code includes `identity={"id": ...}`

### Scenario 2: Agent invocation with multi-turn

- Ask: "Send a message to my agent and then follow up"
- Verify: uses `client.agents.responses.create()`, NOT `client.agents.invoke()`
- Verify: message format uses A2A parts structure (`parts: [{kind: "text", text: ...}]`), not OpenAI-style `content`
- Verify: saves `task_id` from first response and passes it in the follow-up call

### Scenario 3: Model (AI Router) invocation

- Ask: "Call GPT-4.1 directly through the AI Router"
- Verify: uses `provider/model` format (e.g., `openai/gpt-4.1`), not bare model name
- Verify: points OpenAI client at `base_url="https://api.orq.ai/v2/router"`
- Verify: does NOT use orq SDK for this path — uses `openai.OpenAI()`

### Scenario 4: Streaming recommendation

- Ask: "I need to call a deployment for a chatbot UI"
- Verify: recommends `stream=True` for user-facing invocations

---

## `compare-agents`

### Scenario 1: orq.ai vs orq.ai comparison

- Ask: "Compare my two orq.ai agents `agent-gpt4o` and `agent-claude` head-to-head"
- Verify Phase 1: identifies both agents, uses `search_entities` with `type: "agent"`
- Verify Phase 4: generates evaluatorq script with two `@job` functions, each using `agents.responses.create()` (not `agents.invoke()`)
- Verify: script uses `from orq_ai_sdk import Orq` (not `from orq import ...`)
- Verify: both jobs use the same evaluator for fair comparison

### Scenario 2: External vs orq.ai comparison

- Ask: "Compare my LangGraph agent against my orq.ai agent"
- Verify: generates one LangGraph job pattern and one orq.ai job pattern
- Verify: delegates dataset creation to `generate-synthetic-dataset` (does not create inline)
- Verify: delegates evaluator creation to `build-evaluator` (does not design from scratch)

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

## `manage-skills`

### Scenario 1: List skills

- Ask: "Show me the Skills in my workspace"
- Verify: calls `list_skills` (or REST `GET /v2/skills` fallback) and **paginates to completion** (cursor-based — `limit`, `starting_after`, `ending_before`)
- Verify: any user-requested filter (project, tags, name substring) is applied **client-side** after pagination — does NOT pass `project_id`/`tags`/`q` to `list_skills` (the endpoint does not accept them)
- Verify: presents `display_name`, project scope, `tags`, `path` per Skill
- Verify: treats `version` as read-only and server-stamped — does NOT include it in create/update payloads
- Verify: does NOT compute reference counts eagerly — defers them as on-demand work

### Scenario 2: Create skill (authoring guidance)

- Ask: "Create a Skill called `extract_receipt_fields`"
- Verify Phase 3: asks for `description`, `tags`, `project_id` (default project-scoped, not workspace-wide), and `path`
- Verify: warns if the proposed `instructions` contain `+NEVER+` / "you MUST refuse" prose constraints and recommends an MCP tool gate instead
- Verify: relies on `create_skill` + `AlreadyExists` error handling for the uniqueness check rather than a separate pre-flight lookup (works whether or not a `:checkDisplayNameAvailability` helper endpoint is exposed in the workspace)
- Verify: `create_skill` payload uses `display_name` and `instructions` (not `name` / `body` / `doc`); does NOT include `enabled` (field does not exist)
- Verify: echoes back the consumption pattern after create — `{{skill.<display_name>}}` (canonical) with `{{snippet.<display_name>}}` noted as the backward-compatible alias

### Scenario 3: Delete skill — reference scan

- Provide context: a Skill referenced by 2 prompts via `{{skill.<display_name>}}` / `{{snippet.<display_name>}}`
- Ask: "Delete this Skill"
- Verify: runs a reference scan BEFORE deletion (`search_entities` then per-entity body fetch with `get_deployment` / `get_agent` / `get_skill`, substring-matching both `{{skill.<display_name>}}` and `{{snippet.<display_name>}}` case-sensitively)
- Verify: surfaces the references found and offers tagging the Skill with `retired` as the default first step (NOT `enabled: false` — that field does not exist)
- Verify: does NOT call `update_agent` to "prune" `agent.skills[]` — that field is unrelated A2A AgentCard metadata
- Verify: never auto-deletes; always requires explicit consent after the user has seen the reference list
- Verify: final report lists what was deleted (or tagged `retired`) and any references the user should manually update

### Scenario 4: Update skill (no blind overwrite, rename warning)

- Ask: "Update the description of the `refund_policy` Skill"
- Verify: calls `get_skill(skill_id=...)` first, shows the user the current state
- Verify: only patches the changed field — does not echo back unchanged `tags`/`instructions`
- Verify: does NOT pass `version` in `update_skill` (`version` is read-only / server-stamped, not a settable field)
- Verify: confirms the diff with the user before `update_skill`
- Then ask: "Rename `refund_policy` to `refund_policy_eu`"
- Verify: warns that renaming `display_name` silently breaks every `{{skill.refund_policy}}` / `{{snippet.refund_policy}}` reference and runs the reference scan before sending the rename
- Verify: when rewriting `instructions`, applies clarity heuristics from `optimize-prompt` rather than blindly delegating

### Scenario 5: Failure-mode handling

- Ask: "Create a Skill called `refund_policy`" (in a workspace that already has one)
- Verify: handles `AlreadyExists` gracefully — surfaces the conflicting Skill and offers either a renamed create or `update_skill`
- Ask: "Retire the `refund_policy` Skill"
- Verify: routes to Phase 4 (update, add `retired` tag), NOT to Phase 5 (delete)
- Verify: explains that tagging as `retired` is reversible; does NOT suggest `enabled: false` (field does not exist)

---

## Critical Files

- `skills/setup-observability/SKILL.md`
- `skills/setup-observability/resources/traced-decorator-guide.md`
- `skills/setup-observability/resources/framework-integrations.md`
- `skills/setup-observability/resources/baseline-checklist.md`
- `skills/invoke-deployment/SKILL.md`
- `skills/invoke-deployment/resources/api-reference.md`
- `skills/compare-agents/SKILL.md`
- `skills/compare-agents/resources/job-patterns.md`
- `skills/compare-agents/resources/evaluatorq-api.md`
- `skills/compare-agents/resources/gotchas.md`
- `skills/build-agent/SKILL.md`
- `skills/build-evaluator/SKILL.md`
- `skills/generate-synthetic-dataset/SKILL.md`
- `skills/optimize-prompt/SKILL.md`
- `skills/analyze-trace-failures/SKILL.md`
- `skills/run-experiment/SKILL.md`
- `skills/manage-skills/SKILL.md`
- `skills/manage-skills/resources/authoring-guide.md`
- `skills/manage-skills/resources/governance-guide.md`
- `skills/manage-skills/resources/known-caveats.md`
- `commands/manage-skills.md`
