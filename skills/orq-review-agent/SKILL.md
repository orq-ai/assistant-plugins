---
name: orq-review-agent
description: >
  Health-check an orq agent's setup without changing it — run health,
  instructions, tools, skill references, evaluators and model choice checked
  against the suite's best practices, each finding with an actionable
  suggestion, written to one HTML report, with the skill that fixes the top
  finding offered. Use when someone asks to "review my agent", "is my agent set
  up well", "audit support-bot", or "give my agent a health check". Do NOT use
  to apply a fix (use orq-improve-agent), to find out what is failing in
  production from its conversations (use orq-analyze-traces), to pick
  evaluators to add (use orq-recommend-evaluators), or to hill-climb a metric.
allowed-tools: Read, Write, Grep, Glob, Task, AskUserQuestion, Bash(orq agents retrieve:*), Bash(orq agents list:*), Bash(orq tools retrieve:*), Bash(orq knowledge-bases retrieve:*), Bash(orq memory-stores retrieve:*), Bash(orq evals get:*), Bash(orq skills list:*), Bash(orq models list:*), Bash(orq traces list-fields:*), Bash(orq traces list-facets:*), Bash(orq traces aggregate:*), Bash(orq traces search:*), Bash(orq traces get-span:*), Bash(orq traces list-spans:*), Bash(orq reporting query:*), mcp__orq-workspace__search_entities, mcp__orq-workspace__get_agent, mcp__orq-workspace__search_docs
metadata:
  verified: "2026-10-12"
  surface: cli
  source: "docs/specs/orq-review-agent.md"
---

# Review Agent

> **This skill never writes to the platform.** `allowed-tools` is enumerated read verbs only: agent, tool, evaluator, skill and model reads, the `orq traces` queries and `orq reporting query`. No `create`, `update`, `delete` or `invoke` verb is granted, so a mistyped write still prompts. `Write` exists for the report and the scratch files (`agent.json`, `models.json`, `body.json`, `model-check.json`). The model-check script is not pre-approved and prompts once.

You are an **orq.ai agent reviewer**. Given one orq agent, you check its setup against the rubrics the rest of the suite already uses, write what you found to one HTML report with a suggestion for each finding, and point to the skill that fixes the most important one. This is a health check, not a metric hill-climb: you say what to change and why, and the fixing skill or a coding agent works out the exact change.

**Before any `orq traces` call, read [`trace-queries.md`](../orq-shared/resources/trace-queries.md); before any sweep, resolve which `ORQ_API_KEY` the CLI will use with the "Resolve the run key" section of [`run-key-preflight.md`](../orq-shared/resources/run-key-preflight.md).** Skip its `curl` existence check and its `status: live` requirement: they exist for skills that invoke an agent, and a draft agent is a valid review target. `orq agents retrieve` in Phase 1 is the existence check. The checks use three more files from `orq-shared`: [`config-context.md`](../orq-shared/resources/config-context.md), [`prompting-guidelines.md`](../orq-shared/resources/prompting-guidelines.md) and [`tool-description-guide.md`](../orq-shared/resources/tool-description-guide.md). If `orq-shared` is not installed, say so, run the checks that need no rubric (run health, skills, evaluators, model), and list instructions and tools as `unobservable: orq-shared not installed`.

## Gotchas (read first)

Probed 2026-10-12 with `orq` CLI 11.0.2 against API 4.14.20.

1. **A key from another project reads as a missing agent.** `orq agents retrieve <key>` returns `HTTP 404 Not Found: Agent not found` both for a typo and for a real agent in another project. Stop and ask for the right key or workspace; never review an empty config.
2. **The model catalogue nests what the check needs.** `orq models list -o json` is a flat array. Intelligence index and blended price are at `metadata.autorouter.v2.intelligence_index` / `.price`, tau2 at `metadata.autorouter.v2.aa_evaluations.tau2`, the per-effort index at `metadata.autorouter.v2.reasoning_efforts.<effort>.intelligence_index`, and capabilities at `metadata.context_window`, `metadata.supports_tool_calling`, `metadata.supports_vision`, `metadata.supports_structured_outputs`. Of 183 models, 108 had index and price, 82 had tau2. **Some models carry `price: 0`; that means unpriced, not free.**
3. **An agent's `model.id` is not always a catalogue key.** It can be the catalogue `id` (a UUID) or a slug. Two slugs seen live matched nothing exactly: `anthropic/claude-haiku-4-5` (catalogue: `anthropic/claude-haiku-4-5-20251001`) and `deepseek/deepseek-v4-flash` (no match at all). `scripts/model_check.py` matches `id`/`refId` exactly, then `model_id` (a display alias several providers can share), then a `refId` equal to the slug plus a date suffix (`-20251001`, `-0731`) when exactly one row has it, and otherwise reports the model as unknown with up to 5 `closest` catalogue names (by name similarity, date suffix ignored) for the report to show. It never matches a longer name, and never runs the check on a `closest` guess: `openai/gpt-4o` does not resolve to `openai/gpt-4o-mini`. Report a date-suffix match as such. `model` and each `fallback_models` entry can be a plain id string or an object with its own `parameters`; the script reads both.
4. **`attributes.*` group-bys can be blind.** `group_by: ["attributes.gen_ai.response.finish_reasons"]` returned only the null bucket for an agent whose `span.agent` span held `["stop"]`, and `orq traces list-facets` returned `[]`. A null-only result is not "no terminal states". Fall back to the per-trace read in [`resources/checks.md`](resources/checks.md) §1.
5. **The terminal state is on the `span.agent` span now.** Agent traces had spans of type `trace`, `span.tool` (MCP connect/discover only), `span.agent` (`agent.response`) and `span.responses`. `span.agent` carried `gen_ai.response.finish_reasons` and `agent.iterations.count`. `orq agents get-response` returned 404 for the `span.agent`, `span.responses` and root span ids of the one agent probed, so it is not used here.
6. **Per-tool calls and errors are not readable on orq-hosted agents.** `group_by: ["tool_name"]` on agent traces returned only the null bucket, and `status` is trace-scoped, so `tool_name` × `status` is not a per-tool error rate. Report tool usage as `unobservable` unless `tool_name` groups actually come back.
7. **`orq reporting query` scopes agents by `_id`, and agent-scoped latency and cost read 0.** Filtering `agent` by key or by `_id` returned `0` for requests, cost, latency and error rate on an agent with 3 traces in the window. Take latency and cost from `orq traces aggregate` (`duration_ms` p50/p95, `cost.total` sum). Evaluator metrics reject the `agent` filter (`filter field not allowed for metric`); filter `evaluator` `in` the attached ids instead.
8. **`orq traces search` returns one row per matching span**, so a trace id can repeat. Deduplicate before counting or sampling.
9. **Aggregate ops are per metric.** `duration_ms` takes `avg`/`p50`/`p95`/`min`/`max` but not `sum`; `cost.total` takes `sum` but not `p95`; `span_id` cannot be counted. Count with `trace_id`.

## When to use

- "Review my agent" / "is support-bot set up well?" / "audit this agent before launch"
- An agent owner wants one picture of instructions, tools, skills, evaluators and model, and a place to start
- An agent with no traffic yet: the review still runs on config alone

## When NOT to use

- Apply a fix to the prompt or a config knob → `orq-improve-agent`
- "What's failing in production?" / build a failure taxonomy → `orq-analyze-traces`
- "Which evaluators should it have?" → `orq-recommend-evaluators`
- Build a new agent or re-architect one → `orq-build-agent`
- A deployment or a local agent → out of scope for this skill; `orq-analyze-traces` covers both

## Constraints

- **NEVER** call a create, update, delete or invoke verb, and never offer to "just apply" a finding. The fixing skill does that after its own approval gate.
- **ALWAYS** make each finding actionable: observation + best practice + evidence + **suggestion** + fixing skill. The suggestion is one or two sentences saying what to change, at a high level ("raise `max_iterations` to at least 3, or merge the three steps into one"). **NEVER** write the change itself: no rewritten prompt, tool description, evaluator body or diff. Candidates (cheaper models, unreferenced skills) are labelled `candidate`.
- **ALWAYS** cite evidence on every finding: a quoted instruction line, a config field and value, or trace ids and counts.
- **ALWAYS** put anything unread in `unobservable` with a reason. Absence never reads as healthy.
- **ALWAYS** mark evaluator pass rates `unvalidated`.

## Workflow Checklist

```
Agent Review Progress:
- [ ] Phase 1: Resolve the key and read the config
- [ ] Phase 2: Read the context (skills, model catalogue, traces)
- [ ] Phase 3: Run the six checks
- [ ] Phase 4: Rank the findings
- [ ] Phase 5: Write the report
- [ ] Phase 6: Top finding in chat, offer the fixing skill
```

## Steps

### Phase 1: Resolve the key and read the config

1. **Find the agent.** From a vague description use `mcp__orq-workspace__search_entities type=agent`, or `orq agents list -o json -j 'data[].{key:key,description:description}'` and match yourself. Resolve the run key (see above).
2. **Read the whole config.** Primary: `mcp__orq-workspace__get_agent key=<key>` (workspace-scoped). Then save the CLI form the model check reads: `orq agents retrieve <key> -o json > agent.json`; without MCP, this one call is the read. On a 404, apply gotcha 1 and stop.
3. **Keep these fields:** `_id`, `key`, `project_id`, `version`, `instructions`, `system_prompt`, `variables`, `model.id`, `model.parameters`, `fallback_models`, `settings.max_iterations`, `settings.max_execution_time`, `settings.max_cost`, `settings.tools[]`, `settings.evaluators[]`, `knowledge_bases`, `memory_stores`, `team_of_agents`. `settings.evaluators` and `fallback_models` are absent when unset; treat absent, `null` and `[]` alike. Ignore the agent's own `skills` field: it is not how platform Skills attach (see [`resources/checks.md`](resources/checks.md) §4).
4. **Resolve referenced entities:** each evaluator with `orq evals get <id> -o json -j '{name:display_name,type:type,output_type:output_type,project:project_id}'`; knowledge bases and memory stores with their `retrieve` verbs. Tools follow [`resources/checks.md`](resources/checks.md) §3.

**Done when** `agent.json` is written and every id in `settings.evaluators[]`, `knowledge_bases` and `memory_stores` is resolved or listed as `unobservable` with its error.

### Phase 2: Read the context

5. **Skills:** `orq skills list --limit 200 -o json`, paging with `--starting-after` set to the last item's id while `has_more` is true. There is no server-side filter; filter by `project_id` yourself.
6. **Model catalogue:** `orq models list -o json > models.json`. From Windows PowerShell 5.1, `>` writes UTF-16; the script reads UTF-16 and UTF-8 with or without a BOM, so either is fine.
7. **Traces:** count the agent's traces over the last 29 days with `orq traces aggregate` (body in [`resources/checks.md`](resources/checks.md) §1). If the count is 0, re-run without the filter to confirm the population is not empty (`trace-queries.md` §0), then continue in **config-only mode**: the run-health check and every trace-backed column become `unobservable: no traces in 29 days`.

**Done when** the skill list, `models.json` and the trace count exist, and the mode (`traces` or `config-only`) is stated with the count.

### Phase 3: Run the six checks

Run every check in [`resources/checks.md`](resources/checks.md). Each section lists what to read, what to compare, and what a finding looks like.

| Check | Looks at | Fixed by |
|---|---|---|
| 1. Run health | how runs end, errors, iteration and time caps hit, fallback models serving traffic, cost and latency p50/p95 | `orq-improve-agent` |
| 2. Instructions | the 11 prompting guidelines; tools named vs attached; `{{variables}}` declared vs used | `orq-improve-agent` |
| 3. Tools | each description against the 5 rules of the tool guide; calls and errors per tool when observable | `orq-build-agent` |
| 4. Skills | `{{skill.X}}` / `{{snippet.X}}` references that don't resolve; project skills nothing references | `orq-manage-skills` |
| 5. Evaluators | attached evaluators, sample rate, runs, pass rate (unvalidated); gaps | `orq-recommend-evaluators` |
| 6. Model | declared and fallback models vs enabled models with the same capabilities | `orq-run-experiment` |

**Done when** each of the six checks has at least one finding, an explicit "no finding" line, or an `unobservable` entry with a reason.

### Phase 4: Rank the findings

Give every finding a tier, then sort:

1. **Broken** — an unresolved skill reference, a tool the instructions name but the agent lacks, runs ending on a cap (`max_iterations`, `length`, `max_time`).
2. **Trace-backed** — a finding with trace ids or counts behind it.
3. **Config / catalogue** — mechanical, read from the config or the catalogue (a dominated model, an undeclared variable, an evaluator at sample rate 0). A **predicted broken** finding sorts first in this tier: a config contradiction that will break every run, with no traffic to confirm it ([`resources/checks.md`](resources/checks.md) §2).
4. **Best practice only** — a guideline or tool-guide rule with no failure behind it.

Inside a tier, the finding that affects more traffic goes first; with no traffic data, keep check order. **Done when** every finding has a tier and the list is sorted.

### Phase 5: Write the report

8. Fill [`resources/report-template.html`](resources/report-template.html) and write it to `agent-review-<key>-<YYYYMMDD-HHMMSS>.html` in the working directory. Fill every placeholder; the template's comments say what goes where. One section per check, the ranked findings table at the top, and the `unobservable` list at the bottom.
9. **Every finding row has all five parts:** observation, best practice (guideline number, tool rule, or the A1 rule), evidence, suggestion, fixing skill. Model candidates and unreferenced skills carry the `candidate` label.

**Done when** the file exists, no uppercase template placeholder (`{{AGENT_KEY}}` and the like) is left, and every finding row has all five parts. Quoted instruction text may legitimately contain `{{variable}}`.

### Phase 6: Top finding in chat, offer the fixing skill

10. Send one message: the top finding in two sentences with its evidence, the report path, and the counts per tier. If the top finding is predicted broken, say so: it is expected to break every run, but no run has shown it yet.
11. Ask with `AskUserQuestion` whether to run the fixing skill for the top finding. Options: run it now, pick a different finding from the report, or stop here. **Nothing runs without a yes**, and the fixing skill keeps its own approval gates.
12. Name the scratch files this run wrote (`agent.json`, `models.json`, `body.json`, `model-check.json`) so the user can delete them.

**Done when** the user has answered the question, and nothing was written to the platform.

## Done When

- The report exists at `agent-review-<key>-<timestamp>.html`, with no unfilled template placeholder
- All six checks appear in it, each with findings, "no finding", or `unobservable` with a reason
- Every finding has observation, best practice, evidence, a high-level suggestion and fixing skill; none contains rewritten text; candidates are labelled
- Evaluator pass rates are labelled unvalidated
- The top finding, the report path and the yes/no offer were sent in chat
- No create, update, delete or invoke call was made

## Companion skills

- `orq-improve-agent` — applies instruction and config fixes (run health, instructions)
- `orq-build-agent` — tool descriptions and re-architecture; the review points here for tool findings because no skill owns editing an existing agent's tool descriptions yet
- `orq-manage-skills` — creates, renames and fixes the Skills that `{{skill.X}}` references resolve against
- `orq-recommend-evaluators` — picks and creates the evaluators an agent is missing
- `orq-evaluator-alignment` — validates an attached judge before its pass rate is trusted
- `orq-run-experiment` — tests a model switch before making it
- `orq-analyze-traces` — the full failure taxonomy this review deliberately does not build

## Documentation & Resolution

**Lookup order: [`doc-resolution.md`](../orq-shared/resources/doc-resolution.md).** Live reads first (`orq agents retrieve`, `orq models list`, `orq traces …`), then the `orq-shared` files named above.
