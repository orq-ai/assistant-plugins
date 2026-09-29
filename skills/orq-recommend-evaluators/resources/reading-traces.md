# Reading traces (steps 7 and 13)

Read [`trace-queries.md`](../../orq-shared/resources/trace-queries.md) first. This file adds only what this skill needs on top of it.

## Step 7: count traces

1. Resolve the filter field with `orq traces list-fields -o json` (each row's key is `name`, not `field`). Filter on `agent_id` when you have it. `agent_name` projects and filters as null on some agents, so a zero from a name filter is unconfirmed until an id filter agrees.
2. Run one aggregate over the last 14 days, window computed at call time, body passed with `--from-file`:

   ```json
   {"from": "<now-14d>", "to": "<now>",
    "filters": [{"field": "agent_id", "op": "eq", "values": ["<agent id>"]}],
    "compute": [{"metric": "trace_id", "op": "count"}]}
   ```

3. On a zero, re-probe without the filter (`trace-queries.md` §0), then repeat over 29 days. A 30-day window is the retention edge and returns HTTP 400. Report both counts.
4. Below 20, the mode stays `config-only`. You may still read those older traces with step 13 to confirm a candidate. Cite them as evidence, not as a failure rate.
5. If every trace that exists failed (no output, a provider or auth error), say so. List the broken integration under `not_evaluators` with `route: config`. An evaluator cannot score output that is never produced.

**`grounding_reason` in `traces` mode**, written after step 13: the total count, how many step 13 read, and how many of those had readable text. Example: "250 traces in 14 days; 20 read, 12 with readable text". Step 13 is the only step that reads message text, so never report a readable count for traces it did not read. If none were readable, the mode stays `traces`, and you say that no failure rate backs the ranking.

## Step 13: bounded read without an artifact

The goal is to **confirm or rank** Phase 4a candidates ("the scope rule was broken in 3 of 20") and to spot a failure the config did not predict. Anything beyond that is `orq-analyze-traces`' job. Cite trace ids.

When the Task tool is available, give the trace reads to one subagent. It returns one short summary per trace: trace id, user request, what the agent did, which candidate rules it broke. That keeps raw spans out of the main context.

1. **List.** `orq traces search`, sorted `end_time desc`, ids only, up to 20 recent traces.
2. **Find the LLM span** with `list-spans`, using a null-safe `-j` projection.
3. **Project the message text.** Try these in order and stop at the first that returns real text. A null value or a stub like `{"type":"text"}` does not count.
   1. `-j 'span.attributes.gen_ai.{input:input,output:output}'`
   2. `-j 'span.attributes.openresponses.{input:input._value,output:output._value}'`
   3. On an orq-hosted agent, `openresponses.input` often returns only an item count. Use `orq agents get-response` on the trace instead. The agent span is spelled either `span.agent` named `agent.response` or `span.agent_execution`.
   4. `orq traces thread <trace_id> -o json`, which returns the normalized conversation. Use single-key `-j` projections here, because a multi-key projection containing a filter expression fails on older CLIs.
4. **Say when earlier turns could not be read.** Both projections hold JSON strings. For a multi-turn input, only an item count may come back.
5. **Last resort:** `mcp__orq-workspace__get_span mode=full`, only when every CLI projection returned no text.

**Check the subcommand spelling.** On CLI 10.3.1 the subcommand is `thread`. `orq traces conversation` and `orq traces conv` print the `traces` help and **exit 0**, so a wrong spelling looks like an empty conversation. Run `orq traces --help` to check.
