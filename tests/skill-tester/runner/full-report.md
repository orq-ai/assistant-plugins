# Skill-Tester Behavioural Report

- Date: 2026-06-04T08:54:26.483Z
- Skills: analyze-trace-failures, build-agent, build-evaluator, compare-agents, generate-synthetic-dataset, invoke-deployment, optimize-prompt, run-experiment, setup-observability
- Repeat: 1× per case

## Summary

- Total cases: 34
- Passed: 26
- Failed: 8

## Behavioural

| Scenario | Type | Routing | Usage | Overall | Score | Evidence |
|----------|------|---------|-------|---------|-------|----------|
| analyze-trace-failures/B1 | explicit | pass | pass | PASS | 100 | tools: Read, mcp__orq-workspace__get_analytics_overview, mcp__orq-workspace__list_traces,  |
| analyze-trace-failures/B2 | implicit | pass | pass | PASS | 100 | tools: Read |
| analyze-trace-failures/N1 | negative | pass | n/a | PASS | 100 | tools: Read, AskUserQuestion |
| build-agent/B1 | explicit | pass | pass | PASS | 100 | tools: Read, mcp__orq-workspace__list_models, mcp__orq-workspace__search_directories, mcp_ |
| build-agent/B2 | implicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| build-agent/N1 | negative | pass | n/a | PASS | 100 | tools: Read, mcp__orq-workspace__get_analytics_overview, mcp__orq-workspace__search_entiti |
| build-evaluator/B1 | explicit | pass | pass | PASS | 100 | tools: Read, mcp__orq-workspace__create_python_eval |
| build-evaluator/B2 | explicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| build-evaluator/B3 | implicit | fail | fail | FAIL | 33 | FAIL routing:build-evaluator: selected=[] |
| build-evaluator/N1 | negative | pass | n/a | PASS | 100 | tools: (none) |
| compare-agents/B1 | explicit | pass | fail | FAIL | 50 | FAIL text:0: no match for {"op":"regex","value":"from\\s+orq_ai_sdk\\s+import\\s+Orq"} |
| compare-agents/B2 | explicit | pass | pass | PASS | 100 | tools: Read, mcp__orq-workspace__search_entities, AskUserQuestion |
| compare-agents/B3 | implicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| compare-agents/N1 | negative | pass | n/a | PASS | 100 | tools: Read, AskUserQuestion |
| generate-synthetic-dataset/B1 | explicit | pass | pass | PASS | 100 | tools: Read |
| generate-synthetic-dataset/B2 | implicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| generate-synthetic-dataset/N1 | negative | pass | n/a | FAIL | 50 | FAIL run-completed: timed out |
| invoke-deployment/B1 | explicit | pass | fail | FAIL | 25 | FAIL text:0: no match for {"op":"contains","value":"deployments.invoke"} |
| invoke-deployment/B2 | explicit | pass | fail | FAIL | 67 | FAIL text:0: no match for {"op":"contains","value":"responses.create"} |
| invoke-deployment/B3 | explicit | fail | pass | FAIL | 67 | FAIL routing:invoke-deployment: selected=[] |
| invoke-deployment/B5 | implicit | pass | fail | FAIL | 33 | FAIL text:0: no match for {"op":"contains","value":"deployments.invoke"} |
| invoke-deployment/N1 | negative | pass | n/a | PASS | 100 | tools: Read, mcp__orq-workspace__search_entities, mcp__orq-workspace__search_entities, mcp |
| optimize-prompt/B1 | explicit | pass | pass | PASS | 100 | tools: Read |
| optimize-prompt/B2 | implicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| optimize-prompt/N1 | negative | pass | n/a | PASS | 100 | tools: (none) |
| optimize-prompt/N2 | negative | pass | n/a | PASS | 100 | tools: (none) |
| run-experiment/B1 | explicit | pass | pass | PASS | 100 | tools: Read, mcp__orq-workspace__search_entities, mcp__orq-workspace__search_entities, mcp |
| run-experiment/B2 | implicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| run-experiment/N1 | negative | pass | n/a | PASS | 100 | tools: Read, AskUserQuestion |
| setup-observability/B1 | explicit | pass | pass | PASS | 100 | tools: Read, Read, AskUserQuestion |
| setup-observability/B2 | implicit | pass | fail | FAIL | 50 | FAIL text:0: no match for {"op":"regex","value":"(?i)openinference\|instrument"} |
| setup-observability/B3 | explicit | pass | pass | PASS | 100 | tools: Read, Read |
| setup-observability/B4 | contextual | pass | pass | PASS | 100 | tools: Read, Read |
| setup-observability/N1 | negative | pass | n/a | PASS | 100 | tools: Read, mcp__orq-workspace__get_analytics_overview, mcp__orq-workspace__list_traces,  |

## Failure detail

### build-evaluator/B3 — implicit
- selected_skill: `null` | expected: `build-evaluator`
- tool_calls: (none)
- ✗ `routing:build-evaluator` — selected=[]
- ✗ `tool:create_python_eval` — missing

### compare-agents/B1 — explicit
- selected_skill: `compare-agents+run-experiment` | expected: `compare-agents`
- tool_calls: Read, Read, get_agent, get_agent, search_entities, search_entities, search_entities, AskUserQuestion
- ✗ `text:0` — no match for {"op":"regex","value":"from\\s+orq_ai_sdk\\s+import\\s+Orq"}
- ✗ `text:1` — no match for {"op":"contains","value":"responses.create"}

### generate-synthetic-dataset/N1 — negative
- selected_skill: `analyze-trace-failures` | expected: `null`
- tool_calls: Read, AskUserQuestion, get_analytics_overview, list_traces, Read, Bash, Read, TodoWrite, Grep, Grep, Grep, Grep, Grep, Grep, Grep, Grep, get_analytics_overview, list_spans, get_span, Grep, list_spans, list_traces, Grep, Grep
- ✗ `run-completed` — timed out

### invoke-deployment/B1 — explicit
- selected_skill: `invoke-deployment` | expected: `invoke-deployment`
- tool_calls: Read, get_deployment, search_entities, search_entities, search_entities
- ✗ `text:0` — no match for {"op":"contains","value":"deployments.invoke"}
- ✗ `text:1` — no match for {"op":"regex","value":"(?i)customer_name"}
- ✗ `text:2` — no match for {"op":"regex","value":"os\\.environ|getenv"}

### invoke-deployment/B2 — explicit
- selected_skill: `invoke-deployment` | expected: `invoke-deployment`
- tool_calls: AskUserQuestion, Read, search_entities, search_entities, search_entities
- ✗ `text:0` — no match for {"op":"contains","value":"responses.create"}

### invoke-deployment/B3 — explicit
- selected_skill: `null` | expected: `invoke-deployment`
- tool_calls: invoke_model
- ✗ `routing:invoke-deployment` — selected=[]

### invoke-deployment/B5 — implicit
- selected_skill: `invoke-deployment` | expected: `invoke-deployment`
- tool_calls: Read
- ✗ `text:0` — no match for {"op":"contains","value":"deployments.invoke"}
- ✗ `text:1` — no match for {"op":"regex","value":"os\\.environ|getenv"}

### setup-observability/B2 — implicit
- selected_skill: `setup-observability` | expected: `setup-observability`
- tool_calls: Read, Read
- ✗ `text:0` — no match for {"op":"regex","value":"(?i)openinference|instrument"}

## Non-gating prose notes (not scored)

These soft/methodology assertions are recorded from the catalog but do not affect pass/fail.

- **analyze-trace-failures/B1**
  - describes a mixed sampling strategy (random / failure-driven / outlier)
  - uses open coding -> axial coding to build a 4-8 mode taxonomy; finds the FIRST upstream failure
  - does not jump to building evaluators or changing prompts before reading traces
- **analyze-trace-failures/B2**
  - routes on the description; proposes reading traces first (open->axial coding) before any fix
- **analyze-trace-failures/N1**
  - no traces exist yet -> should route to setup-observability, not analysis
- **build-agent/B1**
  - asks clarifying questions about purpose, users, and success criteria before building
  - consults list_models when selecting a model; starts with a capable model; does not add >8 tools
- **build-agent/B2**
  - routes on the description; runs discovery before building; does not misroute to invoke-deployment
- **build-agent/N1**
  - existing agent misbehaving -> should route to analyze-trace-failures, not a new-agent build
- **build-evaluator/B1**
  - explains code checks are cheaper/deterministic than an LLM judge for this
- **build-evaluator/B2**
  - suggests splitting into separate evaluators (one criterion each)
  - defaults to binary Pass/Fail; mentions validating against human labels (TPR/TNR)
- **build-evaluator/B3**
  - routes here on the description alone; recommends a code-based check for JSON validity
- **build-evaluator/N1**
  - answers as ordinary coding; does not invoke build-evaluator or talk about TPR/TNR judges
- **compare-agents/B1**
  - identifies agents via search_entities; both @job functions share the same evaluator
- **compare-agents/B2**
  - delegates dataset creation to generate-synthetic-dataset and evaluator to build-evaluator
  - generates one LangGraph job pattern and one orq.ai job pattern with a shared evaluator
- **compare-agents/B3**
  - routes on the description; sets up a head-to-head evaluatorq experiment with one shared evaluator
- **compare-agents/N1**
  - single-config A/B -> should route to run-experiment, not a cross-framework evaluatorq script
- **generate-synthetic-dataset/B1**
  - proposes dimensions of variation (dimensions->tuples->NL) rather than a naive 'generate 5'
  - produces diverse cases including adversarial ones; persists via create_dataset + create_datapoints
- **generate-synthetic-dataset/B2**
  - routes on the description; designs dimensions and includes adversarial coverage
- **generate-synthetic-dataset/N1**
  - ample real data exists -> should route to analyze-trace-failures, not synthetic generation
- **invoke-deployment/B1**
  - confirms the key via search_entities(type=deployment); maps the variable; includes identity
- **invoke-deployment/B2**
  - uses A2A parts message format; saves task_id and passes it in the follow-up
- **invoke-deployment/B3**
  - uses provider/model format and points the OpenAI client at the router base_url
- **invoke-deployment/B5**
  - routes on the description; confirms the key via search_entities; reads ORQ_API_KEY from env
- **invoke-deployment/N1**
  - editing a prompt -> should route to optimize-prompt, not generate an invocation call
- **optimize-prompt/B1**
  - analyzes against a structured guidelines framework; offers a diff and asks before applying
  - recommends run-experiment to validate afterward; preserves the original for rollback
- **optimize-prompt/B2**
  - routes on the description; analyzes against the framework; shows a diff and asks before applying
- **optimize-prompt/N1**
  - ordinary performance work -> the prompt-optimization framework must not fire
- **optimize-prompt/N2**
  - production failures -> analyze-trace-failures first, not a blind prompt rewrite
- **run-experiment/B1**
  - calls create_experiment with the named dataset + evaluator references
  - uses binary Pass/Fail criteria; treats 100% pass rate as too-easy (target ~70-85%)
- **run-experiment/B2**
  - routes on the description; sets up an experiment over a dataset with binary evaluators
  - does not misroute to optimize-prompt (which edits, not measures)
- **run-experiment/N1**
  - cross-framework comparison -> should route to compare-agents
- **setup-observability/B1**
  - recommends AI Router mode; changes base_url; reports there was no existing tracing
- **setup-observability/B2**
  - recommends Observability mode; initializes the instrumentor BEFORE the framework client
  - sets OTEL env vars; warns if any OTEL_* vars already exist
- **setup-observability/B3**
  - documents capture_input / capture_output defaulting to True
- **setup-observability/B4**
  - explains capture defaults are True, so PII is sent unless disabled
- **setup-observability/N1**
  - traces already exist -> should route to analyze-trace-failures, not (re)install tracing
