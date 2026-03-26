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

## `compare-agents`

### Scenario 1: orq.ai vs external agent (Python)

- Ask: "Compare my orq.ai agent orq-skills-test-echo against a simple Python function that reverses the input"
- Verify Phase 1: identifies two agents — orq.ai (uses `search_entities` to find `orq-skills-test-echo`) and generic Python
- Verify Phase 1: asks or confirms language preference (Python)
- Verify Phase 2: delegates to `generate-synthetic-dataset` or creates dataset via `create_dataset` + `create_datapoints` with `orq-skills-test-` prefix
- Verify Phase 3: delegates to `build-evaluator` or creates evaluator via `create_llm_eval`
- Verify Phase 4: generates a Python script with:
  - `from evaluatorq import evaluatorq, job, DataPoint, EvaluationResult`
  - One `@job("OrqAgent")` using `orq.agents.responses.create()` (NOT `agents.invoke()`)
  - One `@job("ReverseAgent")` wrapping the Python function
  - An evaluator scorer invoking the orq.ai judge by ID
  - A `evaluatorq()` call wiring jobs + data + evaluators
- Verify: script uses A2A message format `{"role": "user", "parts": [{"kind": "text", "text": ...}]}` (NOT OpenAI-style)
- Verify: does NOT hardcode datapoints inline if a dataset was created on the platform

### Scenario 2: orq.ai vs orq.ai

- Ask: "Compare two versions of my agent — orq-skills-test-echo with model gpt-4o-mini vs the same agent"
- Verify: generates two orq.ai job patterns with different job names (e.g., `OrqAgent-A`, `OrqAgent-B`)
- Verify: uses the same `agent_key` for both (since it's the same agent)
- Verify: warns about same-model comparison if both use the same model

### Scenario 3: TypeScript preference

- Ask: "I want to benchmark a LangGraph agent against my orq.ai agent, using TypeScript"
- Verify Phase 4: generates TypeScript, not Python
- Verify: imports from `@orq-ai/evaluatorq`
- Verify: uses `wrapLangGraphAgent` from `@orq-ai/evaluatorq/langchain` for the LangGraph job
- Verify: uses `job()` function (not `@job` decorator)

### Scenario 4: Skill boundary — redirects

- Ask: "Create a dataset for testing my agents"
- Verify: redirects to `generate-synthetic-dataset` (does NOT handle dataset creation itself)
- Ask: "Run an experiment with my orq.ai deployment"
- Verify: redirects to `run-experiment` (no external agents involved)

### Scenario 5: Dataset bias prevention

- Provide: two agents — one with a mock weather tool returning "Sunny, 22C", one with a real API
- Ask: "Compare these agents on weather queries"
- Verify: does NOT write expected outputs matching the mock data
- Verify: expected outputs describe correctness criteria (e.g., "should include current temperature from a real source")

---

## Critical Files

- `skills/setup-observability/SKILL.md`
- `skills/setup-observability/resources/traced-decorator-guide.md`
- `skills/setup-observability/resources/framework-integrations.md`
- `skills/setup-observability/resources/baseline-checklist.md`
- `skills/build-agent/SKILL.md`
- `skills/build-evaluator/SKILL.md`
- `skills/generate-synthetic-dataset/SKILL.md`
- `skills/optimize-prompt/SKILL.md`
- `skills/analyze-trace-failures/SKILL.md`
- `skills/run-experiment/SKILL.md`
- `skills/compare-agents/SKILL.md`
- `skills/compare-agents/resources/job-patterns.md`
- `skills/compare-agents/resources/evaluatorq-api.md`
- `skills/compare-agents/resources/gotchas.md`
