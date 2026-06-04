# Test Setup & Teardown

Shared infrastructure for all orq-skills test suites. Run this first, then run any combination of `mcp-tools.md`, `commands.md`, `skills.md`. Run teardown at the end.

## Safety Rules

- **NEVER delete, modify, or update any pre-existing resource** in the workspace
- All test resources are created under an isolated path `{Project}/orq-skills-tests` (discover available projects via `search_entities(type=project)` first — do NOT assume `Default/` exists)
- All test resource keys/names use the prefix `orq-skills-test-` for clear identification
- Teardown only deletes resources that were created during the test run
- Track all created resource IDs for precise cleanup

### MCP Delete Support

`delete_entity(type=…, id=…)` deletes most lifecycle entities — `agent`, `evaluator`, `experiment`, `deployment`, `prompt`, `knowledge`, `memory_store`, and more. Datasets/datapoints keep their dedicated tools and skills use `delete_skill`, so teardown can be **automatic for every type this suite creates**.

| Resource | Can Create | Can Delete | Cleanup |
|----------|-----------|-----------|---------|
| Dataset | `create_dataset` | `delete_dataset` | Automatic |
| Datapoints | `create_datapoints` | `delete_datapoints` | Automatic |
| Agent | `create_agent` | `delete_entity(type=agent)` | Automatic |
| Evaluator | `create_python_eval` / `create_llm_eval` | `delete_entity(type=evaluator)` | Automatic |
| Experiment | `create_experiment` | `delete_entity(type=experiment)` | Automatic |
| Deployment | `create_deployment` | `delete_entity(type=deployment)` | Automatic |
| Skill | `create_skill` | `delete_skill` | Automatic |

If `delete_entity` is ever refused for a type, fall back to logging the ID for manual dashboard cleanup. Still keep created test resources minimal — reuse the seeded echo agent / length eval where a case allows.

---

## Setup

1. Verify `$ORQ_API_KEY` is set (bash `echo`)
2. Call `search_entities(type=agent)` to verify MCP connectivity
3. Discover available projects via `search_entities(type=project)` — pick the first one and use `{project_name}/orq-skills-tests` as the test path
4. Seed test data (all under the discovered test path):
   - `create_agent` → key: `orq-skills-test-echo`, model: `openai/gpt-4.1-mini`, instructions: "Echo back the user's message verbatim" *(delete via `delete_entity(type=agent)`)*
   - `create_dataset` → `orq-skills-test-dataset` + `create_datapoints` (5 rows with inputs + expected_output) *(delete via `delete_dataset`)*
   - `create_python_eval` → key: `orq-skills-test-eval-length`, code: checks `len(log['output']) > 0` *(delete via `delete_entity(type=evaluator)`)*

Track each as PASS/FAIL. Store all created IDs for teardown.

---

## Teardown

Delete ONLY resources created during this test run (tracked by ID).

### Automatic cleanup (MCP delete tools available)

```
# Datasets — delete via MCP
search_entities(type=dataset, query="orq-skills-test-") → delete_dataset for each

# Datapoints — already cleaned up when parent dataset is deleted
```

### Automatic cleanup via delete_entity

Agents, evaluators, experiments, and deployments delete through `delete_entity`:

```
# Agents
search_entities(type=agent, query="orq-skills-test-") → delete_entity(type=agent, id=<id>) for each

# Experiments
search_entities(type=experiment, query="orq-skills-test-") → delete_entity(type=experiment, id=<id>) for each

# Evaluators — no search_entities(type=evaluator); use the IDs tracked at create time
delete_entity(type=evaluator, id=<id>) for each tracked evaluator
```

If any `delete_entity` call is refused by the server, fall back to logging the ID for manual dashboard cleanup.

### Cleanup summary

Report all resources in the test report with their cleanup status:
- For each deleted resource: confirm deletion succeeded
- For anything the server refused to delete (fallback): list the ID, key, and type so the user can remove it from the dashboard
- Note: `delete_dataset` has a known output validation bug (returns error despite success) — verify deletion by calling `list_datapoints` on the deleted dataset ID

---

## Report Generation

Write `./tests/test-report.md` with:

```markdown
# Test Report: orq-skills Plugin
## Date: {date}

## Summary
- Total tests: N
- Passed: N
- Failed: N
- Skipped: N

## What Works
{list of passing tests with brief notes}

## What Doesn't Work
{list of failing tests with error details}

## What Needs Fixing
{prioritized issues found during testing}

## What Could Be Better
{suggestions for improvement based on test observations}

## Cleanup Status
{list of test resources created and their cleanup status}
```
