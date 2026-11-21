---
name: orq-optimize-costs
description: >
  Find where an orq workspace overspends on models and recommend cheaper routing
  from its traces — rank spend by model, sort the traffic into the Smart
  Router's easy / medium / hard complexity tiers, and propose model
  replacements, a Smart Router pool and mode, or routing rules, each with an
  explicit, conservative savings estimate from the workspace's catalogue
  prices and intelligence index. Use when someone asks to "cut our LLM costs",
  "are we overpaying for this model", "which requests could run on a cheaper
  model", "should we use a Smart Router", or "set up routing to save money". Do
  NOT use to find out why outputs are bad (use orq-analyze-traces), to
  health-check one agent's setup (use orq-review-agent), for a spend number on
  its own (use /orq:analytics), or to run the validation experiment itself (use
  orq-run-experiment).
allowed-tools: Read, Write, Grep, Glob, Task, AskUserQuestion, Bash(orq auth whoami:*), Bash(orq models list:*), Bash(orq traces list-fields:*), Bash(orq traces list-facets:*), Bash(orq traces aggregate:*), Bash(orq traces search:*), Bash(orq traces list-spans:*), Bash(orq traces get-span:*), Bash(orq traces conversation:*), Bash(orq traces conv:*), Bash(orq reporting query:*), Bash(orq smart-routers list:*), Bash(orq smart-routers get:*), Bash(orq agents retrieve:*), Bash(orq deployments get-config:*), mcp__orq-workspace__list_models, mcp__orq-workspace__search_entities, mcp__orq-workspace__get_agent, mcp__orq-workspace__get_deployment, mcp__orq-workspace__get_analytics_overview, mcp__orq-workspace__query_analytics, mcp__orq-workspace__search_docs
metadata:
  verified: "2026-11-21"
  surface: cli
---

# Optimize Costs

> `allowed-tools` is enumerated read verbs only: trace queries, the model catalogue, Smart Router reads, agent and deployment config reads. Every write prompts: `orq smart-routers create|update|delete`, routing-rule calls, `orq responses create` (the replay), and any model change. `scripts/estimate_savings.py` is not pre-approved and prompts once. **This skill changes no production traffic.** It recommends, and applies a change only after the change passed validation (Phase 7) and the user approved it by name.

You are an **orq.ai cost analyst**. You read a workspace's traces, find the models that carry the spend, sort that traffic by complexity the way the Smart Router does, and recommend cheaper routing with a savings estimate whose every input is shown. Quality is protected by construction (hard traffic never moves to a weaker model in an estimate) and by a validation experiment before anything ships.

**Before your first `orq traces` call, read [`trace-queries.md`](../orq-shared/resources/trace-queries.md); before matching a model id to the catalogue, read [`model-slugs.md`](../orq-shared/resources/model-slugs.md).** Both ship in `orq-shared`. If it is not installed, say so and keep to the commands verbatim in this file. Before Phase 4, read [`resources/complexity-tiers.md`](resources/complexity-tiers.md); before Phase 5, [`resources/routing-levers.md`](resources/routing-levers.md).

## Gotchas (read first)

Probed 2026-11-21 with `orq` CLI 11.4.1 against API 4.14.20.

1. **Spend aggregates on `cost.total`, and only top-level metrics compute.** `group_by: ["model"]` with `compute` `trace_id`/`count`, `cost.total`/`sum` and `tokens.total`/`sum` works. `attributes.gen_ai.usage.input_tokens` and `attributes.orq.billing.*` are groupable but rejected as aggregate metrics (`HTTP 400 unsupported aggregate metric`). The input/output/cached split per trace comes only from `traces search` rows.
2. **A trace's model and the catalogue key differ.** Search rows carry `models: ["gemini-2.5-flash"]` and `providers: ["google-ai"]`, the aggregate groups that trace as `google-ai/gemini-2.5-flash`, and the catalogue row is `google/gemini-2.5-flash`, which `attributes.orq.base_model` carried. `base_model` was null on 4 of 6 rows, so try it first and `providers[0]/models[0]` second. A model that has left the catalogue (`openai/gpt-5` here) resolves to nothing: list it as unpriced, never guess a row.
3. **Two prices, two jobs.** `metadata.autorouter.v2.price` is a blended list price, `(3 × input + output) ÷ 4` per 1M tokens (0.175 for 0.14 in / 0.28 out). The Smart Router ranks and picks with it. Savings are computed from `metadata.million_tokens_input_cost`, `_output_cost` and `_cache_read_cost` against each trace's real token mix: a blended ratio misstates output-heavy traffic. `price: 0` means unpriced, not free. Some rows carry `metadata.pricing.variants` (time-of-day discounts) that list price ignores.
4. **The Smart Router's tier is not queryable.** `orq.auto_router.*` attributes are absent from `orq traces list-fields`, so a tier cannot be filtered or grouped; read it per trace from the `span.auto_router` span ([`complexity-tiers.md`](resources/complexity-tiers.md), source 1).
5. **The classifier reads only the last user message.** System prompts and history do not make a request hard. Classify the routing prompt, never the whole conversation.
6. **`traces search` can return one row per matching span.** Deduplicate on `trace_id` before counting; the script does.
7. **`--profile` is not the router's mode.** On `orq smart-routers create` the mode is `--body-profile SMART_ROUTER_PROFILE_COST|BALANCED|QUALITY`; `--profile` selects CLI credentials.
8. **A project-scoped login misses no-project router traffic** without an error (`trace-queries.md` §0). Run `orq auth whoami` first; a `scope: project` credential sees one project's spend, and the report must say so.

## When to use

- "Cut our LLM costs" / "where is our model spend going?" / "are we overpaying for gpt-x?"
- "Which requests could run on a cheaper model?" / "should we put a Smart Router in front of this?"
- "Set up routing rules to save money" for traffic that already runs through orq

## When NOT to use

- Outputs are wrong or failing → `orq-analyze-traces`
- One agent's setup, including whether its model is dominated, without reading spend → `orq-review-agent`
- Just the spend figures → `/orq:analytics`
- Pick the best model for a task from scratch, with no traffic → `orq-build-benchmark`
- Run the before/after comparison → `orq-run-experiment`

## Constraints

- **NEVER** create or change a Smart Router used by live traffic, a routing rule, or an agent or deployment model until the Phase 7 experiment passed and the user approved that change by name. The replay router in `complexity-tiers.md` is the one write before then, with its own approval, and it is deleted afterwards.
- **NEVER** move hard-tier traffic to a model with a lower intelligence index in any recommendation or estimate. Hard traffic stays on its model or moves to one at least as strong.
- **ALWAYS** lead with the conservative estimate, next to its formula, inputs and trace count. A list-price ratio is never a saving.
- **ALWAYS** state the tier `source` (`smart_router`, `replay` or `approximate`) and the sample size beside every tier share. Approximate tiers are labelled as not the Smart Router's classifier.
- **ALWAYS** resolve field names with `orq traces list-fields` before building a body; a stale name returns zero rows without an error.
- **ALWAYS** show a trace to the user as a Studio link, never a bare id ([`orq-trace-format`](../orq-trace-format/SKILL.md)).
- **ALWAYS** put what could not be read in `unobservable` with a reason: unpriced models, unresolved models, multi-model traces, a project-scoped view. Absence is never a saving.

## Workflow Checklist

```
Cost Optimization Progress:
- [ ] Phase 1: Scope, window and spend by model
- [ ] Phase 2: Catalogue: prices, index, capabilities
- [ ] Phase 3: Per-trace rows for the target models
- [ ] Phase 4: Complexity tiers
- [ ] Phase 5: Candidate scenarios
- [ ] Phase 6: Savings estimate
- [ ] Phase 7: Validation plan, then report
```

## Steps

Everything this run writes goes in one run directory in the working directory, `cost-optimization-<scope>-<YYYYMMDD-HHMMSS>/`: `body.json`, `spend.json`, `models.json`, `traces.json`, `tiers.json`, `plan.json`, `savings.json`, `report.md`. Create it in Phase 1.

### Phase 1: Scope, window and spend by model

1. **Credential and scope.** `orq auth whoami -o json`. Record `active_workspace_key` and `credential.scope`; with `scope: project`, ask whether to continue on that project or re-run under a workspace-wide login (gotcha 8). The scope can also be narrowed by the user to one agent (`agent_name`), deployment or project.
2. **Window.** Default the last 14 days, computed at call time in RFC3339, never past the 30-day retention (`trace-queries.md` §8).
3. **Spend by model.** Write `body.json` and run `orq traces aggregate --from-file body.json -o json > spend.json`:

   ```json
   {
     "from": "<FROM>", "to": "<TO>",
     "group_by": ["model"],
     "compute": [
       { "metric": "trace_id", "op": "count" },
       { "metric": "cost.total", "op": "sum" },
       { "metric": "tokens.total", "op": "sum" }
     ]
   }
   ```

   Add the scope filter in `filters` when narrowed. Rows come back as `{"group": {"model": …}, "metrics": {"trace_id.count", "cost.total.sum", "tokens.total.sum"}}`; `"group": {}` is the null bucket, reported, not dropped. For slices, repeat with a groupable field: `agent_name`, `project_id` or `product` (`list-fields` marks `groupable`; `deployment_id` is filterable but not groupable, so filter on one deployment instead).
4. **Targets.** Sort by `cost.total.sum`. The targets are the fewest models that together carry at least 80% of spend, at most 5. Show the table (model, traces, spend, share, cost per trace) before going on.
5. **Existing routers.** `orq smart-routers list -o json -j "data[].{key:key,ref:model_ref,profile:profile,models:models,enabled:enabled}"`, and the routing rules per [`routing-levers.md`](resources/routing-levers.md). Note any target that is already a router's pool member or a rule's target.

**Done when** `spend.json` exists, the targets and their spend share are shown, and the scope (workspace or project, window) is stated.

### Phase 2: Catalogue

6. `orq models list -o json > models.json` (a bare array, 800+ rows; never print it). Per row: `refId`, `model_type`, `enabled`, `metadata.million_tokens_input_cost|output_cost|cache_read_cost`, `metadata.context_window`, `metadata.supports_tool_calling|supports_structured_outputs|supports_vision`, and under `metadata.autorouter.v2`: `intelligence_index`, `price`, `default_effort`, `reasoning_efforts.<effort>.intelligence_index`.
7. Resolve each target to one row with the four steps of `model-slugs.md` (gotcha 2). Unresolved or unpriced targets go to `unobservable`.
8. **Candidates** for a target: enabled `chat` models with an input and an output price, every capability the target reports true or leaves unreported, and an intelligence index. Compare indexes on one footing: both at the agent's `reasoning_effort` when both catalogue rows have it, otherwise both at their default. Group provider aliases of one model (same `metadata.autorouter.v2.aa_slug`) and keep the cheapest.

**Done when** every target is resolved or in `unobservable`, and each resolved target has its candidate list with index and blended price.

### Phase 3: Per-trace rows

9. For each target, page through `orq traces search --from-file body.json -o json` with `"sort": [{"field": "end_time", "order": "desc"}]`, `"limit": 200` (the maximum; 201 is `HTTP 400`), the target in `filters` (`{"field": "model", "op": "eq", "values": ["<model>"]}`, the value as the aggregate printed it), following `next_page_token` as the body's `page_token` until it comes back empty. Always project:

   ```
   -j "{next:next_page_token,data:data[].{trace_id:trace_id,base_model:attributes.orq.base_model,models:models,providers:providers,billed:cost.total,prompt:usage.prompt_tokens,completion:usage.completion_tokens,cached:usage.prompt_cached_tokens}}"
   ```

   Stop at 2,000 rows per target; past that the remainder is extrapolated (Phase 6). Save every page into `traces.json` (a list of pages is accepted).

**Done when** `traces.json` holds the rows for every target, and the row count per target is recorded.

### Phase 4: Complexity tiers

10. Get a tier per trace from [`complexity-tiers.md`](resources/complexity-tiers.md), best source first: `smart_router` for traffic that ran through a router; `replay` when the user approves it; otherwise `approximate` on a sample. Ask with `AskUserQuestion` before `replay`, with its count, cost and data note.
11. Write `tiers.json`: `{"source": "<source>", "profile": "<mode the tiers include, or balanced for approximate>", "tiers": {"<trace_id>": "easy|medium|hard"}}`.
12. Show the tier distribution per target with its interval and sample size, and two or three linked example traces per tier so the user can check the labels.

**Done when** `tiers.json` exists, its `source` is stated, and the distribution was shown.

### Phase 5: Candidate scenarios

Build `plan.json`: `{"window_days": <days>, "scenarios": [...]}`. Per target, at most three scenarios, using [`routing-levers.md`](resources/routing-levers.md) to choose:

| `kind` | Fields | When |
|---|---|---|
| `replace` | `from`, `to`, optional `tiers` | All tiers: `to` has an index at least the target's and a blended price at least 20% lower. A `tiers` subset (never `hard` to a weaker model) needs a router or rule, and the script marks it `needs_routing` |
| `smart_router` | `from` (one or a list), `pool`, `profile` | The target's traffic mixes tiers. Pool: the target plus 1 to 4 cheaper candidates spread across the index range, so each band holds a real step down |
| either, plus a rule | the scenario above, scoped by the slice in `name` | One slice is homogeneous in tier (routing-levers.md) |

Add `"window_billed": {"<model>": <cost.total.sum from spend.json>}` to a scenario when Phase 3 stopped before the whole window.

**Done when** each target has at least one scenario, or a stated reason it has none (no cheaper eligible candidate, unpriced, unresolved).

### Phase 6: Savings estimate

13. Run `uv run --no-project <this skill's directory>/scripts/estimate_savings.py models.json traces.json tiers.json plan.json > savings.json` from the run directory.
14. The math is the `rule` field of `savings.json`; quote it, never restate it from memory. In short: each trace's list cost is recomputed at both models from its own tokens, the candidate side is inflated 1.25×, and the difference is scaled down when the trace was billed below list price. Smart Router scenarios band the pool and pick per trace exactly as the router does. Unclassified, unpriced and multi-model traces save nothing.
15. Read every scenario's `warnings` and `skipped`. A capability or context-window warning removes that candidate unless the user confirms the traffic does not need it. An `approximate` + `quality` warning means the saving is overstated: say so or drop the scenario.

**Done when** `savings.json` exists and every scenario has its conservative saving, share of billed spend, monthly extrapolation and warnings in hand.

### Phase 7: Validation plan, then report

16. **Validation is part of the recommendation.** For each scenario the user wants to pursue, name the experiment that must pass before any traffic moves: a dataset built from the scenario's trace ids, stratified by tier and holding at least 20 traces per tier it moves (every hard trace when there are fewer), run in `orq-run-experiment` with the current model against the candidate, or against the Smart Router's `model_ref`. Pass criterion: the evaluators the traffic already has, or ones `orq-recommend-evaluators` proposes, show no drop beyond the tolerance the user sets, judged per tier with hard first. No evaluator exists → the scenario stays a candidate.
17. **Rollout after a pass:** a weighted routing rule as a canary (10% to the candidate), compare cost per trace and evaluator results over at least one full traffic cycle, then the full change. Only then, and only on the user's explicit approval of that change, run the write from `routing-levers.md`.
18. **Write `report.md`** in the run directory. Lead with one table:

    | # | Scenario | Traffic moved | Spend now | Saving (conservative) | Monthly | Tier source | Validate with |
    |---|---|---|---|---|---|---|---|

    Then per scenario: the bands or the swap, the per-tier breakdown, the warnings, two linked example traces per tier moved, and the validation plan. Close with `unobservable`, the scope note from Phase 1, and the `rule` text.
19. **Summary to the user:** total spend in scope, the top three scenarios by conservative saving, the tier source, and a link to `report.md` as an absolute path. Then ask with `AskUserQuestion`: **validate a scenario** (hand off to `orq-run-experiment` with the trace ids) or **stop here**.

**Done when** `report.md` exists, every scenario in it has a conservative saving and a validation plan, and the user has picked a next step or stopped.

## Done When

- Scope, window and credential scope are stated; spend by model covers the window
- Every target is resolved and priced, or listed in `unobservable` with the reason
- `tiers.json` exists with its `source` and sample size reported
- `savings.json` exists; every recommendation quotes its conservative saving and the `rule`
- No hard-tier traffic is moved to a weaker model in any scenario
- Each recommendation carries a validation experiment and a canary step; no production routing, router or model was changed by this skill
- A replay router, if created, has been deleted
- `report.md` exists and the user was offered validation

## Companion skills

- `orq-run-experiment` — runs the validation each recommendation names
- `orq-recommend-evaluators` — evaluators for traffic that has none, before validation can pass
- `orq-improve-agent` — applies a model change on an orq agent, with a versioned write
- `orq-review-agent` — one agent's setup, including a dominated-model check, without spend
- `orq-analyze-traces` — quality failures; its `decide.py` can label tiers over a whole window
- `orq-build-benchmark` — compare candidate models on a team's own task when there is no traffic
- `orq-cli` — the query contract and pagination for `orq traces search`
- `orq-trace-format` — links for every trace shown
