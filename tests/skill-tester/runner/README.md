# Skill-tester harness (deterministic, headless)

A Node harness that validates the orq.ai skills two ways, both graded by **pure
code** — no LLM judge:

- **Behavioural** (default) — grades **skill routing + tool usage** by spawning one
  headless `claude -p` per case and parsing the **real tool-call event stream**
  against a YAML gold-truth spec. No workspace writes.
- **Functional** — exercises each skill's **real MCP operations** live against the
  orq.ai workspace (create → read-back → cleanup), verifies the results, and
  detects **drift** (a documented tool the server no longer exposes). Mutates the
  workspace under an isolated path and cleans up after itself; requires `ORQ_API_KEY`.

These are the headless/CI implementation of the two tracks described in
[`../resources/behavioural-runner.md`](../resources/behavioural-runner.md) and
[`../resources/functional-runner.md`](../resources/functional-runner.md).

## Quick start

```bash
cd tests/skill-tester/runner
npm ci                        # deps: yaml, @modelcontextprotocol/sdk
export ORQ_API_KEY=...         # required for functional; behavioural grades without it (read tools just error)

node run.mjs --skills build-evaluator                 # behavioural (default), one skill
node run.mjs --skills all --concurrency 6             # behavioural, everything
node run.mjs --track functional --skills build-evaluator   # functional, one skill
node run.mjs --track all --skills all                 # both tracks
node run.mjs --list                                   # list available specs
```

Exit code is non-zero if any gating check failed (CI gate). Reports are written to
`last-report.md` (behavioural) and `functional-report.md` (functional); `--out`
overrides the destination for a single-track run.

### Flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--track <t>` | `behavioural` | `behavioural` \| `functional` \| `all` |
| `--skills a,b,c` / `all` | `all` | which catalog specs to run (`<skill>.cases.yaml` / `<skill>.functional.yaml`) |
| `--concurrency N` | `4` | behavioural: cases in parallel within a skill (functional runs sequentially) |
| `--repeat N` | `1` | behavioural: re-run each case N×; sets `flaky` when results disagree |
| `--out <path>` | per-track default | report destination (single-track runs only) |
| `--timeout <ms>` | `180000` | behavioural: per-case kill deadline |
| `--keep-temp` | off | behavioural: keep the temp system-prompt/settings dir |
| `--debug` | off | print each case's prompt + response (behavioural) or MCP request + response (functional) to stderr |
| `CLAUDE_BIN` (env) | `claude` | override the claude binary path (behavioural) |

The behavioural report always includes a collapsed **Model responses** section
with each case's full final answer; the functional report includes a collapsed
**Tool calls & responses** section. `--debug` additionally streams these to stderr
during the run — pair it with `--concurrency 1` so the per-case blocks don't
interleave.

The markdown report always includes a collapsed **Model responses** section with
each case's full final answer. `--debug` additionally streams the prompt and
response to stderr during the run — pair it with `--concurrency 1` so the
per-case blocks don't interleave.

## How it works

Each case spawns (cwd = repo root, prompt fed via **stdin**):

```
claude -p
  --append-system-prompt-file <AGENTS.md + routing framing>
  --strict-mcp-config --mcp-config .mcp.json
  --disallowedTools Skill              # force routing to surface as a Read of SKILL.md
  --allowedTools <read-only allowlist> # reads execute; writes are denied
  --permission-mode dontAsk            # non-interactive auto-deny, never hangs
  --settings <{"disableAllHooks":true}>
  --output-format stream-json --verbose
```

Three deterministic signals are extracted from the stream and graded:

1. **Routing** — which `skills/<name>/SKILL.md` the agent `Read`. Negative
   controls assert the skill-under-test was **not** read.
2. **Tool calls** — `mcp__orq-workspace__*` and `Read/Grep/Glob` events, matched
   by name + optional arg matchers + ordering. Write tools (`create_*`,
   `update_*`, `delete_*`, `invoke_*`) are **auto-denied but still emit their
   `tool_use` block**, so we capture intent with **zero side effects**.
3. **Response text** — code-generation patterns that live in the answer
   (`from orq_ai_sdk import Orq`, `base_url=".../v2/router"`), matched by
   regex/substring, plus `forbidden_*` anti-patterns.

Soft/methodology assertions that a trace can't verify are carried as non-gating
`notes` (recorded in the report, never pass/fail). See the plan for the rationale.

## Spec format (`catalog/<skill>.cases.yaml`)

```yaml
skill: build-evaluator
skill_path: skills/build-evaluator/SKILL.md
cases:
  - id: B1
    type: explicit            # explicit | implicit | contextual | negative
    prompt: "Build an evaluator that checks if the output is valid JSON."
    context: |                # optional; appended to the user turn (e.g. a code snippet)
    expect:
      routing: build-evaluator        # SKILL.md that must be Read (omit for pure negatives)
      order: any                      # any (default) | subsequence | strict
      tool_calls:
        - tool: create_python_eval    # short alias → mcp__orq-workspace__create_python_eval
          args: { code: { op: contains, value: "json" } }
      text:            [ { op: regex, value: "create_python_eval" } ]
      forbidden_tools: [ create_llm_eval ]
      forbidden_text:  [ { op: regex, value: "(?i)likert" } ]
    notes:                            # non-gating prose, informational only
      - "explains code checks are cheaper than an LLM judge"
  - id: N1
    type: negative
    prompt: "Write me a Python function that parses a JSON string."
    expect:
      not_routing: build-evaluator    # this skill must NOT fire
```

**Matchers** (`{ op, value }`, or a bare string = `contains`): `exact`,
`contains`, `regex`, `exists`, `oneOf`. Tool names may be the short alias or the
full `mcp__orq-workspace__` name — they're normalised before comparison.

## Functional track

The functional runner connects to the orq MCP server from `.mcp.json`
(streamable HTTP, `Bearer $ORQ_API_KEY`) via the official MCP SDK and executes
each case's steps for real. On startup it **discovers a project** and seeds three
shared resources under `{project}/orq-skills-tests` — an echo agent
(`orq-skills-test-echo`), a dataset (`orq-skills-test-dataset` + 5 datapoints),
and a length eval (`orq-skills-test-eval-length`). Pre-existing resources are
**reused, never deleted**; only resources this run created are torn down in a
`finally` (so the workspace is left clean even on failure).

Each case is graded **PASS / FAIL / DRIFT / SKIP**:
- **FAIL** — a step errored or a `verify` matcher didn't hold.
- **DRIFT** — a `call:` names a tool the server doesn't expose, or a `drift:`
  expectation about the live tool list was violated (e.g. a skill documents
  `evaluator_get` but the server only has `get_python_eval`).
- **SKIP** — a `skip:` case (e.g. an AI-router HTTP call or a Python-SDK check
  that isn't an MCP operation).

### Spec format (`catalog/<skill>.functional.yaml`)

```yaml
skill: build-evaluator
cases:
  - id: F1
    title: Create + read back a Python evaluator
    steps:
      - call: create_python_eval                 # an orq MCP tool name
        args: { key: orq-skills-test-py-eval, path: "{{testPath}}", output_type: boolean, code: "..." }
      - call: get_python_eval
        args: { key: orq-skills-test-py-eval }
        save: { evalId: id }                      # dot-path into the parsed response → {{evalId}}
        verify:
          - { path: code, op: contains, value: "json.loads" }   # reuses the behavioural matchers
    cleanup:
      - { call: delete_entity, args: { type: evaluator, id: "{{evalId}}" } }
    drift:
      - { tool: evaluator_get, expect: absent }   # absent | present, checked against tools/list
  - id: F2
    title: AI Router HTTP completion
    skip: "AI-router HTTP call, not an MCP tool — out of scope for the MCP runner"
```

- `{{var}}` interpolation resolves `testPath`, the seeded ids
  (`echoAgentKey`, `echoAgentId`, `datasetId`, `lengthEvalId`), and any `save`d value.
- `verify` reuses the same `{ op, value }` matchers as the behavioural track,
  applied to a dot/bracket path into the parsed tool response (`path: ""` = whole
  response; handy with `op: exists`).
- `cleanup` steps run after the case (best-effort); seed teardown also sweeps
  every id this run created.

## Notes & limits

- **No `--bare`**: it would force `ANTHROPIC_API_KEY`-only auth and break OAuth
  logins. Isolation is instead via `--strict-mcp-config` + disallowed `Skill`.
- **Determinism caveat**: the agent under test still varies run-to-run. Use
  `--repeat` to surface flakiness; the catalog is a *living record* — when a real
  routing/usage failure appears, add it as a new case (often a negative control).
- **Windows**: spawns through the shell with quoted args (Node refuses to spawn a
  `.cmd` with `shell:false`). Set `CLAUDE_BIN` if `claude` isn't on `PATH`.
