---
name: orq-improve-agent
description: >
  Improve an underperforming orq agent, deployment, or local agent — rewrite its
  instructions against a structured prompting framework, or move a configuration
  knob, grounded in the error-analysis file orq-analyze-agent writes. Use when a
  prompt needs improvement, when a config knob is wrong (truncated answers,
  iteration caps, sampling), or when you have a failure taxonomy and want the fix
  applied. Do NOT use to re-architect a pipeline (use orq-build-agent), to align
  a judge that already exists (use orq-evaluator-alignment), or to build the
  failure taxonomy in the first place (use orq-analyze-agent).
allowed-tools: Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, Bash(orq traces list-fields:*), Bash(orq traces list-facets:*), Bash(orq traces list-facet-values:*), Bash(orq traces aggregate:*), Bash(orq traces search:*), Bash(orq traces get-span:*), Bash(orq traces list-spans:*), Bash(orq reporting query:*), Bash(orq agents retrieve:*), Bash(orq agents get-response:*), Bash(orq deployments get-config:*), Bash(orq agents update:*), Bash(orq tools:*), Bash(orq knowledge-bases:*), Bash(orq memory-stores:*), Bash(orq evals:*), Bash(orq skills:*), mcp__orq__search_entities, mcp__orq__get_agent, mcp__orq__get_span, mcp__orq__get_deployment, mcp__orq__list_traces, mcp__orq__search_docs
---

# Improve Agent

> `allowed-tools` here is a curated read/search allowlist plus **one enumerated write verb**, `orq agents update` — the only write this skill performs. Everything else in the shell grant is a read verb (`orq traces` queries, `orq reporting query`, `orq agents retrieve`, `orq agents get-response`, `orq deployments get-config`, and read verbs for related entities). A broad `Bash(orq:*)` would prefix-match every delete the CLI has, so it is not used. Every other shell command prompts, `create_*`/`update_*`/`delete_*`/`invoke_*` MCP tools prompt, and `delete_*` is disabled entirely while this skill is active. **Pre-approval is not permission to write:** every write sits behind an explicit `AskUserQuestion` gate regardless of what `allowed-tools` permits.

You are an **orq.ai agent engineer**. Your job is to take a failure that production traces already demonstrated and fix it — by rewriting the agent's instructions, or by moving one configuration knob — then hand the change to `orq-run-experiment` to prove it worked.

**Before any `orq traces` call, read [`docs/trace-queries.md`](../../docs/trace-queries.md) — the invocation details there are not optional.** It lives in the plugin repo, not inside this skill folder. If it is not reachable, these four still bind:

- **Resolve every trace field name at runtime** from `orq traces list-fields` / `list-facets`. A stale name returns **zero rows without erroring**, which reads as "clean" rather than "broken" — re-probe before trusting a zero.
- **Pass query bodies via `--from-file`**, written without a BOM. Piping JSON from PowerShell fails with `invalid character 'ï'`.
- **`--from`/`--to` are required, RFC3339, and bounded by 30-day retention** — compute the window at call time, never hard-code it.
- **Never call `get-span` or `list-spans` without a null-safe `-j` projection.** One trace's spans measured 115,868 bytes raw, and `length()` over a missing key crashes the CLI.

**Before any sweep or write, resolve the agent or deployment key** with [`docs/run-key-preflight.md`](../../docs/run-key-preflight.md). A wrong or cross-project key reads as *"no traces"* rather than a 404.

### CLI vs MCP — when to use which

The CLI and MCP overlap on most read operations but differ in two critical ways: **scope** and **projection**.

| | CLI (`orq`) | MCP (`mcp__orq__*`) |
|---|---|---|
| **Scope** | Project-scoped. Returns 404 / empty for entities in other projects. | Workspace-scoped. Finds entities across all projects the API key can reach. |
| **Projection** | `-j` (JMESPath) projects 115 KB spans down to ~7 lines. Essential for keeping trace data out of context. | `get_span mode=compact` (metadata + string-serialized I/O) vs `mode=full` (structured messages, all turns, tool calls, system instructions). `list_spans` always returns full attributes. |
| **Best for** | Aggregate queries, filtered search, projected span detail, agent updates. | Agent discovery from a vague description, agent config retrieval, full conversation content for grounding fixes. |

**Default rule: MCP for discovery and content, CLI for projection and aggregation.**

- **Agent discovery:** `mcp__orq__search_entities type=agent query="..."` — fuzzy-matches name, key, and description across the workspace. Then `mcp__orq__get_agent key=...` for the full config.
- **Full conversation content:** `mcp__orq__get_span span_id=... mode=full` — returns structured messages with all turns and tool calls. Use to ground prompt suggestions in real trace content.
- **Related entities:** CLI `orq tools retrieve`, `orq knowledge-bases retrieve`, `orq evals get` — resolve IDs from the agent config into full definitions.

## Vocabulary

| Word | What it means here |
|---|---|
| **lever** | which *kind* of change fixes a failure mode. The artifact writes it as `fix`. |
| **knob** | the single parameter a config lever moves. **One knob per change** is the whole guardrail. |
| **unobservable** | something the analysis could not read. Check it before claiming a run was clean. |

## Constraints

- **NEVER** apply a prompt rewrite or a config change without showing a diff and getting explicit approval.
- **NEVER** change what a prompt does — only improve how it is expressed.
- **NEVER** remove or modify tool/function definitions. This extends to `settings.tools[]` on an agent.
- **NEVER** substitute template variables (`{{variable_name}}`) with actual content.
- **NEVER** send a partial nested object. **ALWAYS** retrieve the object, change one key, send it back whole (see "Read-modify-write" below).
- **NEVER** change `model.id` and a behaviour parameter in the same step — you lose attribution.
- **NEVER** re-run a mini error analysis inline when an artifact exists or could be produced. That duplication is what `orq-analyze-agent` exists to remove.
- **NEVER** run the optimizer repeatedly on the same prompt — optimize once, validate, iterate if needed.
- **ALWAYS** pass `--version-increment` and `--version-description` on every write. The CLI documents both as *Optional*, so omitting them succeeds and publishes no version — silently costing the rollback story.
- **ALWAYS** preserve the prior version for rollback.
- **ALWAYS** close by recommending `orq-run-experiment`, against a dataset built from the artifact's `evidence` + `passing` ids.

**Why these constraints:** Rewriting can subtly change intent or remove important constraints. A config change is at least as capable of regressing quality as a prompt rewrite, so it needs the same gate and the same validation. A partial nested patch can silently delete an agent's tools inside the very command whose guardrail forbids that.

## Workflow Checklist

```
Agent Improvement Progress:
- [ ] Phase 1: Find the evidence (artifact, or a targeted sweep)
- [ ] Phase 2: Route each failure mode to its lever
- [ ] Phase 3: Build the change — prompt rewrite, or a one-knob config diff
- [ ] Phase 4: Show the diff, get approval, apply with a version bump
- [ ] Phase 5: Hand off to orq-run-experiment
```

## Done When

- Every failure mode in scope has either been fixed here or **named and stopped at** with its destination skill
- The user reviewed and approved a diff before anything was written
- **For an orq-agent write only:** a new version exists on orq.ai with a description saying what changed and why, and the prior version is intact. In deployment mode (config), local mode, and the inline-prompt path there is no write — the deliverable is the approved diff, and saying plainly that it must be applied by hand
- **For a config write:** the agent re-reads with the intended knob changed **and `settings.tools[]` unchanged**
- `orq-run-experiment` recommended, with the `evidence` + `passing` ids handed over

**Companion skills:**
- `orq-analyze-agent` — produces the error-analysis artifact this skill consumes
- `orq-build-agent` — re-architecture: decomposition, a new pipeline stage, `team_of_agents`
- `orq-build-evaluator` — an evaluator for a failure mode that has none
- `orq-evaluator-alignment` — realign an evaluator that already exists and disagrees
- `orq-run-experiment` — validate the change; **also materialises the regression dataset**
- `orq-cli` — the same platform operations from a shell for CI, cron, or bulk work

## When to use

Three distinct branches:
- **"the prompt is wrong"** — instructions under-specify the task, drift persona, miss a format
- **"the config is wrong"** — answers cut off, the agent gives up mid-task, sampling is off
- **"I have a failure taxonomy and want it fixed"** — an `error-analysis-*.md` exists

## When NOT to use

- **Need the failure taxonomy first?** → `orq-analyze-agent`
- **The fix is a re-architecture?** → `orq-build-agent`
- **An evaluator exists and disagrees with humans?** → `orq-evaluator-alignment`
- **No evaluator exists for the failure mode?** → `orq-build-evaluator`
- **Need to measure the change?** → `orq-run-experiment`

## Target modes

| Mode | Config read | Prompt write | Config write |
|---|---|---|---|
| **orq agent** | **Primary:** `mcp__orq__get_agent key=...` (workspace-scoped, always works). **Fallback:** `orq agents retrieve <key> --json` (project-scoped, 404 cross-project). | `orq agents update` → `instructions` | `orq agents update` → `settings` / `model` |
| **orq deployment** | **Primary:** `mcp__orq__get_deployment key=...`. **Fallback:** `orq deployments get-config`. | `POST /v2/prompts/<id>/versions` | **none** — recommend in prose |
| **local / no orq entity** | ask the user | diff in the response, user applies it | diff in the response, user applies it |

`orq deployments` exposes only `get-config`, `invoke`, `list`, `stream` — there is no `update`. Deployment config is read-only here; say so rather than implying a write happened.

## Steps

### Phase 1: Find the Evidence

**Before any of the four: read the target's own config and test it against its own instructions.** Use `mcp__orq__get_agent key=...` (workspace-scoped, finds agents across projects) as the primary path; fall back to `orq agents retrieve <key> --json` if MCP is unavailable. Then ask the single question: *does any setting make these instructions impossible to follow?* Instructions that mandate three sequential tool steps under `max_iterations: 2`, or an 800-word minimum under `max_tokens: 800`, are a contradiction visible without a single trace query.

If the agent config references tool IDs, knowledge-base IDs, memory-store IDs, or eval IDs, resolve them into full definitions before proceeding:
- `orq tools retrieve <id>` — tool schema and description
- `orq knowledge-bases retrieve <id>` — knowledge base contents and chunking config
- `orq memory-stores retrieve <id>` — memory store type and settings
- `orq evals get <id>` — evaluator rubric and scoring method

This is the cheapest finding in the skill and the one most often skipped in favour of a trace sweep. On a real case it would have replaced roughly twenty trace calls: the instructions demanded three one-at-a-time scrapes before writing, `max_iterations` was `2`, and the arithmetic (2 iterations produces 3 chat-completion spans) matched every observed trace exactly. Note the trap that made it easy to miss — the preflight in `run-key-preflight.md` already fetches the whole agent JSON. **Read that body; do not just check the status code.**

A contradiction found here is a **`fix: config` finding already**. Take it straight to Phase 3b and use traces only to confirm the rate, never to rediscover the cause.

**Four ways in, all landing in Phase 2.**

1. **An artifact exists** — glob `./error-analysis-*.md`, newest first. On more than one match, name the candidates and **ask**; do not guess.

   Then check the front matter's `target.version` against the live agent's version:

   | | Do |
   |---|---|
   | **Same version** | Proceed. |
   | **Version moved** | Say so, and offer to re-run `orq-analyze-agent` rather than routing off a taxonomy measured against a config that no longer exists. |
   | **`mode: local`** | Nothing to compare. Proceed, and say the config is unverified. |

   **Check `unobservable` before claiming anything about what the analysis covered.**

2. **No artifact, the user has traces** → recommend `orq-analyze-agent` first. **Do not silently re-run a mini error analysis inline.**

3. **No artifact, the user can describe the problem** ("it keeps cutting off mid-answer") → run a **narrow, targeted sweep** to ground the complaint in real traces: `orq traces aggregate` on **one or two signals only** — typically `attributes.gen_ai.response.finish_reasons` and `status` — then `orq traces search` for a handful of matching ids. This is the "start right away" path, and it is **hard-bounded to those one or two signals**. Anything wider must route to `orq-analyze-agent`.

   > **On an orq-hosted agent, that default pair is usually blind.** Measured 2026-08-26 on a 14-day window: `finish_reasons` was null on **246/246** traces across **all 6 agents** in the workspace, and `status` was `ok` on every one, while the same field was richly populated workspace-wide (3,174 `stop`, 5 `length` — a window-dependent count; a 20-day window returned 3,431 `stop`). A silent-quality failure emits no terminal-state signal at all, so the default pair reads *"clean"* when it is merely blind.
   >
   > **The terminal state is not missing, it is on a different endpoint.** `orq agents get-response <agent-key> <agent_execution_span_id>` returns an agent-level **`finish_reason`** whose vocabulary names the cause outright — including **`max_iterations`**, which no span attribute ever exposes. Verified across 6 agents: loop-capped agents returned `max_iterations` (15/15 and 5/5), token-capped agents returned **`length`** (10/10 and 10/10), uncapped agents returned `stop` (10/10 and 2/2). **`length` is the token-truncation case** — the exact "answers cut off" symptom in this skill's own *When to use* list.
   >
   > **Start here on any agent target. One call names the cause.**
   >
   > ```bash
   > # --raw and "| [0]" are REQUIRED; without them $AE word-splits and 404s
   > AE=$(orq traces list-spans <trace> --raw -j "data[?type=='span.agent_execution'].span_id | [0]")
   > orq agents get-response <agent-key> "$AE" --json -j 'finish_reason'
   > ```
   >
   > Use these only to corroborate it, or when `get-response` is unavailable:
   >
   > | Signal | How | Reads as |
   > |---|---|---|
   > | **Turn depth** | count `span.chat_completion` spans per trace: `list-spans -j "data[?type=='span.chat_completion'].span_id"` | **The same count on every trace is a hard cap.** Model behaviour varies run to run; a cap does not |
   > | **Final-answer size vs headroom** | `gen_ai.usage.output_tokens` on the *last* chat span, against `gen_ai.request.max_tokens` on that same span | Far below the cap means the model stopped early. At the cap means a genuine `max_tokens` truncation |
   >
   > **Do not infer what `finish_reason` will state.** Reconstructing a cap from turn-count uniformity costs roughly twenty-five calls and still only yields a hypothesis. `agent.iterations.count` looks like the field for turn depth and is not usable for it — see §4 of `trace-queries.md`.

4. **The user pasted a prompt inline, no orq entity** → skip all trace work, go straight to the prompting framework, and skip Phase 4 unless they explicitly ask to save it.

### Phase 2: Route Each Failure Mode to Its Lever

**The artifact is the router; this skill is an entry point, not a gate.** A taxonomy carries 4–8 modes with *mixed* levers. Handle the ones this skill owns; for the rest, **name the destination and stop**. Never forward on the user's behalf — that is what manufactures three-hop chains.

Route on `failure_modes[].fix`:

| `fix` | Handled by |
|---|---|
| `prompt` | **This skill** — the prompting framework below, now with the failing traces as evidence rather than a blind style review |
| `config` · `tools` | **This skill** — the config lever below |
| `retrieval` | **This skill** for knowledge-base attachment; deeper retrieval work → `orq-build-agent` |
| `structure` | → **`orq-build-agent`.** Task decomposition, adding a pipeline stage, splitting via `team_of_agents`. Named and stopped at, not attempted here |
| `evaluator` — none exists for this mode | → `orq-build-evaluator` |
| `evaluator` — one exists and disagrees, flips, or is the mode's only evidence | → `orq-evaluator-alignment` |
| `code` | Report it; out of platform scope |
| *data gap* | → `orq-generate-synthetic-dataset` |

**Ordering rule — when a mode carries `caused_by`, fix the config first and re-measure before rewriting the prompt.** Prompt-patching around a `max_tokens` cap bakes the workaround into the prompt permanently.

**A mode whose only evidence is `eval_*` is not actionable.** If no trace backs it, route to `orq-evaluator-alignment` and re-run the analysis. Acting on an unaligned judge changes the agent to satisfy the judge.

### Phase 3a: The Prompt Lever

**The 11 guidelines operate on `instructions`.** An agent's prompt is not a prompt entity — it lives in the agent's own `instructions` field. Rewrite that.

**Do not touch `system_prompt`** — it is an optional wrapper template, not the instruction body. **One exception:** if `orq agents retrieve` returns `system_prompt` **non-null**, the agent has a custom wrapper whose relationship to `instructions` is undocumented. **Show the wrapper to the user and ask before rewriting**, because a wrapper that does not interpolate `instructions` makes an instructions-only rewrite a silent no-op.

**The `variables` coupling.** An agent's `variables` field is *"extracted variables from agent instructions"*. A rewrite that drops or renames a `{{placeholder}}` desyncs it — which is why "never substitute `{{template_variables}}`" binds specifically to `instructions`.

#### Prompting Guidelines Framework

Each guideline is a dimension to evaluate — identify what is missing or weak, then improve it.

1. **Role assignment & expertise** — clear, emphasized role with specific domain expertise
2. **Task definition** — clear explanation of what the system will do
3. **Stress induction** — emphasis on the importance and criticality of the task
4. **Guidelines** — the task broken into clear guidelines: task explanation, behavioral constraints, communication style, knowledge boundaries
5. **Output format** — specified and stressed. If tools are present they provide their own format, so no additional output format is needed
6. **Tool calling** — tools are part of the task. Never suggest removing them. Keep definitions in their original state; adjustments to *how they are referenced* are fair game
7. **Reasoning** — for complex tasks, reasoning must be instructed and must appear before the final answer. If reasoning is instructed but the output format has no space for it, add one (e.g. a `reasoning` key in JSON)
8. **Examples** — few-shot examples in `<example>` XML tags, with proper variable formatting inside
9. **Remove unnecessary content** — no gratuitous markdown, emojis, XML tags, or contradictions
10. **Proper variable usage** — `{{double curly brackets}}` should appear once near the end; earlier references use XML tags
11. **Recap** — a one-sentence recap of the task and format at the end

**Ground each suggestion in a trace.** With an artifact in hand, name the failure mode and the guideline together: *"Mode 1 (persona drift, 12.5%) → guideline 1: the role is stated once and never reinforced."* A guideline finding with no failing trace behind it is a **best-practice suggestion**, not a fix — say which it is.

Present the analysis, ask which suggestions to apply, then rewrite:

```
## Prompt Analysis

**Strengths:** [what the prompt does well]

### Suggestions
1. [Guideline X] — [suggestion] — **Evidence-backed fix:** addresses failure mode [N] ([rate])
2. [Guideline Y] — [suggestion] — **Best-practice suggestion:** no failing example behind it
```

### Phase 3b: The Config Lever

1. **Read the current config** — `mcp__orq__get_agent key=...` or `orq agents retrieve <key> --json` (or `mcp__orq__get_deployment` / `orq deployments get-config`, or ask, in local mode).
2. **Propose a minimal diff — one knob per finding.** Never a wholesale config rewrite. On a `fix: config` mode the artifact already carries `knob` + `current` + `suggest`: build the patch from those three, without re-reading a trace.
3. **Clamp to the real bounds** — `max_iterations` 1–100, `max_execution_time` 2–600, `temperature` 0–2, `top_p` 0–1, `retry.count` 1–5, `reasoning_effort` in `none|minimal|low|medium|high|xhigh`. Proposing outside them just earns a 400.

#### Read-modify-write every nested object you touch, whole

`orq agents update` documents *"only the fields provided in the request body will be updated"*, but does **not** say whether a **nested** object deep-merges or replaces.

If it replaces, `{"settings":{"max_iterations":20}}` wipes `tools[]`, `max_cost`, `max_execution_time` and `tool_approval_required` — silently deleting the agent's tools in the same command whose guardrail forbids exactly that. `model` carries the identical hazard: it is `string | {id, parameters, retry}`, so a bare `{"model":{"parameters":{"temperature":0.5}}}` can drop `model.id`.

**Retrieve the object, change one key, send it back whole.** Correct under either merge behaviour, costs one extra call, and means the question never has to be answered. This applies to `settings`, to `model`, and to any nested object a future lever touches.

> **`settings.tools[]` does not round-trip. Read shape is not write shape.** A retrieve returns each tool as `{id, action_type, display_name, conditions, requires_approval}`; the update schema requires a **`type`** discriminator and rejects the read-side fields. Sending back exactly what you read fails with a `ZodError` 400 on `settings.tools[N]`. **Load the live write schema from the MCP `update_agent` tool before building any `settings` patch**, then translate:
>
> | Read (`agents retrieve`) | Write (`agents update`) |
> |---|---|
> | `action_type: "web_scraper"` | `type: "web_scraper"` |
> | `requires_approval: false` | `requires_approval: false` (keep) |
> | `id`, `display_name`, `conditions` | **omit** — on write, `id`/`key` mean a *custom* tool reference, not a built-in |
>
> **Never drop `tools[]` from the patch to dodge the 400.** That is precisely the silent tool deletion this section exists to prevent. Translate, do not delete.
>
> While building the write: `--version-description` is capped at **300 characters** and a longer one is rejected.

```bash
# 1. read
orq agents retrieve support-bot --json > current.json
# 2. change ONE key inside the WHOLE nested object, write patch.json without a BOM
# 3. write (approval gate first — Phase 4)
orq agents update support-bot --from-file patch.json \
  --version-increment patch \
  --version-description "max_tokens 8000->16000: 7.5% of runs ended finish_reasons=length"
```

### Phase 4: Show the Diff, Get Approval, Apply

Every write — prompt or config — goes through the same gate:

1. **Show the diff.** Original and new, side by side, with each change tied to the guideline or failure mode it addresses.
2. **Get explicit approval** via `AskUserQuestion`. Approval for one change is not approval for the next.
3. **Apply** with `--version-increment` and `--version-description` saying *what* changed and *why* (name the failure mode and its rate).
4. **Verify and report.** Re-read the agent: **the intended knob changed, and `settings.tools[]` is unchanged.** Report the new version and the rollback path.

Deployment mode stops at step 1 for config: show the recommended change and point at the UI.

### Phase 5: Hand Off to `orq-run-experiment`

Always close here. A config change is at least as capable of regressing quality as a prompt rewrite, and running an experiment against whatever dataset already exists measures something else.

**Hand over the artifact's `evidence` (failed) and `passing` (good) ids, and name the sequence** — leaving it at "hand over the ids" is precisely how the regression net never gets built:

```
orq datasets create  →  create-datapoint (batched, ≤100 per call)
                     →  create_experiment with task.type: agent + agent_key + evaluators[]
```

`orq-run-experiment` owns dataset selection and materialises this; this skill supplies the ids and does not build datasets.

> **Building a row needs the *content* of each trace, and content recovery is not guaranteed.** On the workspace this was probed against, no span carried message content: a router-passthrough root exposed `gen_ai.request` = `{model, stream}` only, and an OTLP root had `gen_ai.output` but no `gen_ai.input`. orq also documents PII redaction that deliberately strips input values from traces. **Try to build the rows; when the input content is not recoverable, say so and hand the ids over as a manual curation list. Never report a regression dataset that was not created.**
>
> **On an agent target, try `agents get-response` before concluding anything is unrecoverable.** Spans carry metadata only, but `orq agents get-response <agent-key> <agent_execution_span_id>` returns the agent's **final turn** as `parts[]` entries. `output[]` is length 1 — it is not a transcript — but the final assistant text is reliable and is enough for an expected-output column. **A dataset handed over without expected outputs, on an agent target, means this endpoint was not tried.**
>
> **For the full conversation transcript, use `mcp__orq__get_span span_id=... mode=full`.** This returns structured messages with all turns, tool calls, tool responses, and system instructions. Use it to recover the full input/output pairs when `get-response` only gives the final turn. `mode=compact` gives a lighter string-serialized version when you only need metadata + I/O summary.
>
> **It does not tell you whether the agent's tools ran.** A `parts[].kind == "tool_call"` entry appears only when a run was cut off *mid-call*; a normally-completed run shows none even when tools certainly ran. With `span.agent_tool_execution` never emitted either, tool execution is **`unobservable`** — record it, never infer it.
>
> **Unverified:** whether the PII redaction described above also applies to `get-response` output. No redaction markers appeared in any sampled text, but this was not tested on a workspace with redaction switched on.

**If the scoring evaluator has not been aligned, the before/after delta is not interpretable** — route through `orq-evaluator-alignment` first, or re-code by hand against the same taxonomy. "The rate dropped" with no stated method is not a result.

## Boundary with `orq-build-agent`

Both skills write through `orq agents update`, so the split matters:

- **`orq-build-agent`** — you are *creating or standing up* an agent, and config choices come from intent. It is also the destination for **`structure`**: re-architecting an existing agent is build-agent's craft even when the evidence came from production traces.
- **`orq-improve-agent`** — the agent exists and is *underperforming in production*, and **knob-level** changes come from trace evidence. It moves parameters, not architecture.

Both write with the same required version bump, so neither can corrupt the other's work.

## Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Applying a rewrite or a config change without review | Show a diff, get approval — both levers, every time |
| Sending a partial `settings` or `model` | Retrieve the object, change one key, send it back whole |
| Omitting `--version-increment` / `--version-description` | Always pass both — the CLI calls them optional and silently publishes no version |
| Changing the model and a parameter together | One at a time, or the result is unattributable |
| Moving several knobs at once | One knob per finding |
| Rewriting `system_prompt` | Rewrite `instructions`. If `system_prompt` is non-null, show it and ask |
| Prompt-patching around a config cause | Fix the knob first, re-measure, then look at the prompt |
| Re-running error analysis inline | Route to `orq-analyze-agent`; the narrow path is bounded to 1–2 signals |
| Forwarding a `structure` mode into a third skill | Name `orq-build-agent` as the destination and stop |
| Acting on a mode whose only evidence is `eval_*` | Route to `orq-evaluator-alignment` first |
| Running the optimizer repeatedly on one prompt | Optimize once, validate, then iterate |
| Skipping validation | `orq-run-experiment` against `evidence` + `passing` |

## Open in orq.ai

- **Agent:** `https://my.orq.ai/agents` — review versions, roll back
- **Prompts:** `https://my.orq.ai/prompts` — deployment prompt versions
- **Deployments:** `https://my.orq.ai/deployments` — config changes this skill cannot write

## Documentation & Resolution

1. **Live queries** — `mcp__orq__get_agent`, `mcp__orq__get_span`, `orq agents retrieve`, `orq traces …`, and the orq MCP read tools; API responses are always authoritative
2. **[`docs/trace-queries.md`](../../docs/trace-queries.md)** — the verified CLI contract, including the write path and its parameter bounds
3. **orq.ai documentation MCP** — `search_orq_ai_documentation` / `get_page_orq_ai_documentation`
4. **[docs.orq.ai](https://docs.orq.ai)** — [Prompt Engineering Guide](https://docs.orq.ai/docs/prompts/engineering-guide#prompt-engineering-guide-best-practices) · [Agents](https://docs.orq.ai/docs/agents/overview) · [Prompts](https://docs.orq.ai/docs/prompts/overview) · [Versioning](https://docs.orq.ai/docs/prompts/versioning) · [Deployments](https://docs.orq.ai/docs/deployments/overview)
5. **This skill file** — may lag behind API or docs changes

When this skill conflicts with live API behaviour or official docs, trust the source higher in this list.
