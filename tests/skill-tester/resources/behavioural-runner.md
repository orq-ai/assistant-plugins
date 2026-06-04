# Behavioural Runner

How to run the **behavioural** track: verify that an agent, given a realistic prompt and the **full
skill menu**, routes to the correct skill (or correctly stays away from it) and uses it within its
constraints. Routing is part of the test — the subagent is never told which skill to use.

> **Default implementation: the deterministic headless runner.** Prefer
> [`../runner/`](../runner/) (`node run.mjs --skills <name>|all`). It spawns one headless
> `claude -p --output-format stream-json` per case, captures the **real** tool-call events, and
> grades them **deterministically** (no LLM judge) against the YAML gold-truth specs in
> [`../catalog/<skill>.cases.yaml`](../catalog/). The agent-driven, self-reported flow below remains
> the conceptual model and the fallback when the harness can't run; the runner supersedes its
> capture/judge mechanics. See [`../runner/README.md`](../runner/README.md).

## Key principle: full menu, not a hint

Each scenario is run by a fresh **Claude Code subagent** (via the `Task`/Agent tool) that is given
the complete skill catalog — `agents/AGENTS.md` (which lists all 9 skills, their descriptions, and
their `SKILL.md` paths) — and must **decide for itself** which skill matches and read that
`SKILL.md`. Handing it a single pre-selected skill would defeat the purpose: choosing the wrong
skill among all available ones is a real failure this track must catch.

## Invocation types (every scenario is tagged)

Coverage across four types tests routing quality, not just happy-path usage:

| Type | What it tests | Expectation |
|------|---------------|-------------|
| **explicit** | Prompt names the skill or its artifact ("build an evaluator") | Skill fires |
| **implicit** | Prompt describes only the *need*, never the skill — tests whether the `description` field alone is a strong enough routing signal | Skill fires |
| **contextual** | Implicit + noisy domain context that could mislead | Correct skill still fires |
| **negative** (control) | An adjacent request that should **not** fire this skill (catches over-eager triggering / false positives) | Skill does **not** fire (a different skill, or none, is correct) |

Each catalog file must carry at least one **implicit** scenario and at least one **negative
control**. A skill that fires on its negative control is failing as badly as one that misses its
explicit case.

## What to capture: the trace, not just the answer

Score *what actually happened*, not only the final text. For each run, capture:
1. **selected_skill** — which `SKILL.md` the subagent chose to read / act on (instruct it to state
   this explicitly).
2. **tool_calls** — the ordered list of operations/tools it used. The headless runner reads these as
   **ground-truth `tool_use` events** from `claude -p --output-format stream-json` — including write
   tools, which are auto-denied (`--permission-mode dontAsk`) but still emit their `tool_use` block,
   so intent is captured with zero side effects. (In the agent-driven fallback there is no event
   stream, so the subagent must instead **self-report** a closing JSON block
   `{ "selected_skill": ..., "tool_calls": [...] }` — treat that as a claim, not ground truth.)
   Routing itself is a `tool_call`: which `skills/<name>/SKILL.md` the agent `Read`.
3. **final response** — the substantive answer.

Process assertions (e.g. "calls `list_models` when selecting a model") are graded against
`tool_calls`, not against prose claims in the answer.

## Procedure (per scenario)

1. **Spawn a candidate subagent** (`subagent_type: general-purpose`). Seed its prompt with:
   - The full content of `agents/AGENTS.md` (the `<available_skills>` block + the instruction to
     read the matching `SKILL.md` when a description fits the user intent).
   - Framing: *"You are the orq.ai workspace assistant. Skills live under `skills/<name>/SKILL.md`
     relative to the repo root. Respond to the following user message as you normally would. If no
     skill fits, answer normally and do not force one. End your reply with a JSON block:
     `{\"selected_skill\": <name|null>, \"tool_calls\": [<ordered ops>]}`."*
   - The scenario's **trigger prompt** verbatim + any **Provide** context.
   - Instruction to **stop after the first substantive response** and **not create live resources**
     (this track tests reasoning/usage, not side effects; the functional track owns live execution).
2. **Capture** selected_skill, tool_calls, and the final response.
3. **Judge** with a separate step (judge subagent or inline). Give the judge:
   - the scenario's **type**, **expected routing** (correct skill, or "none/other" for negatives),
   - its **PASS assertions** and **anti-pattern (FAIL) assertions**, and
   - the captured selected_skill + tool_calls + response.
   The judge emits one object conforming to [`verdict.schema.json`](verdict.schema.json):
   - **routing** — `pass` if `selected_skill` matches expectation. For a **negative control**,
     `pass` means the skill under test did **not** fire.
   - **usage** — `pass` only if every PASS assertion holds and no anti-pattern is violated; `n/a`
     for negative controls.
   - **checks[]** — one `{id, pass, notes}` per assertion (stable ids so results diff across runs).
   - **overall_pass**, **score** (0–100), **evidence** (quote), **flaky**.

## Batching

If seeding many scenarios into separate subagents is heavy, batch several scenarios for the **same**
skill into one subagent run — but always keep the **full** skill menu in context so routing remains
a genuine choice. If the catalog grows large enough that context is a problem, the tester can be
split into a few sub-skills (e.g. one per lifecycle stage); keep the runner mechanism identical.

## Rules

- **No live writes** in the behavioural track. If correct behaviour would create a resource, the
  subagent *describes/generates* the call, not executes it. (Live execution is the functional track.)
- **Judge on evidence.** Mark a check FAIL only against a concrete catalog assertion, quoting the
  transcript or a `tool_calls` entry.
- **Determinism caveat.** Behavioural results vary run-to-run; on a FAIL, re-run once before
  reporting to distinguish a real defect from a flaky sample, and set `flaky` accordingly.
- **Living record.** Every real routing/usage failure you find should become a new catalog scenario
  (often a new negative control), so the suite grows from observed failures.

## Output

Hand the per-scenario verdict objects to the report step. Use the `## Behavioural` section of
[`report-template.md`](report-template.md).
