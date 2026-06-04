# Skill-Tester Behavioural Report

- Date: 2026-06-04T09:07:24.704Z
- Skills: invoke-deployment, compare-agents, setup-observability, generate-synthetic-dataset
- Repeat: 1× per case

## Summary

- Total cases: 17
- Passed: 16
- Failed: 1

## Behavioural

| Scenario | Type | Routing | Usage | Overall | Score | Evidence |
|----------|------|---------|-------|---------|-------|----------|
| invoke-deployment/B1 | explicit | pass | pass | PASS | 100 | tools: Read |
| invoke-deployment/B2 | explicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| invoke-deployment/B3 | explicit | fail | pass | FAIL | 67 | FAIL routing:invoke-deployment: selected=[setup-observability] |
| invoke-deployment/B5 | implicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| invoke-deployment/N1 | negative | pass | n/a | PASS | 100 | tools: Read, mcp__orq-workspace__search_entities, mcp__orq-workspace__search_entities, mcp |
| compare-agents/B1 | explicit | pass | pass | PASS | 100 | tools: Read, Read, Read |
| compare-agents/B2 | explicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| compare-agents/B3 | implicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| compare-agents/N1 | negative | pass | n/a | PASS | 100 | tools: Read, AskUserQuestion |
| setup-observability/B1 | explicit | pass | pass | PASS | 100 | tools: Read, Read, AskUserQuestion, Glob |
| setup-observability/B2 | implicit | pass | pass | PASS | 100 | tools: Read, Read |
| setup-observability/B3 | explicit | pass | pass | PASS | 100 | tools: Read, Read |
| setup-observability/B4 | contextual | pass | pass | PASS | 100 | tools: Read, Read |
| setup-observability/N1 | negative | pass | n/a | PASS | 100 | tools: Read, mcp__orq-workspace__get_analytics_overview, mcp__orq-workspace__list_traces,  |
| generate-synthetic-dataset/B1 | explicit | pass | pass | PASS | 100 | tools: Read |
| generate-synthetic-dataset/B2 | implicit | pass | pass | PASS | 100 | tools: Read, AskUserQuestion |
| generate-synthetic-dataset/N1 | negative | pass | n/a | PASS | 100 | tools: Read, mcp__orq-workspace__get_analytics_overview, mcp__orq-workspace__list_traces,  |

## Failure detail

### invoke-deployment/B3 — explicit
- selected_skill: `setup-observability` | expected: `invoke-deployment`
- tool_calls: Read, Read
- ✗ `routing:invoke-deployment` — selected=[setup-observability]

## Non-gating prose notes (not scored)

These soft/methodology assertions are recorded from the catalog but do not affect pass/fail.

- **invoke-deployment/B1**
  - maps the {{customer_name}} variable into inputs; reads ORQ_API_KEY from env; includes identity
- **invoke-deployment/B2**
  - uses A2A parts message format; saves task_id and passes it in the follow-up
- **invoke-deployment/B3**
  - uses provider/model format and points the OpenAI client at the router base_url
- **invoke-deployment/B5**
  - routes on the description; confirms the key via search_entities; generates SDK code reading ORQ_API_KEY from env
- **invoke-deployment/N1**
  - editing a prompt -> should route to optimize-prompt, not generate an invocation call
- **compare-agents/B1**
  - both @job functions share the same evaluator; orq.ai job uses agents.responses.create()
- **compare-agents/B2**
  - delegates dataset creation to generate-synthetic-dataset and evaluator to build-evaluator
  - generates one LangGraph job pattern and one orq.ai job pattern with a shared evaluator
- **compare-agents/B3**
  - routes on the description; sets up a head-to-head evaluatorq experiment with one shared evaluator
- **compare-agents/N1**
  - single-config A/B -> should route to run-experiment, not a cross-framework evaluatorq script
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
- **generate-synthetic-dataset/B1**
  - proposes dimensions of variation (dimensions->tuples->NL) rather than a naive 'generate 5'
  - produces diverse cases including adversarial ones; persists via create_dataset + create_datapoints
- **generate-synthetic-dataset/B2**
  - routes on the description; designs dimensions and includes adversarial coverage
- **generate-synthetic-dataset/N1**
  - ample real data exists -> should route to analyze-trace-failures, not synthetic generation
