# MCP Tool Integration Tests

Tests the orq.ai MCP server tools directly — one thorough suite covering every
lifecycle-able entity (create → verify exists → mutate → delete) plus the input
edge cases that stress the server's contract. Requires `setup.md` to have run
first (seed data must exist).

**Prerequisites:** `orq-skills-test-echo` agent, `orq-skills-test-dataset` dataset,
`orq-skills-test-eval-length` evaluator from [`setup.md`](setup.md).

**Isolation (inherited from `setup.md`, non-negotiable):**
- NEVER modify, update, or delete any pre-existing workspace resource.
- Every created resource lives under the discovered test path `{project}/orq-skills-tests`,
  keyed with the `orq-skills-test-` prefix.
- Track every created ID. Teardown (Phase 8) deletes ONLY this run's resources.
- Reuse the seeded echo agent / length eval instead of creating extras wherever a case allows.

**How to read a case:** each numbered case lists the **Operation**, what to **Verify**, the
**Cleanup** owner, and (where relevant) **Edge** inputs that should be *rejected*. Negative
cases assert the server returns an error — a silent accept is a FAIL.

---

## Phase 0 — Static pre-flight (no workspace, no API key needed)

Run before any live call; these gate the suite and need no network.

1. `bash tests/scripts/validate-plugin-manifests.sh` → exits 0; all 4 plugin manifests + symlinks valid.
2. `bash tests/scripts/validate-skill-frontmatter.sh` → exits 0; every `SKILL.md` matches the Agent
   Skills spec (name == dir, lowercase/hyphen, ≤64 chars, no reserved words, description 1–1024 chars,
   body ≤500 lines). **These are the same constraints Phase 7 asserts against the live MCP** — if the
   two disagree, that is drift to report.

---

## Phase 1 — Read-only / discovery (safe, no cleanup)

3. `search_entities` for each type: `project`, `dataset`, `prompt`, `experiment`, `agent`,
   `evaluator`, `knowledge`, `memory_store`, `deployment` → each returns without error.
   - **Edge:** `limit=1`, `limit=100` (max), `limit=101` (expect clamp or reject); a fabricated
     `starting_after` cursor → expect empty/typed error, not a 500.
4. `search_directories` → lists project dirs.
5. `search_docs(query="create an evaluator")` → non-empty results (drives in-IDE doc search).
6. `list_models(modelType=chat)` → non-empty; note one function-calling model (e.g. `openai/gpt-4.1-mini`)
   for later phases. Also probe `modelType=embedding` and an invalid type → typed error.
7. `list_traces` → returns array. If non-empty: `list_spans(trace_id=…)` → `get_span(span_id=…, mode=compact)`
   then `mode=full`. If empty, mark SKIP (not FAIL).
8. `get_analytics_overview(period=24h)` → returns snapshot; probe an invalid `period` → typed error.
9. `query_analytics(metric=usage, time_range={start:"7d"})` → returns rows; repeat `metric=cost`,
   `group_by=["model"]`.
10. **Not-found:** `get_agent(key="orq-skills-test-does-not-exist")`,
    `get_span(span_id="bogus")` → each returns a clean not-found error, not a crash.

> **DRIFT VERIFY:** earlier revisions of this doc listed `list_registry_keys` here. It is not in the
> current tool surface. Call it; if it errors as unknown, record DRIFT and drop it.

---

## Phase 2 — Dataset + datapoints (full CRUD, auto-deletable)

11. `create_dataset` → `orq-skills-test-crud-dataset` under the test path.
12. `create_datapoints` → 3 rows (inputs + expected_output).
    - **Edge (string fidelity):** include one row with real newlines, unicode + emoji, and a ~10k-char
      value. Send literal content, NOT `\n` escape sequences — verify it round-trips unchanged in #14.
    - **Edge (batch bounds):** a batch of exactly 100 → accepted; a batch of 101 → rejected (maxItems);
      an empty array → rejected (minItems).
13. `list_datapoints` → verify 3 rows. **Edge:** `limit=1` + follow the cursor to page 2.
14. `update_datapoint` → modify one row; re-list and verify the change (and that the unicode/emoji row
    from #12 is byte-identical).
15. `delete_datapoints` → delete 1; verify 2 remain.
16. `delete_dataset` → delete `orq-skills-test-crud-dataset`.
    - **Known bug:** `delete_dataset` returns an output-validation error despite succeeding. Confirm
      deletion by calling `list_datapoints` on the dead ID and expecting not-found — do NOT trust the
      return value.

---

## Phase 3 — Evaluators (create → read → update → delete)

17. `create_python_eval` → key `orq-skills-test-py-eval`, code that returns `True` when `log['output']`
    is valid JSON.
18. `create_llm_eval` → key `orq-skills-test-llm-eval`, minimal binary Pass/Fail judge prompt using
    `{{log.output}}`. Confirm `{{log.input}}` and `{{log.reference}}` are also accepted.
    - **Edge (referential):** create with a model that does NOT support function calling → expect reject.
    - **Edge (contract):** `guardrail_config.type` that disagrees with `output_type` (e.g. `boolean`
      guardrail on a `number` eval) → expect reject.
19. Read back with `get_python_eval` and `get_llm_eval` → returns stored code / prompt + model.
    - **DRIFT VERIFY:** the skills + several `resources/api-reference.md` files reference `evaluator_get`,
      which the server does NOT expose. The working tools are `get_python_eval` / `get_llm_eval`. Record
      DRIFT if `evaluator_get` is called anywhere and fails.
20. `update_python_eval` (change code) and `update_llm_eval` (change prompt + `output_type`) → read back,
    verify the change.
21. **Teardown:** `delete_entity(type="evaluator", id=<each>)`.
    - **DRIFT VERIFY:** `setup.md` and prior revisions claim evaluators have *no* MCP delete tool and
      require manual cleanup. `delete_entity` now supports `evaluator`. If it succeeds, record DRIFT and
      update the manual-cleanup guidance; if it fails, fall back to listing the IDs for manual cleanup.

---

## Phase 4 — Agents

22. `get_agent(key=orq-skills-test-echo)` → config matches what setup created (model + instructions).
23. `create_agent` → key `orq-skills-test-crud-agent`, model `openai/gpt-4.1-mini`, simple instructions.
    - **Edge (key regex `^[A-Za-z][A-Za-z0-9]*([._-][A-Za-z0-9]+)*$`):** a valid-but-unusual key like
      `orq-skills-test.a_b-c` → accepted; reject `1abc`, `_x`, `has space`, and an emoji key.
    - **Edge (required):** omit `team` → expect reject.
24. `update_agent(key=orq-skills-test-crud-agent)` → change instructions with `versionIncrement=patch`,
    `versionDescription="test bump"`; read back.
    - **Edge (versioning):** call update twice and assert two distinct published versions exist.
    - **Edge:** `versionIncrement="huge"` (invalid enum) and a call missing `versionDescription` → reject.
25. *(Optional, cost)* `invoke_agent(model="agent/orq-skills-test-echo", input="ping")` →
    `retrieve_agent_response` resolves; output echoes input. Skip in zero-cost runs.
26. **Teardown:** `delete_entity(type="agent", id=<crud-agent>)`. Same DRIFT VERIFY as #21 (setup.md
    assumes no agent delete; `delete_entity` now supports `agent`). Double-delete the same ID → expect a
    clean already-gone error (idempotency).

---

## Phase 5 — Deployments (NEW — absent from prior suite)

27. `create_deployment` → key `orq-skills-test-deployment`, a function-calling model, messages using
    `{{variable}}` templating.
    - **Edge (template):** a message with an unbalanced `{{variable` → expect reject or a clearly
      surfaced template error.
    - **Edge (key regex):** reject a leading-digit / spaced key as in #23.
28. `get_deployment(key=orq-skills-test-deployment)` → returns model + messages as created.
29. **Teardown:** `delete_entity(type="deployment", id=<id>)`. (No `update_deployment` tool exists —
    note that as a capability gap, not a failure.)

---

## Phase 6 — Experiments

30. `create_experiment` → key `orq-skills-test-experiment`, referencing the seeded
    `orq-skills-test-dataset` + `orq-skills-test-eval-length`, `auto_run=false`.
    - **Verify:** references resolve (no "dataset/evaluator not found").
    - **Edge (referential):** a second create with a garbage `dataset.id` → expect reject.
31. `list_experiment_runs` → array consistent with #30. **Edge:** `limit` bound + a fabricated cursor.
32. *(Optional, cost — gated)* one `create_experiment(auto_run=true)` over the seeded dataset, then
    `get_experiment_run` exporting each format: `json`, `jsonl`, `csv` → all return a signed download
    URL. Skip in zero-cost runs.
33. **Teardown:** `delete_entity(type="experiment", id=<id>)` (DRIFT VERIFY as #21).

---

## Phase 7 — Skills (NEW — live mirror of the Phase 0 static linter)

The MCP exposes skills as a first-class entity. These cases assert the **server enforces the same
Agent Skills spec** that `validate-skill-frontmatter.sh` enforces statically.

34. `create_skill` → display_name `orq-skills-test-skill`, short description, simple instructions, under
    the test path. (Note: `create_skill` uses `display_name` with pattern
    `^[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*$`, which differs from the lowercase-only file-name rule —
    record any mismatch between the SDK pattern and the spec the static linter enforces.)
35. `get_skill(skill_id=…)` and `list_skills` → the new skill is present with the stored instructions.
36. `update_skill` → change instructions + description; read back, verify.
37. **Spec-enforcement edges (each should be rejected by the server):**
    - description empty → reject (matches static Check 5).
    - description > 1024 chars → reject (matches static Check 4).
    - display_name containing a reserved word (`claude`/`anthropic`) → reject (matches static Check 3b).
    - display_name > 64 chars → reject (matches static Check 3a).
    - instructions body > 500 lines → reject or warn (matches static Check 1).
    - Any edge the server *accepts* that the static linter *rejects* (or vice-versa) = DRIFT to report.
38. **Teardown:** `delete_skill(skill_id=…)`; double-delete → clean already-gone error.

---

## Phase 8 — Teardown + drift register

Delete ONLY this run's resources, in reverse-dependency order, even if an assertion failed earlier:
experiments → evaluators → agents → deployments → skills → datasets. Prefer `delete_entity` /
`delete_skill` / `delete_dataset`; for anything the server refuses to delete, list the ID + key + type
for manual dashboard cleanup. Confirm dataset deletion via `list_datapoints` (the `delete_dataset`
output bug, #16).

**Drift register — verify live and record in the report:**

| # | Suspected drift | Expected current truth |
|---|-----------------|------------------------|
| D1 | `setup.md` / old suite: agents, evaluators, experiments have no MCP delete | `delete_entity` now deletes `agent`, `evaluator`, `experiment`, `deployment`, etc. → teardown can be automatic |
| D2 | `evaluator_get` referenced by skills + api-reference docs | Real tools are `get_python_eval` / `get_llm_eval` |
| D3 | `list_registry_keys` listed as a read-only tool | Not in current tool surface — confirm and drop |
| D4 | Deployments and Skills absent from prior suite | Both are MCP entities now (Phases 5 & 7) |
| D5 | Skill name spec mismatch | Static linter requires lowercase `^[a-z0-9][a-z0-9-]*$`; `create_skill` SDK pattern allows mixed case `^[A-Za-z0-9]...` — reconcile |

---

## Critical Files

- orq MCP server (external dependency) — `https://my.orq.ai/v2/mcp`
- [`tests/scripts/validate-skill-frontmatter.sh`](scripts/validate-skill-frontmatter.sh) — Phase 0 static spec check
- [`tests/scripts/validate-plugin-manifests.sh`](scripts/validate-plugin-manifests.sh) — Phase 0 manifest check
- [`tests/setup.md`](setup.md) — seed data, isolation rules, teardown
