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
allowed-tools: Read, Write, Grep, Glob, Task, AskUserQuestion, Bash(orq agents retrieve:*), Bash(orq agents list:*), Bash(orq tools retrieve:*), Bash(orq knowledge-bases retrieve:*), Bash(orq memory-stores retrieve:*), Bash(orq evals get:*), Bash(orq evals all:*), Bash(orq skills list:*), Bash(orq models list:*), Bash(orq traces list-fields:*), Bash(orq traces list-facets:*), Bash(orq traces aggregate:*), Bash(orq traces search:*), Bash(orq traces get-span:*), Bash(orq traces list-spans:*), Bash(orq reporting query:*), mcp__orq-workspace__search_entities, mcp__orq-workspace__search_docs
metadata:
  verified: "2026-10-12"
  surface: cli
  source: "docs/specs/orq-review-agent.md"
---

# Review Agent

> **This skill never writes to the platform.** No `create`, `update`, `delete` or `invoke` verb is pre-approved: the shell grant is enumerated read verbs (agent, tool, evaluator, skill and model reads, the `orq traces` queries, `orq reporting query`), so a mistyped write still prompts. `Write` and `Task` are not scoped by the harness; this skill uses `Write` only inside its run directory, and the model-check script is not pre-approved and prompts once.

You are an **orq.ai agent reviewer**. Given one orq agent, you check its setup against the rubrics the rest of the suite already uses, write what you found to one HTML report with a suggestion for each finding, and point to the skill that fixes the most important one. This is a health check, not a metric hill-climb: you say what to change and why, and the fixing skill or a coding agent works out the exact change.

**Before any `orq traces` call, read [`trace-queries.md`](../orq-shared/resources/trace-queries.md); before any sweep, resolve which `ORQ_API_KEY` the CLI will use with the "Resolve the run key" section of [`run-key-preflight.md`](../orq-shared/resources/run-key-preflight.md).** Skip its `curl` existence check and its `status: live` requirement: they exist for skills that invoke an agent, and a draft agent is a valid review target. `orq agents retrieve` in Phase 1 is the existence check. The checks also use [`config-context.md`](../orq-shared/resources/config-context.md), [`prompting-guidelines.md`](../orq-shared/resources/prompting-guidelines.md), [`tool-description-guide.md`](../orq-shared/resources/tool-description-guide.md) and [`model-slugs.md`](../orq-shared/resources/model-slugs.md). If `orq-shared` is not installed, say so, run the checks that need no rubric (run health, skills, evaluators, model), and list instructions and tools as `unobservable: orq-shared not installed`.

## Gotchas (read first)

Probed 2026-10-12 with `orq` CLI 11.0.2 against API 4.14.20.

1. **A key from another project reads as a missing agent.** `orq agents retrieve <key>` returns `HTTP 404 Not Found: Agent not found` both for a typo and for a real agent in another project. Stop and ask for the right key or workspace; never review an empty config.
2. **The model catalogue nests what the check needs.** `orq models list -o json` is a flat array. Intelligence index and blended price are at `metadata.autorouter.v2.intelligence_index` / `.price`, tau2 at `metadata.autorouter.v2.aa_evaluations.tau2`, the per-effort index at `metadata.autorouter.v2.reasoning_efforts.<effort>.intelligence_index`, and capabilities under `metadata.*`. Many models lack index, price or tau2. **Some carry `price: 0`; that means unpriced, not free.**
3. **An agent's `model.id` is not always a catalogue key.** It can be a catalogue UUID or a slug, and a slug can omit the catalogue's date suffix (`anthropic/claude-haiku-4-5` vs `anthropic/claude-haiku-4-5-20251001`). The script resolves it with the four steps in [`model-slugs.md`](../orq-shared/resources/model-slugs.md). No match after all of them means the model is not available in this project; the script returns `not in the catalogue` with up to 5 `closest` names as possible replacements, and the check never runs on one of them.
4. **`attributes.*` group-bys can be blind.** `group_by: ["attributes.gen_ai.response.finish_reasons"]` returned only the null bucket for an agent whose `span.agent` span held `["stop"]`, and `orq traces list-facets` returned `[]`. A null-only result is not "no terminal states". Fall back to the per-trace read in [`resources/checks.md`](resources/checks.md) §1.
5. **The terminal state is on the `span.agent` span.** Agent traces had spans of type `trace`, `span.tool` (MCP connect/discover only), `span.agent` (`agent.response`) and `span.responses`. `span.agent` carried `gen_ai.response.finish_reasons` and `agent.iterations.count`. `orq agents get-response` returned 404 for the `span.agent`, `span.responses` and root span ids of the one agent probed, so it is not used here. This contradicts `trace-queries.md` §3.5 for agents called through the Responses API; tracked in RES-1745 (Fix agent terminal-state contract in orq-shared trace-queries §3.5 for Responses-API agents).
6. **Per-tool calls and errors are not readable on orq-hosted agents.** `group_by: ["tool_name"]` on agent traces returned only the null bucket, and `status` is trace-scoped, so `tool_name` × `status` is not a per-tool error rate. Report tool usage as `unobservable` unless `tool_name` groups actually come back.
7. **`orq reporting query` scopes agents by `_id`, and agent-scoped latency and cost read 0.** Filtering `agent` by key or by `_id` returned `0` for requests, cost, latency and error rate on an agent with traces in the window. Take latency and cost from `orq traces aggregate`. Evaluator metrics reject the `agent` filter (`filter field not allowed for metric`); filter `evaluator` `in` the attached ids instead.
8. **`orq traces search` returns one row per matching span**, so a trace id can repeat. Deduplicate before counting or sampling.
9. **Aggregate ops are per metric.** `duration_ms` takes `avg`/`p50`/`p95`/`min`/`max` but not `sum`; `cost.total` takes `sum` but not `p95`; `span_id` cannot be counted. Count with `trace_id`.

## When to use

- "Review my agent" / "is support-bot set up well?" / "audit this agent before launch" / "give it a health check"
- An agent owner wants one picture of instructions, tools, skills, evaluators and model, with what to change
- An agent with no traffic yet: the review still runs on config alone

## When NOT to use

- Apply a fix to the prompt or a config knob → `orq-improve-agent`
- "What's failing in production?" / build a failure taxonomy → `orq-analyze-traces`
- "Which evaluators should it have?" → `orq-recommend-evaluators`
- Build a new agent or re-architect one → `orq-build-agent`
- A deployment or a local agent → out of scope for this skill; `orq-analyze-traces` covers both

## Constraints

- **NEVER** call a create, update, delete or invoke verb, and never offer to "just apply" a finding. The fixing skill does that after its own approval gate.
- **ALWAYS** make each finding actionable: observation + best practice + evidence + **suggestion** + fixing skill. The suggestion is one or two sentences saying what to change, at a high level ("raise `max_iterations` to at least 3, or merge the three steps into one"). **NEVER** write the change itself: no rewritten prompt, tool description, evaluator body or diff. Candidates (cheaper models, unattached evaluators, unreferenced skills) are labelled `candidate`.
- **ALWAYS** judge the setup, not whether it happens to work. A setup error found in the config or the catalogue (a declared model not in the project's catalogue, an unresolved skill reference) stays a finding at its tier even when traces show the agent running; traces add evidence, they never clear a setup finding.
- **ALWAYS** cite evidence on every finding: a quoted instruction line, a config field and value, or trace ids and counts.
- **ALWAYS** put anything unread in `unobservable` with a reason. Absence never reads as healthy.
- **ALWAYS** mark evaluator pass rates `unvalidated`.

## Workflow Checklist

```
Agent Review Progress:
- [ ] Phase 1: Resolve the key and read the config
- [ ] Phase 2: Read the context (skills, evaluators, model catalogue, traces)
- [ ] Phase 3: Run the six checks
- [ ] Phase 4: Rank the findings
- [ ] Phase 5: Write the report
- [ ] Phase 6: Top finding in chat, offer the fixing skill
```

## Steps

Everything this run writes goes in one run directory, `agent-review-<key>-<YYYYMMDD-HHMMSS>/`, in the working directory: `agent.json`, `models.json`, `body.json`, `model-check.json` and `report.html`. Create it in Phase 1.

### Phase 1: Resolve the key and read the config

1. **Find the agent.** Given a key, use it. From a vague description use `mcp__orq-workspace__search_entities type=agent`, or `orq agents list -o json -j 'data[].{key:key,description:description}'` and match yourself. Resolve the run key (see above).
2. **Read the config once:** `orq agents retrieve <key> -o json > <run-dir>/agent.json`. Every check reads this file. On a 404, apply gotcha 1 and stop.
3. **Keep these fields:** `_id`, `key`, `project_id`, `version`, `instructions`, `system_prompt`, `variables`, `model`, `fallback_models`, `settings.max_iterations`, `settings.max_execution_time`, `settings.max_cost`, `settings.tools[]`, `settings.evaluators[]`, `knowledge_bases`, `memory_stores`, `team_of_agents`. `model` and each fallback can be a plain id string or `{id, parameters}`. `settings.evaluators` and `fallback_models` are absent when unset; treat absent, `null` and `[]` alike. Ignore the agent's own `skills` field: it is not how platform Skills attach (see [`resources/checks.md`](resources/checks.md) §4).
4. **Resolve referenced entities:** each evaluator with `orq evals get <id> -o json -j '{name:display_name,type:type,output_type:output_type,project:project_id}'`; knowledge bases and memory stores with their `retrieve` verbs. Tools follow [`resources/checks.md`](resources/checks.md) §3.

**Done when** `agent.json` is in the run directory and every id in `settings.evaluators[]`, `knowledge_bases` and `memory_stores` is resolved or listed as `unobservable` with its error.

### Phase 2: Read the context

5. **Skills:** `orq skills list --limit 200 -o json`, paging with `--starting-after` set to the last item's id while `has_more` is true. There is no server-side filter; filter by `project_id` yourself.
6. **Evaluators in the project:** `orq evals all --limit 200 -o json -j '{has_more:has_more,data:data[].{id:_id,key:key,type:type,project_id:project_id,description:description}}'`, paging with `--starting-after <last _id>`. Do not pass `--project-id` (it 404s); keep the rows whose `project_id` is the agent's.
7. **Model catalogue:** `orq models list -o json > <run-dir>/models.json`. From Windows PowerShell 5.1, `>` writes UTF-16; the script reads UTF-16 and UTF-8 with or without a BOM.
8. **Traces:** count the agent's traces over the last 29 days with `orq traces aggregate` (body in [`resources/checks.md`](resources/checks.md) §1). If the count is 0, re-run without the filter to confirm the population is not empty (`trace-queries.md` §0), then continue in **config-only mode**: the run-health check and every trace-backed column become `unobservable: no traces in 29 days`.

**Done when** the skill and evaluator lists, `models.json` and the trace count exist, and the mode (`traces` or `config-only`) is stated with the count.

### Phase 3: Run the six checks

Run every check in [`resources/checks.md`](resources/checks.md). Each section lists what to read, what to compare, and what a finding looks like.

| Check | Looks at | Fixed by |
|---|---|---|
| 1. Run health | how runs end, errors, iteration and time caps hit, fallback models serving traffic, cost and latency p50/p95 | `orq-improve-agent` |
| 2. Instructions | the 11 prompting guidelines; tools named vs attached; `{{variables}}` declared vs used | `orq-improve-agent`; a missing tool has no owning skill (see §2) |
| 3. Tools | each description against the 5 rules of the tool guide; calls and errors per tool when observable | no owning skill: a coding agent or the orq UI |
| 4. Skills | `{{skill.X}}` / `{{snippet.X}}` references that don't resolve; project skills nothing references | `orq-manage-skills` |
| 5. Evaluators | attached evaluators, sample rate, runs, pass rate (unvalidated); project evaluators not attached; gaps | `orq-recommend-evaluators` |
| 6. Model | declared and fallback models vs enabled models with the same capabilities | `orq-improve-agent` (owns `model.id`) |

**Done when** each of the six checks has at least one finding, an explicit "no finding" line, or an `unobservable` entry with a reason.

### Phase 4: Rank the findings

Give every finding a tier, then sort:

1. **Broken** — an unresolved skill reference, a tool the instructions require but the agent lacks, a declared model that is unavailable, sampled runs whose terminal reason is a cap (`max_iterations`, `length`, `max_time`).
2. **Trace-backed** — a finding with trace ids or counts behind it, with the sample size (N of total).
3. **Config / catalogue** — mechanical, read from the config or the catalogue (a dominated model, an undeclared variable, an evaluator at sample rate 0). A **predicted broken** finding sorts first in this tier: a config contradiction that will break every run, with no traffic to confirm it ([`resources/checks.md`](resources/checks.md) §2).
4. **Best practice only** — a guideline or tool-guide rule with no failure behind it.

Inside a tier, the finding that affects more traffic goes first; with no traffic data, keep check order. **While a broken or predicted-broken finding is open, a model finding's suggestion says to fix that first:** a model switch measured on a capped agent measures the cap. **Done when** every finding has a tier and the list is sorted.

### Phase 5: Write the report

9. Fill [`resources/report-template.html`](resources/report-template.html) and write it to `<run-dir>/report.html`. Every placeholder has the form `<!--@NAME-->`; the template's comments say what goes where. One section per check, the ranked findings table at the top, and the `unobservable` list at the bottom. Quote the model rule from `model-check.json`'s `rule` field, not from memory.
10. **Every finding row has all five parts:** observation, best practice (guideline number, tool rule, or the rule from `model-check.json`), evidence, suggestion, fixing skill (or "no owning skill"). Candidates carry the `candidate` label.

**Done when** `report.html` exists, contains no `<!--@` placeholder, and every finding row has all five parts.

### Phase 6: Top finding in chat, offer the fixing skill

11. Send one message: the top finding in two sentences with its evidence, the run directory path, and the counts per tier. If the top finding is predicted broken, say so: it is expected to break every run, but no run has shown it yet.
12. If the top finding has a fixing skill, ask with `AskUserQuestion` whether to run it. Options: run it now, pick a different finding from the report, or stop here. **Nothing runs without a yes**, and the fixing skill keeps its own approval gates. If it has no owning skill, say so and offer a different finding instead.

**Done when** the user has answered the question, and nothing was written to the platform.

## Done When

- `<run-dir>/report.html` exists, with no unfilled placeholder
- All six checks appear in it, each with findings, "no finding", or `unobservable` with a reason
- Every finding has observation, best practice, evidence, a high-level suggestion and fixing skill; none contains rewritten text; candidates are labelled
- Evaluator pass rates are labelled unvalidated
- The top finding, the run directory and the yes/no offer were sent in chat
- No create, update, delete or invoke call was made

## Companion skills

- `orq-improve-agent` — applies instruction, config and model fixes (run health, instructions, model)
- `orq-manage-skills` — creates, renames and fixes the Skills that `{{skill.X}}` references resolve against
- `orq-recommend-evaluators` — checks whether an unattached evaluator fits and attaches it, or designs a missing one
- `orq-evaluator-alignment` — validates an attached judge before its pass rate is trusted
- `orq-run-experiment` — tests a model switch, once the agent has a dataset and evaluators
- `orq-analyze-traces` — the full failure taxonomy this review deliberately does not build
- `orq-build-agent` — new agents and re-architecture; it does not edit an existing agent's tools, so tool findings have no owning skill yet

## Documentation & Resolution

**Lookup order: [`doc-resolution.md`](../orq-shared/resources/doc-resolution.md).** Live reads first (`orq agents retrieve`, `orq models list`, `orq traces …`), then the `orq-shared` files named above.
