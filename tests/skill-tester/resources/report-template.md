# Report Template

Write the result of a run to `tests/test-report.md`. Extends the report skeleton in
[`../../setup.md`](../../setup.md) with per-skill, per-track detail. Behavioural results are backed by
structured verdicts conforming to [`verdict.schema.json`](verdict.schema.json).

```markdown
# Skill Test Report: orq-skills

- **Date:** {ISO date}
- **Branch / commit:** {git branch} @ {short sha}
- **Mode:** {all | factual | behavioural}  ·  **Scope:** {all skills | <skill>}
- **Workspace project (test path):** {project}/orq-skills-tests

## Summary

| Track | Pass | Fail | Drift | Skip |
|-------|------|------|-------|------|
| Functional   | N | N | N | N |
| Behavioural  | N | N | — | N |

Behavioural false-positive guard: **{X}/{Y} negative controls held** (skill correctly did NOT fire).

## Per-skill results

| Skill | Functional | Behavioural (routing / usage) | Neg. controls | Notes |
|-------|-----------|-------------------------------|---------------|-------|
| analyze-trace-failures      | ✅/❌/⏭ | ✅/❌ · ✅/❌ | ✅/❌ | … |
| build-agent                 | … | … | … | … |
| build-evaluator             | … | … | … | … |
| compare-agents              | … | … | … | … |
| generate-synthetic-dataset  | … | … | … | … |
| invoke-deployment           | … | … | … | … |
| optimize-prompt             | … | … | … | … |
| run-experiment              | … | … | … | … |
| setup-observability         | … | … | … | … |

## Functional

For each FAIL/DRIFT, give the skill, the case, what was attempted, and the actual result.

- **{skill} — {case}:** {PASS/FAIL/DRIFT/SKIP}. {actual output or error}. {if DRIFT: "skill documents
  `X`; server exposes `Y`".}

## Behavioural

One row per scenario, from its `verdict.schema.json` object. Include negative controls — a fired
negative control is a routing FAIL.

| Scenario | Type | Expected | Selected | Routing | Usage | Score | Failed checks / evidence |
|----------|------|----------|----------|---------|-------|-------|--------------------------|
| {skill}/B1 | explicit | {skill} | {selected} | ✅/❌ | ✅/❌/n/a | 0–100 | {check ids + quote} |
| {skill}/N1 | negative | (not {skill}) | {selected\|none} | ✅/❌ | n/a | … | … |

For each FAIL, note whether a re-run was flaky.

## Drift found (skill ⇄ platform)

Bullet list of every documented entity that no longer matches the platform (tool/endpoint/model/import),
with the skill file + line and the correct value. This is the actionable fix list.

## Cleanup status

- **Deleted via MCP:** {confirm each deletion — datasets via `delete_dataset`; agents / evaluators /
  experiments / deployments via `delete_entity`; skills via `delete_skill`}
- **Manual fallback (only if a delete was refused):** list `{type} {key} {id}` so it can be removed
  from the orq.ai dashboard.
```
