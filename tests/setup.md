# Test Setup & Teardown

Shared infrastructure for all orq-skills test suites. Run this first, then run any combination of `mcp-tools.md`, `commands.md`, `skills.md`. Run teardown at the end.

## Safety Rules

- **NEVER delete, modify, or update any pre-existing resource** in the workspace
- All test resources are created under an isolated path `{Project}/orq-skills-tests` (discover available projects via `search_entities(type=project)` first — do NOT assume `Default/` exists)
- All test resource keys/names use the prefix `orq-skills-test-` for clear identification
- Teardown only deletes resources that were created during the test run
- Track all created resource IDs for precise cleanup

### MCP Delete Support

Only **datasets** and **datapoints** can be deleted via MCP. The following resource types have **no MCP delete tool** — test resources will persist and require manual cleanup via the orq.ai dashboard:

| Resource | Can Create | Can Delete | Cleanup |
|----------|-----------|-----------|---------|
| Dataset | `create_dataset` | `delete_dataset` | Automatic |
| Datapoints | `create_datapoints` | `delete_datapoints` | Automatic |
| Agent | `create_agent` | — | Manual |
| Evaluator | `create_python_eval` / `create_llm_eval` | — | Manual |
| Experiment | `create_experiment` | — | Manual |

Keep the number of non-deletable test resources to a minimum. Reuse agents and evaluators across tests where possible instead of creating extras.

---

## Setup

1. Verify `$ORQ_API_KEY` is set (bash `echo`)
2. Call `search_entities(type=agent)` to verify MCP connectivity
3. Discover available projects via `search_entities(type=project)` — pick the first one and use `{project_name}/orq-skills-tests` as the test path
4. Seed test data (all under the discovered test path):
   - `create_agent` → key: `orq-skills-test-echo`, model: `openai/gpt-4.1-mini`, instructions: "Echo back the user's message verbatim" *(manual cleanup required)*
   - `create_dataset` → `orq-skills-test-dataset` + `create_datapoints` (5 rows with inputs + expected_output) *(auto-deletable)*
   - `create_python_eval` → key: `orq-skills-test-eval-length`, code: checks `len(log['output']) > 0` *(manual cleanup required)*

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

### Manual cleanup required (no MCP delete tools)

The following resource types **cannot be deleted via MCP**. List them in the report so they can be cleaned up manually via the orq.ai dashboard:

```
# Agents — NO delete_agent MCP tool
search_entities(type=agent, query="orq-skills-test-") → log for manual cleanup

# Experiments — NO delete_experiment MCP tool
search_entities(type=experiment, query="orq-skills-test-") → log for manual cleanup

# Evaluators — NO delete_evaluator MCP tool, NO search_entities(type=evaluator)
# Must track created evaluator IDs from setup → log for manual cleanup
```

### Cleanup summary

Report all resources in the test report with their cleanup status:
- For each auto-deleted resource: confirm deletion succeeded
- For each manual resource: list the ID, key, and type so the user can find and delete them in the dashboard
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
