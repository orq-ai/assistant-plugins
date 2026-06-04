# Functional Runner

> **Default path is the headless runner** — `cd tests/skill-tester/runner && node run.mjs --track
> functional --skills <name>|all`. It executes the machine-readable `catalog/<skill>.functional.yaml`
> specs against the orq MCP server, grades PASS/FAIL/DRIFT/SKIP deterministically, and seeds + tears
> down its own resources. See [`../runner/README.md`](../runner/README.md). The prose below is the
> conceptual model and the no-CLI fallback for cases the runner marks SKIP (HTTP router, Python-SDK
> imports, framework codegen).

How to run the **functional (factual)** track: exercise each skill's real operations live and
verify they work against the latest release. This is integration-style validation — *do what the
skill says and check the result* — not static name-matching. When a skill references an endpoint,
MCP tool, model, or import that no longer exists, the live call fails and the run records it as
**drift**.

## Preconditions

- Preflight from [`../../setup.md`](../../setup.md) has run: `$ORQ_API_KEY` set, MCP reachable,
  isolated path `{project}/orq-skills-tests` chosen, shared seed data created:
  - `orq-skills-test-echo` — agent, `openai/gpt-4.1-mini`, instructions "Echo back the user's message verbatim"
  - `orq-skills-test-dataset` — dataset with 5 datapoints
  - `orq-skills-test-eval-length` — python eval checking `len(log['output']) > 0`
- If preflight could not run (no key/network): record the whole functional track as **SKIPPED** and stop.

## Procedure (per in-scope skill)

1. Open the skill's catalog file (`../catalog/<skill>.md`) and read its **Functional cases**.
2. For each case, perform the operation **exactly as the skill prescribes** (MCP tool, HTTP
   endpoint, SDK call, or generated-code pattern), using only `orq-skills-test-`–prefixed inputs
   under the isolated path. **Reuse** the seeded echo agent / dataset / eval rather than creating new
   resources where a case allows it.
3. Apply the case's **Verify** step — verification differs per skill (read-back, completion
   returned, import succeeds, array non-empty, etc.).
4. Record one of:
   - **PASS** — operation succeeded and verification held. Note the actual output briefly.
   - **FAIL** — operation errored or verification failed. Capture the error / mismatched output.
   - **DRIFT** (a FAIL subtype) — the skill documents an entity (tool/endpoint/model/import) that
     does not exist or has been renamed. Record both what the skill says and what actually works.
   - **SKIP** — case needs something unavailable (e.g. an external framework, a real deployment).
     State why.
5. Track every created resource ID for teardown.

## Rules

- **Idempotent + isolated.** Never touch pre-existing resources. Everything you create is prefixed
  and under the test path; teardown removes only what this run created.
- **Minimise created resources, then delete them.** Agents, evaluators, experiments, and deployments
  delete via `delete_entity` (datasets via `delete_dataset`, skills via `delete_skill`) — prefer
  reusing the seeded ones, and delete anything a case creates during teardown (fall back to logging
  the ID only if a delete is refused).
- **Authoritative source order** when a result is ambiguous: live MCP/API response → orq.ai docs →
  the skill file. If the skill conflicts with live behaviour, the skill is wrong → record as DRIFT.
- **Don't fix the skill here.** This runner only detects and reports. Fixes are a separate change.

## Output

Hand the per-skill case results (status + actual output + drift notes + created IDs) to the report
step. Use the `## Functional` section of [`report-template.md`](report-template.md).