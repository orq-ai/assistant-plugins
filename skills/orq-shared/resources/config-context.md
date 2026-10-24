# Config Context — what to read about an agent before judging it

The list of mechanical facts about how an agent is configured and how its runs ended. `orq-analyze-traces` relays it in Phase 0 before open coding; `orq-review-agent` reads the same list for its run-health check. Correct an item here once.

All sweep signals go through `orq traces aggregate` with the target in `filters` ([`trace-queries.md`](trace-queries.md) §1). Resolve every field name at runtime.

1. **Terminal states — how runs ended, as a distribution.**
   - `attributes.gen_ai.response.finish_reasons` — `stop` / `length` / `tool_calls` / `max_iterations` / `max_time`
   - `status` — `ok` / `error` / `unset`, with the error messages
   - `attributes.orq.billing.threshold_exceeded`

   On managed agents, trace `status` may stay `ok` when a nested model call errors, and `finish_reasons` can sweep as all-null. Inspect span-level `attributes.http.response.status_code`; read run terminal reasons and per-turn finish reasons as described in [`trace-queries.md`](trace-queries.md) §§3.5–4.

   `max_iterations` and `max_time` are **explicit terminal reasons** — the platform is saying the run hit the cap. No distribution-shape inference needed.

2. **Declared config — what the agent is set to** (from `mcp__orq-workspace__get_agent`, `orq agents retrieve`, `orq deployments get-config`, or the user in local mode):
   - `model.id` and `model.fallback_models`
   - `model.parameters.*` — `temperature`, `top_p`, `max_tokens`, `reasoning_effort`, `tool_choice`, `parallel_tool_calls`
   - `settings.*` — `max_iterations`, `max_execution_time`, `max_cost`, `tools[]`, `evaluators[]` **with each `sample_rate`**
   - `knowledge_bases`, `memory_stores`

   When the agent config references entity IDs (tools, knowledge bases, memory stores, evaluators), resolve them into full definitions to understand what the agent can actually do:
   - `orq tools retrieve <id> -o json` — tool function schemas and descriptions
   - `orq knowledge-bases retrieve <id> -o json` — KB config and datasource attachments
   - `orq memory-stores retrieve <id> -o json` — memory store config
   - `orq evals get <id> -o json` — evaluator definition and scoring criteria

3. **Observed behaviour — what actually happened**, so declared and actual can be compared:
   - model actually served vs. declared (is a fallback firing?)
   - `openresponses.truncation`
   - tool-call spread by `tool_name`
   - prompt-cache hit ratio (`cached_tokens ÷ prompt_tokens`)
   - latency and cost distribution
   - existing `eval_passed` / `eval_score` rates, **each paired with its `settings.evaluators[].sample_rate`** — available off the traces, or grouped per evaluator in one call via `orq reporting query` with `genai.evaluator.pass_rate` / `.score.avg` / `.runs` and `group_by: ["evaluator"]`
   - TTFT via `orq reporting query` (`genai.ttft.avg|p50|p95`) — **an all-zero result is `unobservable: [ttft]`, not "fast"**

   Check evaluator presence with `exists`, not `neq null`. An attached evaluator without results is unknown, never a 0% or 100% pass rate. Reporting takes the agent ULID `_id`; cross-check its scoped request count against the trace sweep before relaying numbers. A mismatch makes reporting metrics unobservable. Reporting cannot filter TTFT by agent; mark that scope unobservable ([`trace-queries.md`](trace-queries.md) §5).

   > **Relay evaluator rates as unvalidated.** A judge's agreement with human judgement drifts — with foundation-model updates, and with the team's own definition of the criterion. Relay the rate, relay the sample rate, and say neither is ground truth.

4. **Name everything unreadable.** Anything that cannot be read goes in `unobservable` **with a reason**. Never omit it — absence must never read as "fine".

**The list is complete when every item in 1–3 is either a measured value or listed in `unobservable` with a reason.** That criterion is exhaustive on purpose: it is the defence against a mid-deploy trace query returning a confident, empty, non-erroring result that a looser check would wave through as clean.
