---
name: orq-shared
description: >
  Reference bundle shared by the other orq skills — the verified `orq` CLI trace
  query contract and the run-key preflight that resolves an agent or deployment
  key before any call. Read the file another skill points you at; do not invoke
  this skill on its own. Use when a skill tells you to read
  `../orq-shared/resources/<file>.md`, or when you need the CLI trace filter,
  projection, and write-path rules verbatim. Do NOT use to run CLI commands (use
  orq-cli), to analyze traces (use orq-analyze-traces), or to fix an agent (use
  orq-improve-agent).
allowed-tools: Read, Grep, Glob
---

# orq Shared Reference

This skill holds no procedure. It exists so the references several other skills
depend on ship with them.

Skills install as sibling directories, so a link to
`../orq-shared/resources/<file>.md` resolves after `npx skills add`, after a
plugin install, and in a repo checkout. A link out of the skill tree
(`../../docs/...`) resolves only in a checkout — every other install copies the
skill folder and nothing else.

## Contents

| File | What it holds | Skills that require it |
|------|---------------|------------------------|
| [`resources/trace-queries.md`](resources/trace-queries.md) | The verified `orq` CLI trace contract: runtime field resolution, the layered read, invocation details that fail loudly or silently, projections, where config knobs live, `reporting query`, what does not work, and the `orq agents update` write path with its parameter bounds. | `orq-analyze-traces`, `orq-improve-agent` |
| [`resources/run-key-preflight.md`](resources/run-key-preflight.md) | Resolving and verifying an agent or deployment key with the run key before invoking or sweeping, the `ORQ_API_KEY` export pattern, the MCP-vs-run-key caveat, and HTTP 000. | `evaluatorq`, `orq-analyze-traces`, `orq-compare-agents`, `orq-improve-agent`, `orq-invoke-deployment`, `orq-red-team`, `orq-simulate-agent` |
| [`resources/doc-resolution.md`](resources/doc-resolution.md) | The lookup order when a platform detail is needed: live queries, then the documentation MCP, then docs.orq.ai, then the skill file — and which source wins on a conflict. | `orq-analyze-traces`, `orq-build-agent`, `orq-build-evaluator`, `orq-generate-synthetic-dataset`, `orq-improve-agent`, `orq-run-experiment` |

## Rules for editing these files

- Correct a rule here once. A skill that restates a rule instead of linking it
  will drift; keep the restatements in skills down to the fallback minimum each
  one already declares.
- `resources/trace-queries.md` requires every capability claim to carry the
  command that produced it. Honour that when adding one, or mark it `untested`.
- The canonical source for CLI behaviour outside these two files is
  [`orq-cli`](../orq-cli/SKILL.md). Fix a CLI rule there first, then mirror only
  what a trace query actually needs.
