---
name: skill-tester
description: >
  Maintainer/dev skill that tests the orq.ai skills in this repo. Runs two
  evaluation tracks — FUNCTIONAL (exercise each skill's real operations live
  against the orq.ai workspace and verify they work / match the latest release)
  and BEHAVIOURAL (spawn Claude Code subagents with the full skill menu and judge
  whether they route to and use the right skill correctly). Use when validating
  skills before release, after editing a skill, or to catch drift between what a
  skill documents and what the platform actually exposes. Dev-only — NOT a
  user-facing orq.ai skill, NOT registered in agents/AGENTS.md or the README.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, Task, AskUserQuestion, orq*
---

# Skill Tester

You are the **orq.ai skills test runner**. Your job is to validate the skills in this
repository two complementary ways and produce a single report. This skill is **maintainer
tooling**, not part of the shipped product — it tests the *other* skills.

> Scope (v1): validates the skills on the **current working tree / branch** only. PR mode
> and CI are designed-for follow-ups (see [Next steps](#next-steps)).

## Two evaluation tracks

| Track | Question it answers | Mechanism |
|-------|---------------------|-----------|
| **Functional** (factual) | Do the operations a skill prescribes actually work against the latest release? Do the endpoints, MCP tools, models, SDK imports, and code patterns it names exist and behave as claimed? | **Default: the deterministic headless runner** ([`runner/`](runner/), `node run.mjs --track functional --skills <name>\|all`). Connects to the orq MCP server and runs each `catalog/<skill>.functional.yaml` case for real (create → read-back → cleanup) under an isolated path, grading **PASS / FAIL / DRIFT / SKIP** deterministically. A referenced-but-nonexistent tool fails its `tools/list` check → **drift** surfaces naturally. Seeds + tears down its own resources. The agent-driven flow below is the conceptual model / no-CLI fallback. |
| **Behavioural** | Does an agent route to the right skill and use it within its constraints — and stay away when it shouldn't? | **Default: the deterministic headless runner** ([`runner/`](runner/), `node run.mjs --skills <name>\|all`). One headless `claude -p --output-format stream-json` per scenario, **seeded with the full skill menu** (`agents/AGENTS.md`, all 9 skills) and forced to `Read` the matching `SKILL.md` itself. It captures **ground-truth tool-call events** and grades routing + tool calls + response-text patterns **deterministically — no LLM judge**. Scenarios span four **invocation types** (explicit / implicit / contextual / **negative control**) and emit a verdict ([`resources/verdict.schema.json`](resources/verdict.schema.json)). |

The per-skill test specs live in [`catalog/`](catalog/) — prose `<skill>.md` (functional cases +
behavioural scenarios + expected outputs, human-readable) plus a machine-readable
`<skill>.cases.yaml` consumed by the headless runner. Keep both updated when a skill changes; the
catalog is the authoritative "what to test and what's correct" source.

## Modes

Invoke with a mode (default `all`) and optional skill scope:

- `all` — run both tracks for every skill (default).
- `factual` — functional track only.
- `behavioural` — behavioural track only.
- `--skill <name>` — restrict to one skill (e.g. `factual --skill build-evaluator`).

## How to run

### 0. Preflight (always)

Run the **Setup** section of [`../setup.md`](../setup.md):
- Verify `$ORQ_API_KEY` is set and the orq MCP is reachable (`search_entities(type=agent)`).
- Discover a project via `search_entities(type=project)` and use `{project}/orq-skills-tests` as the
  isolated test path.
- Seed shared test data (`orq-skills-test-echo` agent, `orq-skills-test-dataset`,
  `orq-skills-test-eval-length`). Track every created ID for teardown.

If `$ORQ_API_KEY` or network is unavailable: mark the **functional** track **SKIPPED** (not FAILED)
and continue with the behavioural track, which needs no live workspace.

### 1. Functional track

**Default path — run the headless runner:**

```bash
cd tests/skill-tester/runner && npm ci
node run.mjs --track functional --skills all     # or --skills build-evaluator,run-experiment
```

It connects to the orq MCP server, seeds shared resources under the isolated path, runs every
`catalog/<skill>.functional.yaml` case for real, grades PASS / FAIL / DRIFT / SKIP deterministically,
tears down what it created, writes `functional-report.md`, and exits non-zero on failure/drift. See
[`runner/README.md`](runner/README.md). The agent-driven flow below is the conceptual model / no-CLI
fallback.

Follow [`resources/functional-runner.md`](resources/functional-runner.md). For each in-scope skill,
execute the **Functional cases** from its catalog file live, under the isolated path, and record
PASS / FAIL / SKIP with the **actual output** and any drift (e.g. a documented tool name the server
does not expose). Reuse the shared seed resources instead of creating extras.

### 2. Behavioural track

**Default path — run the headless runner:**

```bash
cd tests/skill-tester/runner && npm ci
node run.mjs --skills all          # or --skills build-evaluator,run-experiment
```

It executes every `catalog/<skill>.cases.yaml` scenario as a headless `claude -p` run, captures the
real tool-call stream, grades deterministically, writes a report, and exits non-zero on failure. See
[`runner/README.md`](runner/README.md). The agent-driven flow below is the conceptual model / no-CLI
fallback.

Follow [`resources/behavioural-runner.md`](resources/behavioural-runner.md). For each **Behavioural
scenario** in the catalog, spawn a subagent with the full skill menu, issue the trigger prompt,
capture the **selected skill + tool-call trace + response**, and judge it against the scenario's
PASS / anti-pattern assertions. Grade **routing** (did it pick the right skill among all 9 — or, for
**negative controls**, correctly *not* fire?) and **usage** (did it respect the skill's
constraints?). Each scenario is tagged with an invocation type and produces a structured verdict.

### 3. Teardown + report

Run the **Teardown** section of [`../setup.md`](../setup.md) — delete ONLY resources created this
run. Then write `../test-report.md` using [`resources/report-template.md`](resources/report-template.md):
per-skill, per-track PASS/FAIL/SKIP, failing output, drift found, and cleanup status (auto-deleted
vs. manual-cleanup IDs).

## Safety

Inherits every rule in [`../setup.md`](../setup.md):
- **NEVER** modify, update, or delete any pre-existing workspace resource.
- All test resources created under `{project}/orq-skills-tests`, keys prefixed `orq-skills-test-`.
- Teardown removes only this run's resources. Agents, evaluators, experiments, and deployments delete
  via `delete_entity`; datasets via `delete_dataset`; skills via `delete_skill` — teardown is
  automatic (fall back to listing IDs only if a delete is refused).
- Keep created resource counts minimal; reuse the seeded echo agent / eval across cases.

## Done when

- Both in-scope tracks have run (or are explicitly SKIPPED with a reason).
- `../test-report.md` is written with per-skill, per-track results and a cleanup section.
- Teardown left no orphaned auto-deletable resources; manual-cleanup IDs are listed.

## Next steps

Designed-for, not built in v1:

- **PR testing** — list/checkout each open PR with `gh`, generate or update catalog cases for what
  the PR changed, run both tracks against the PR branch, and report per-PR.
- **CI** — a `.github/workflows` job that invokes this runner (functional track needs `ORQ_API_KEY`
  as a secret; behavioural track can run without it).