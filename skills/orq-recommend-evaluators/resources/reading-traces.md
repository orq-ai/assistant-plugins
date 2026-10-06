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
2. **Read the normalized conversation first:** `orq traces conversation <trace_id> -o json`. It selects the most specific conversation span; use `--spans` to inspect that choice, or pass a span id when needed. For a trajectory candidate, project `messages[].{index:index,role:role,tool_names:tool_calls[].name,content_types:content[].type}` first to check turn and tool-call order without returning message text. A 2026-09-29 agent trace showed system, user, assistant tool call, tool result and final assistant in order across two chat-completion spans.
3. **Read bounded message text** from the thread for the candidate rules being checked. `-o json` is uncut unless you pass `--max-chars` or `--tool-max-chars` (on 11.0.0+; the `xml` and `markdown` renders default to `--max-chars 4000` per block), so cap long tool results with `--tool-max-chars` and use `--slice`, `--match` or `--include` to narrow a long conversation. A null value or a stub like `{"type":"text"}` does not count as readable text.
4. **If the thread lacks text,** find the LLM span with `list-spans` using a null-safe `-j` projection, then try `get-span -j 'span.attributes.gen_ai.{input:input,output:output}'` and `-j 'span.attributes.openresponses.{input:input._value,output:output._value}'`. On an orq-hosted agent, `openresponses.input` may hold only an item count; use `orq agents get-response` on the agent-execution span for final output, but do not treat that final turn as the whole thread.
5. **Last resort:** `mcp__orq-workspace__get_span mode=full`, only when the CLI thread and projections returned no text. Say when earlier turns could not be read.

**Check the subcommand spelling.** From CLI 11.4.0 the subcommand is `conversation` (alias `conv`), with `thread` kept as a deprecated spelling that warns on stderr; 10.0.0 through 11.3.x have only `thread`. A spelling the installed CLI lacks is not always an error: on 10.3.1 `orq traces conversation` printed the `traces` help and **exit 0**, which looks like an empty conversation (an 11.1.0 release candidate errors `unknown command "conversation" for "orq traces"` at exit 1 instead). Run `orq traces --help` to check.
