# Skill-Tester Behavioural Report

- Date: 2026-06-04T09:40:36.603Z
- Skills: build-evaluator
- Repeat: 1× per case

## Summary

- Total cases: 4
- Passed: 2
- Failed: 2

## Behavioural

| Scenario | Type | Routing | Usage | Overall | Score | Evidence |
|----------|------|---------|-------|---------|-------|----------|
| build-evaluator/B1 | explicit | pass | pass | PASS | 100 | tools: Read, mcp__orq-workspace__create_python_eval |
| build-evaluator/B2 | explicit | pass | fail | FAIL | 50 | FAIL forbidden-text:0: matched {"op":"regex","value":"(?i)likert\|\\b1-?5\\b\|\\b1-?10\\b\ |
| build-evaluator/B3 | implicit | pass | fail | FAIL | 67 | FAIL tool:create_python_eval: missing |
| build-evaluator/N1 | negative | pass | n/a | PASS | 100 | tools: (none) |

## Failure detail

### build-evaluator/B2 — explicit
- selected_skill: `build-evaluator` | expected: `build-evaluator`
- tool_calls: Read, AskUserQuestion
- ✗ `forbidden-text:0` — matched {"op":"regex","value":"(?i)likert|\\b1-?5\\b|\\b1-?10\\b|bertscore|rouge"}

### build-evaluator/B3 — implicit
- selected_skill: `build-evaluator` | expected: `build-evaluator`
- tool_calls: Read, AskUserQuestion
- ✗ `tool:create_python_eval` — missing

## Non-gating prose notes (not scored)

These soft/methodology assertions are recorded from the catalog but do not affect pass/fail.

- **build-evaluator/B1**
  - explains code checks are cheaper/deterministic than an LLM judge for this
- **build-evaluator/B2**
  - suggests splitting into separate evaluators (one criterion each)
  - defaults to binary Pass/Fail; mentions validating against human labels (TPR/TNR)
- **build-evaluator/B3**
  - routes here on the description alone; recommends a code-based check for JSON validity
- **build-evaluator/N1**
  - answers as ordinary coding; does not invoke build-evaluator or talk about TPR/TNR judges
