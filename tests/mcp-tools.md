# MCP Tool Integration Tests

Tests the orq.ai MCP server tools directly. Requires `setup.md` to have run first (seed data must exist).

**Prerequisites:** `orq-skills-test-echo` agent, `orq-skills-test-dataset` dataset, `orq-skills-test-eval-length` evaluator from setup.

---

## Read-only tools (safe, no cleanup needed)

1. `search_entities` — all 9 types (agent, dataset, prompt, experiment, knowledge, memory_store, deployment, project, evaluator)
2. `search_directories` — list project dirs
3. `list_models(modelType=chat)` → verify non-empty
4. `list_registry_keys` → verify returns array
5. `list_traces` → verify returns array
6. `list_spans` for a trace (if traces exist)
7. `get_span` for a span (if spans exist)
8. `get_analytics_overview(period=24h)`
9. `query_analytics(metric=usage, time_range={start:"7d"})`

## Dataset CRUD cycle (create → use → cleanup)

10. `create_dataset` → `orq-skills-test-crud-dataset`
11. `create_datapoints` → 3 rows into it
12. `list_datapoints` → verify 3 rows
13. `update_datapoint` → modify one row, verify change
14. `delete_datapoints` → delete 1, verify 2 remain
15. `delete_dataset` → delete `orq-skills-test-crud-dataset` (only this test resource)

## Evaluator tools *(manual cleanup required — no MCP delete tool)*

16. `create_llm_eval` → key: `orq-skills-test-llm-eval`, with simple judge prompt
17. `create_python_eval` → key: `orq-skills-test-py-eval`
18. `get_evaluator_llm(key=orq-skills-test-llm-eval)` → verify returns prompt and model
19. `get_evaluator_python(key=orq-skills-test-py-eval)` → verify returns code

## Agent tools

20. `get_agent(key=orq-skills-test-echo)` → verify config matches what we created
21. `create_agent` → key: `orq-skills-test-crud-agent` *(manual cleanup required — no MCP delete tool)*
22. `update_agent(key=orq-skills-test-crud-agent)` → update instructions (only our test agent)

## Experiment tools

23. `create_experiment` → key: `orq-skills-test-experiment` with seeded dataset + evaluator *(manual cleanup required — no MCP delete tool)*
24. `list_experiment_runs`

---

## Critical Files

- orq MCP server (external dependency)
