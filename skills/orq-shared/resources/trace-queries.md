# Trace Queries — the `orq` CLI contract

Shared reference for `orq-analyze-traces` and `orq-improve-agent`. Everything here was probed live against `orq` CLI 4.13.1 on 2026-08-26, and re-verified against **4.14.0** the same day with no behavioural drift observed. The agent-target, reporting-scoping and sampling-filter claims dated 2026-10-14 were probed on **CLI 11.4.1** (API 4.14.20) against an orq-hosted agent.

**Read this before your first `orq traces` call. The invocation details below are not optional** — three of them (`--from-file`, the required sort, the null-safe projection) fail loudly, and two (a stale field name, a mid-deploy empty result) fail *silently* by returning a confident empty answer.

---

## CLI vs MCP — when to use which

The CLI and MCP overlap on most read operations but differ in two critical ways: **scope** and **projection**.

| | CLI (`orq`) | MCP (`mcp__orq-workspace__*`) |
|---|---|---|
| **Scope** | Project-scoped. Returns 404 / empty for entities in other projects. | Workspace-scoped. Finds entities across all projects the API key can reach. |
| **Projection** | `-j` (JMESPath) projects 115 KB spans down to ~7 lines. Essential for keeping trace data out of context. | `get_span mode=compact` (metadata + string-serialized I/O) vs `mode=full` (structured messages, all turns, tool calls, system instructions). `list_spans` always returns full attributes. |

**Default rule: MCP for discovery and content, CLI for projection and aggregation.**

- **Agent discovery:** `mcp__orq-workspace__search_entities type=agent query="..."` — fuzzy-matches name, key, and description across the workspace. Then `mcp__orq-workspace__get_agent key=...` for the full config (model, instructions, tools, KBs, memory stores, settings, URL).
- **Trace aggregates and search:** CLI `orq traces aggregate` / `search` with `--from-file` — the only path that supports `group_by`, `compute`, and arbitrary filters.
- **Span tree (compact):** CLI `orq traces list-spans -j "data[].{...}"` — the projection keeps 83-span traces under 10 KB.
- **Span detail (projected):** CLI `orq traces get-span -j 'span.summary'` for metadata, `span.attributes.*` for config knobs.
- **Full conversation content:** CLI `orq traces conversation <trace-id> [<span-id>]` (**7.4.0+**; short alias `conv`; spelled `traces thread` before 11.4.0, deprecated from then) or `mcp__orq-workspace__get_span span_id=... mode=full`. `conversation` normalizes Chat Completions, OpenAI Responses and OpenTelemetry GenAI payloads into one message list and renders it far more compactly than the raw span — never reconstruct a conversation out of `get-span` attributes. Its `-o` is its own (`xml` default, plus `markdown`, `json`, `yaml`, `toon`); see `orq-cli/SKILL.md`, "Reading a conversation", for span selection, the filter flags and their fixed order. **The `xml` and `markdown` renders cut every block at 4,000 characters** by default and mark it `[truncated: N more characters]`, which hides most of a long system prompt or pasted log; pass `--max-chars 0`, or use `-o json` (uncut unless capped), when the full text matters (a 124 KB conversation rendered as 6.7 KB by default, 2026-10-14). `conversation` does not carry `finish_reason`. Per turn, read it from the `span.chat_completion` span with `get-span -j 'span.attributes.gen_ai.response.finish_reasons'` (§4) or MCP `mode=full`; for the run as a whole, `agents get-response` (§3.5).
- **Related entities:** CLI `orq tools retrieve`, `orq knowledge-bases retrieve`, `orq memory-stores retrieve`, `orq evals get` — resolve the IDs from the agent config into full definitions. **`orq tools retrieve` only resolves custom tools.** A built-in tool in `settings.tools[]` (one carrying `action_type`, e.g. `current_date`) returns `HTTP 404 Tool collection not found` for its `id` (CLI 11.4.1, 2026-10-14). Its schema is on any chat-completion span instead: `get-span … -j 'span.attributes.gen_ai.tool.definitions'` (§4).

Each consuming skill states what it is **best for** on top of this; the scope and projection rows above do not change per skill.

## Shared vocabulary

| Word | What it means here |
|---|---|
| **lever** | which *kind* of change fixes a failure mode — prompt, config, tools, retrieval, structure, evaluator, code. Written to the error-analysis artifact as `fix`. |
| **knob** | the single parameter a config lever moves. **One knob per change** is the whole guardrail. |
| **unobservable** | honest absence: something that could not be read, named with a reason — never omitted, never assumed fine. Check it before claiming a run was clean. |

---

## 0. Resolve field names at runtime. Always.

Field names in the trace registry **changed once in a single afternoon** — the 2026-08-26 release renamed every `attr.*` field to `attributes.*` and grew the registry from 56 fields to 57. A hard-coded name that no longer resolves returns **zero rows without erroring**, which reads as "clean" rather than "broken".

```bash
orq traces list-fields -o json     # the queryable fields
orq traces list-facets -o json     # the facetable subset
```

> **Canonical source: `skills/orq-cli/SKILL.md`, "The trace filter contract" onward.** That skill owns the CLI's general query constraints — field discovery, the `end_time desc` sort, the 30-day retention `400`. This file states each as a one-line rule because it is read at call time, and links there for the detail. **Correct them there first**; a rule fixed in one file and missed in the other is exactly the stale guidance §0 is about.

**ALWAYS** resolve names from those two commands before building a query body. **NEVER** copy a field name out of this document into a query. When a name you expected is absent, say so and stop — do not fall back to a filter that silently matches nothing.

**A mid-deploy query returns a confident, empty, non-erroring result.** During the 2026-08-26 rollout, `attributes.*` facets returned `[]` and `attributes.*` filters returned zero rows, both without an error. Re-probe before trusting a zero: run the same query with the filter removed and confirm the population is non-empty.

**An `ORQ_API_KEY` in the environment overrides the CLI login, and can see less.** With the variable set, `orq auth whoami` reports `source: ORQ_API_KEY`; without it, the saved session (`scope: all_projects`). Router traffic sent with no project (`project_id eq ""`) is invisible to a project key: the same 7-day search returned 0 traces under the env key and 5 under the session (CLI 11.4.1, 2026-10-14), with no error either way. Run `orq auth whoami` first, and when a target is missing retry with `env -u ORQ_API_KEY orq …` before calling it absent. `decide.py` reads traces with `ORQ_API_KEY` only, so for no-project traffic it needs a workspace-wide key.

### Every capability claim in this file must carry the command that produced it

This document has twice described behaviour that was never run: a capability cell filled in by inference, and a *"the only way to X"* sentence that a five-minute check refuted. Both read as verified because they sat beside genuinely verified text.

**Rule: a cell in a capability table, or any sentence of the form "X is the only way to Y", must be accompanied by the command that produced it. If it was not run, write `untested` in the cell.** `untested` is useful. A plausible guess formatted as a measurement is worse than a blank, because the next reader cannot tell the two apart.

---

## 1. The layered read

| Layer | Command | Cost | Answers |
|---|---|---|---|
| **1. Population sweep** | `orq traces aggregate --from-file <body>.json -o json` | one call, one row per group | Distributions for any signal, **scoped to the target** via `filters` |
| **2. Row selection** | `orq traces search --from-file <body>.json -o json -j 'data[].trace_id'` | one call, ids only | Which traces exhibit the swept condition |
| **3. Config detail** | `orq traces get-span <trace-id> <span-id> -o json -j '<projection>'` | one call/span, **projected** | Per-span knobs layers 1–2 cannot see |
| **3a. Span order** | `orq traces list-spans <trace-id> -o json -j '<projection>'` | one call/trace, **projected** | The ordered state sequence, for `where` |
| **3b. Conversation** | `orq traces conversation <trace-id> [<span-id>]` (7.4.0+) | one call/trace, normalized | What was actually said — messages, tool calls, tool results, reasoning |
| **4. Content + terminal state** | `orq agents get-response <agent-key> <agent_execution_span_id> -o json -j '<projection>'` | one call/trace, **projected** | **Agent targets only.** The run-level `finish_reason` (`max_iterations`, `max_time`, `length`, `stop`), which no span carries at trace level and `aggregate` therefore cannot sweep — see §3.5 |

### Why `aggregate` and not `list-facet-values`

`list-facet-values` is cheaper but **takes no filter value** — its only flags are `--filter-operator`, `--from`, `--to`, `--limit`. A facet sweep is therefore always workspace-wide, and it covers only the facetable subset (28 of 57 fields on 2026-08-26, 30 on 2026-10-14; missing `agent_id`, `tool_name`, `duration_ms`, `cost.total`, `agent.iterations.count`, `eval_*`). The field is positional: `orq traces list-facet-values agent_name --from … --to …`.

Use facets for a quick unscoped orientation. Use **`aggregate` for anything a finding depends on** — it takes `filters`, groups on any field including `attributes.*`, and computes `count` / `avg` / `sum`.

### Sweep body — worked example

```json
{
  "from": "2026-08-12T00:00:00Z",
  "to": "2026-08-26T00:00:00Z",
  "group_by": ["attributes.gen_ai.response.finish_reasons"],
  "filters": [{ "field": "agent_name", "op": "eq", "values": ["support-bot"] }],
  "compute": [{ "metric": "trace_id", "op": "count" }]
}
```

Returns one `{"group": {...}, "metrics": {"trace_id.count": N}}` row per group.

> **Filter an orq agent on `agent_name` with its key.** There is no `agent_key` field. On an orq-hosted agent (CLI 11.4.1, 2026-10-14), `agent_name eq "<key>"` matched all 57 of its traces, while `agent_id eq "<key>"` and `attributes.orq.agent.key eq "<key>"` each returned **zero rows without erroring**: `agent_id` holds the agent's ULID `_id`, not its key. Re-probe any zero against `agent_name` before trusting it (§0).

> A group with no value for the `group_by` field comes back as `"group": {}`. That is the **null bucket**, not an error. Count it or drop it deliberately — never read it as a parse failure.

---

## 2. Invocation details that fail loudly

**Bodies go in a file, never on stdin.** Piping JSON from PowerShell fails with `invalid character 'ï'` — the UTF-8 BOM. **Use one file per query type and overwrite it each time** — `body.json` for sweeps, `body.json` again for the next sweep. Never create a new file per call.

```powershell
[System.IO.File]::WriteAllText("$PWD\body.json", $json)   # no BOM, overwrite
orq traces aggregate --from-file body.json -o json
# next query: overwrite the same body.json
```

The same applies to `orq reporting query`: inline `--filters` JSON from PowerShell is mangled before it reaches the CLI. Use `--from-file`.

**Delete `body.json` when the run is done.**

**`from` / `to` are required in a body file and bounded by 30-day retention** (`skills/orq-cli/SKILL.md`, "Traces expire after 30 days"). A `traces aggregate` body without them returns HTTP 400; only omitted *flags* on `orq reporting query` fall back to seven days (§5.1). **NEVER hard-code a `--from`** — it ages past the boundary and starts erroring. Compute the window at call time, or use the relative form. **In a body file it must be RFC3339**; only the *flag* form accepts `7d` / `now-24h` / `now`, because the CLI sends a `--from-file` body as written (§5.1).

**`filters[].values` must be an array of STRINGS, even for a numeric field.** `values: [0]` is rejected with `HTTP 400: invalid value for string field values: 0`; `values: ["0"]` is accepted. A field's declared `type: "number"` in `list-fields` does not change this.

**Trace-level `status` does not see span errors.** On an orq agent whose model calls returned HTTP 400 (both the primary and the fallback), the root span stayed `ok` and the two `span.chat_completion` spans were `error`. `status eq "error"` matched **0** of those traces in both `search` and `aggregate`, and `group_by: ["attributes.http.response.status_code"]` returned only the null bucket, because aggregation works on the trace row. **`traces search` with `{"field":"attributes.http.response.status_code","op":"eq","values":["400"]}` does match at span level**: 3 traces on 201, each a real error (CLI 11.4.1, 2026-10-14). The error text is on the errored span: `get-span … -j '{s:span.summary.status,why:span.attributes.otel.status_description,http:span.attributes.http.response.status_code}'`.

**Null-safe existence checks: use `exists`, not `neq ""`.** `{"field":"evaluator_key","op":"neq","values":[""]}` matched all 201 traces of an agent that had no evaluator result at all, because a missing value is "not equal". `op: "exists"` (with `values: []`) returned 0, the true answer. `list-fields` lists `exists` / `not_exists` in each field's `operators`; check there before relying on them.

**`traces search` has exactly one legal sort:**

```json
"sort": [{ "field": "end_time", "order": "desc" }]
```

Anything else is a 400 (`skills/orq-cli/SKILL.md`, "Results are unordered, and only one sort exists"). **Without an explicit sort, rows are not time-ordered** — pass it every time. Paginate on `page_token`.

**JMESPath `length()` over a missing key crashes the CLI:**

```
FATAL ... Invalid type for: <nil>, expected: []jmespath.jpType{"string","array","object"}
```

Every projection must be null-safe. **Project the array and count client-side; never `length(...)` inside `-j`.**

---

## 3. Projections — the answer to "spans can be large"

`orq traces list-spans` on one real trace returned **115,868 bytes**. The `-j` projection is applied **by the CLI**, so only the projection enters context.

`-j` is a global flag. `list-spans --help` does not advertise it; it applies anyway.

**A `get-span` or `list-spans` call without `-j` is a defect, not a style choice.**

### Config detail (`get-span`)

```bash
orq traces get-span <trace-id> <span-id> -o json -j '{
  temp:   span.attributes.gen_ai.request.temperature,
  top_p:  span.attributes.gen_ai.request.top_p,
  max:    span.attributes.gen_ai.request.max_tokens,
  finish: span.attributes.gen_ai.response.finish_reasons,
  model:  span.summary.model,
  dur:    span.summary.duration_ms
}'
# -> {"dur":60147,"finish":null,"max":null,"model":"gemma-4-31b","temp":null,"top_p":null}
```

Measured: a ~4 KB hydrated span collapses to ~7 lines. Ten spans of config detail costs well under 1 KB.

### Span order (`list-spans`) — what `where` is built from

```bash
orq traces list-spans <trace-id> -o json \
  -j "data[].{id:span_id,parent:parent_span_id,name:name,type:type,start:started_at,status:status}"
```

Measured on an 83-span agent trace: **115,868 → 22,666 bytes** with an eight-field projection; the six fields above land well under 10 KB. One call per failed trace.

The response is `{data[], has_more, next_page_token, object}`. Each row is a **flat canonical summary with no nested `attributes` object at all** — `span_id`, `parent_span_id`, `name`, `operation`, `type`, `status`, `started_at`, `ended_at`, `duration_ms`, `model`, `provider`, `cost`, `usage`, `trace_id`, `has_detail`.

- **The state discriminator is `type` + `name`.** `type` gives the category (`trace` for the root, `span.chat_completion`, `span.agent_tool_execution`); `name` gives the step (`Read`, `Bash`, `chat claude-opus-4-6`). `operation` was identical to `name` on every span observed — treat it as redundant.
- **Sort client-side by `started_at`.** Rows came back ascending on the trace tested, but that is n=1 and undocumented.
- `span.attributes.orq.span_type` and `span.attributes.gen_ai.tool.name` are **not here** — they are `get-span` territory, and `where` does not need `get-span` at all.

> **Limits on the probe behind this section.** It first ran against Claude Code sessions and AI-Router passthroughs, where the hierarchy was flat (every `parent_span_id` pointed at the root), so "after which state" rests on timestamp order there. On an **orq-hosted agent** (57 traces, CLI 11.4.1, 2026-10-14) the parent links are real: `trace` (`AgentInvoke`) → `span.agent_execution` (named after the agent key) → one `span.chat_completion` per model turn. No `span.agent_tool_execution` appeared, so a tool call shows up only as an extra chat-completion turn; read the call itself with `traces conversation` (§3.5).

---

## 3.5. `agents get-response` — the layer that is not a span

**On an orq Agent target, the agent-level terminal state is not in the trace store.** It lives on the agents endpoint. The message text and tool calls *are* readable from traces now, through `orq traces conversation` (§1 layer 3b); and each model turn's own `finish_reasons` is on its `span.chat_completion` span (§4). What only this endpoint gives is the reason the **run** ended, which is how `max_iterations` and `max_time` show up:

```bash
# task-id is the span.agent_execution span id. The "| [0]" and --raw are REQUIRED:
# without them -j returns a formatted JSON array, which word-splits and 404s.
AE=$(orq traces list-spans <trace-id> --raw -j "data[?type=='span.agent_execution'].span_id | [0]")
orq agents get-response <agent-key> "$AE" -o json -j 'finish_reason'
```

*(Verified end to end 2026-08-26 on CLI 4.14.0: returns `"max_iterations"`.)*

**1. `finish_reason` — the agent-level terminal state.** This is *not* `attributes.gen_ai.response.finish_reasons`, which is null on **246/246** orq-hosted agent traces across **all 6 agents** in the probed workspace (measured 2026-08-26, 14-day window) while populating richly workspace-wide. Observed values, one agent per row:

| Agent | Binding knob | n | `finish_reason` |
|---|---|---|---|
| loop-capped | `max_iterations: 2` | 15/15 | `max_iterations` |
| loop-capped (second) | — | 5/5 | `max_iterations` |
| token-capped | `max_tokens: 800` | 10/10 | **`length`** |
| token-capped (second) | — | 10/10 | **`length`** |
| uncapped control | `max_iterations: 15` | 10/10 | `stop` |
| uncapped (second) | — | 2/2 | `stop` |

**`length` is the token-truncation case and `max_iterations` the loop-cap case** — the two symptoms this skill family exists to tell apart, and neither is expressible in any span attribute. `{stop, max_iterations, length}` is what was *observed*, not a documented enumeration: treat an unseen value as possible.

**2. `output[]` — the FINAL TURN ONLY, not a transcript.** `output[]` was length 1 on every trace tested, across 4 agents and all three terminal states. Its `parts[]` entries are `kind: "text"` (carrying `text`) or `kind: "tool_call"` (carrying `tool_name`, `arguments`, `tool_call_id`).

The final assistant text is reliable: `output[0].parts[?kind=='text'].text` returned one well-formed message (9,221 chars on an agent required to write 800+ words), with no truncation or redaction markers in any sample.

> **It does NOT give a tool-call inventory. Never use it as one.** A `tool_call` part appears only when the run was cut off *mid-call*. A normally-completed run shows none even when tools certainly ran — a control agent whose instructions mandate three `web_scraper` calls, with 5 chat-completion turns, returned **zero** `tool_call` parts. `span.agent_tool_execution` is not emitted either. **Read tool use from `orq traces conversation <trace-id>` instead:** it renders each `<tool_call name=…>` with its arguments and the tool-role result (verified on 57 agent traces, CLI 11.4.1, 2026-10-14; `current_date` calls and results appeared on every trace that had two chat-completion turns).

**Every bad id returns the same 404.** `{"message":"Agent response not found for this task"}` comes back identically for a trace id, a root span id, a chat-completion span id, the correct span id with the wrong agent key, and a garbage string. The error cannot tell you which mistake you made — re-derive the id from `list-spans` rather than guessing.

**Project it.** A full response embeds every scraped page and one observed run carried 176k tokens in a single turn. `-j 'finish_reason'` is a few bytes; an unprojected `-o json` is not. **Never call this without `-j`.**

## 4. Where the config knobs live

`orq traces list-fields` returns 57 fields (re-read it; do not trust that number).

**Top-level, filterable:** `status`, `model`, `base_model`, `provider`, `agent_id`, `agent_name`, `deployment_environment`, `project_id`, `session_id`, `operation`, `name`, `product`, `tool_name`, `duration_ms`, `cost.total`, `tokens.total`, `agent.iterations.count`, `eval_passed`, `eval_score`, `eval_label`, `evaluator_key`.

**Attribute-scoped** (facetable, filterable, groupable): `attributes.gen_ai.response.finish_reasons`, `attributes.gen_ai.usage.input_tokens`, `attributes.gen_ai.tool.type`, `attributes.orq.span_type`, `attributes.orq.model.id`, `attributes.orq.billing.*`, `attributes.http.response.status_code`, `attributes.type`.

**Not in the registry at all — per-span only:** `temperature`, `top_p`, `max_tokens`, and the entire `openresponses.*` block.

> **Queryable is not groupable is not computable.** The three capabilities are independent. `list-fields` now carries `groupable` and `facet` booleans per field (CLI 11.4.1: `-j "data[?groupable].name"`), so read groupability from there; `compute` support is still not listed, and a filterable field can still match nothing. Verified:
>
> | Field | `filters` | `group_by` | `compute` |
> |---|---|---|---|
> | `agent.iterations.count` | **silently 0 rows** | **400** `field "agent.iterations.count" cannot be grouped` | **400** `unsupported aggregate metric` |
>
> **Probe a field before building a finding on it.** The `group_by` and `compute` rejections are at least loud. **The `filters` cell is §0's hazard in its purest form:** `agent.iterations.count` is declared filterable with operators `[eq,gt,gte,lt,lte]`, throws no error, and matches **zero rows every time** — even `gte "0"`, a condition true of any non-negative number — because nothing backs the field in `span.attributes`. A filter on it silently empties whatever query it touches. For agent turn depth, count `span.chat_completion` spans instead.

Per span, on an **orq-hosted Responses-API span**:

| Location | Knobs |
|---|---|
| `span.attributes.gen_ai.request` | `temperature`, `top_p`, `max_tokens`, `model` |
| `span.attributes.gen_ai.response` | `finish_reasons`, `model` |
| `span.attributes.gen_ai.tool.definitions` | full tool definitions → tool-inventory count. **An object keyed `"0"`, `"1"`, …, not an array** (agent span, 2026-10-14): project `values(span.attributes.gen_ai.tool.definitions)[].function.name` and count client-side |
| `span.attributes.gen_ai.usage` | `prompt_tokens_details.cached_tokens`, `reasoning.output_tokens` |
| `span.attributes.openresponses` | `truncation`, `reasoning` (`{"effort":"medium",…}`), `tool_choice`, `tools`, `parallel_tool_calls`, `max_output_tokens`, `frequency_penalty`, `presence_penalty`, `service_tier`, `status` |

> **Parsing gotcha.** Inside `openresponses`, `reasoning` / `text` / `tool_choice` / `tools` are **JSON-encoded strings**, and `max_output_tokens` is a **string** (`"8000"`) while `gen_ai.request.max_tokens` is an **int** (`8000`). **Prefer the `gen_ai.*` normalised view**; fall back to `openresponses.*` only for the knobs it alone carries (`truncation`, `reasoning.effort`, `tool_choice`, `parallel_tool_calls`).

### Coverage limit — these knobs are not everywhere

They exist on **orq-hosted invocation spans**. They are **absent** on:

- **OTLP-ingested** third-party spans — `gen_ai` carries only model/provider/usage; no `request.temperature`, no `openresponses` (verified on an `orq.claude_code.session` trace).
- **AI-Router chat-completions passthrough** spans — same gap; the projection above returned `temp:null, max:null, finish:null` (verified on a `chat.cerebras` trace). **Router `/v1/responses` traffic is different**: its `responses.*` spans carry the `openresponses` block, with `tools`, `truncation`, `temperature`, `reasoning`, `max_output_tokens` and `instructions` (5 of 5 `responses.openai` traces, 2026-10-14). The keys can be present with an empty value (`truncation: ""` on all 5), and an empty or missing value is `unobservable`, not a setting.
- **orq Agent invocation** spans — `gen_ai.request` carries only `{temperature, max_tokens, model, stream}`. **No `top_p` and no `openresponses` block at all**, so `truncation` and `reasoning_effort` are unreadable from spans. Verified across 55 traces on each of two agents (2026-08-26). **Per-turn `finish_reasons` is there, on the `span.chat_completion` span** (`["length"]` on a turn that hit `max_tokens`; 2026-10-14). It is only null at the trace level that `aggregate` groups on, so it cannot be swept: sweep the run-level reason with `agents get-response` (§3.5) and read the turn-level one per span. Tool activity is read with `traces conversation` (§3.5).

**Anything unreadable is `unobservable` with a reason. Absence never reads as a pass.**

---

## 5. `orq reporting query` — the supplement

A catalogue API over analytics rollups. It carries three signals the trace registry does not, and it **cannot replace the sweep**.

- **18 metrics:** `genai.requests` · `genai.tokens` · `genai.cost` · `genai.errors` · `genai.error_rate` · `genai.latency.p50|p95|p99` · **`genai.ttft.avg|p50|p95`** · **`genai.evaluator.runs|pass_rate|score.avg`** · `genai.guardrail.runs|block_rate|triggered` · `genai.usage`. Enumerate at runtime by passing an invalid `--metric` and reading the 400.
- **`group_by` for `genai.requests`:** `model`, `provider`, `agent`, `deployment`, `evaluator`, `project`, `tool`. **Rejected:** `status`, `finish_reason`, `agent_name`, `entity`, `error_type` — which is exactly why it cannot do the terminal-state sweep. Dimensions are per-metric; probe rather than assume.
- **Use `mode: "scalar"`.** It returns one flat row per group — `{dimensions:{…}, metrics:{…}, timestamp:null}` — instead of the time buckets `query_analytics` forces. Add `include_totals` for a window-wide total under `.totals.metrics`.
- **Target scoping:** `filters: [{"field":"agent","op":"eq","values":["<agent _id>"]}]`. `values` is a **list and must be non-empty**; a scalar `value` is a 400. **The `agent` dimension holds the agent's ULID `_id`, not its key** (`group_by: ["agent"]` returns `{"agent":"01M4X7…"}`). Filtering on the key **matches nothing without erroring**: an empty `data` array when grouped, a scalar `0` when not (CLI 11.4.1, 2026-10-14). Take the `_id` from `agents retrieve`.
- **Cross-check before relaying any reporting number.** On 2026-10-14 an agent with 57 traces in the window, filtered by its correct `_id`, reported `genai.requests 0`, `genai.cost 0` and `genai.latency.p50 0`, still 0 about 70 minutes after its first run. Agent invocations not being counted in the rollup is the likelier cause than lag; unresolved. **If the scoped `genai.requests` does not match the trace count from the sweep, record every reporting-sourced item as `unobservable` with that mismatch as the reason.** A zero here is not "free" or "no evaluator runs".
- **Some metrics refuse agent scoping.** `genai.ttft.*` with an `agent` filter returns HTTP 400 `does not support entity dimensions`, and `genai.evaluator.*` returns HTTP 400 `filter field not allowed for metric: "agent"`. Per-agent TTFT is then `unobservable: [ttft]`; per-agent evaluator rates come from the trace fields `eval_passed` / `eval_score` instead.
- The request body requires `from` / `to`, but the CLI fills in the last seven days when the flags are omitted. Pass an explicit relative window for repeatable scripts. The 30-day retention bound applies as in §2.

**TTFT lives here, and an all-zero result is not "fast".** A live `genai.ttft.p50` scalar query returned 20 model groups with **every value `0`** and a window total of `0` — consistent with the metric being populated only for streaming requests. Treat an all-zero TTFT result as **`unobservable: [ttft]`**, never as a measurement.

### 5.1 The flag surface

The body fields above are also flags, so a metrics question needs no body file at all. Read from the generated command surface at CLI 10.3.0 (`cli/generated/reporting_commands.go`); `orq reporting query --help` prints the same list.

> `help-input` is registered on the **root** command, not per subcommand. `orq help-input` prints the body-and-shorthand syntax; `orq reporting query help-input` does not — `reporting query` takes trailing args as a shorthand body fragment, so it is parsed as input, not as a subcommand. Use `orq reporting query --help` for that command's own flags.

| Flag | Body field | Meaning |
|---|---|---|
| `--metric` | `metric` | **required**, exactly one of the 18 above |
| `--from` / `--to` | `from` / `to` | Optional CLI flags; omitted together, they default to the last seven days. Both accept RFC3339, a bare date, **and relative values** — `7d`, `24h`, `now-24h`, `now`. Required in a body file. |
| `--grain` | `grain` | bucket size: omit for `auto` (server picks from the range), or `minute` / `hour` / `day` |
| `--mode` | `mode` | `timeseries` (default) or `scalar` |
| `--group-by` | `group_by` | **repeatable** (`--group-by model --group-by provider`) |
| `--filters` | `filters` | one JSON string, array of `{field, op, values}` |
| `--sort` | `sort` | `desc` or `asc` |
| `--time-zone` | `time_zone` | IANA zone for bucketing |
| `--include-totals` | `include_totals` | add a window-wide `.totals.metrics` object alongside `.data` |
| `--limit` | `limit` | Maximum bucket rows returned in either mode. The generated help says the default is 1000; the maximum is 5000. Passing 10000 returns HTTP 400, rather than clamping the value. |
| `-o json` | — | machine output. **There is no `--json` flag** — `orq --json …` exits non-zero with `unknown flag: --json` (`cli/custom/register_test.go`, `TestJSONFlagIsGone`) |

**Relative `from` / `to` work in the flag form only.** The CLI normalizes `7d` / `now` to RFC3339 for a generated flag and for shorthand args, but **a body read from `--from-file` or stdin is sent as written** — `bartolo/cli/input.go`, `normalizeShorthandDateTimes`: *"a body from --from-file or stdin is machine-written, so it is sent as given rather than silently rewritten."* `from`/`to` are `format: date-time`, so `"from": "7d"` inside a body file is rejected by the server. Since §2 requires the body in a file, **put the window on `--from` / `--to` flags and leave it out of the body.** This is also why "never hard-code a `--from`" costs nothing here: `--from 7d` is already relative.

**`filters[].field` is a closed enum here, and it is not the trace vocabulary.** The reporting `Filter.field` enum holds 35 reporting dimensions (`project`, `identity`, `provider`, `model`, `product`, `api_key`, `status_code`, `http_status_code`, `credential_type`, `billing_billable`, `dimension`, `dimension_type`, `tag`, `agent`, `tool`, `deployment`, `evaluator`, `dataset`, `prompt`, `policy`, `conversation`, `thread`, `memory_store`, `knowledge`, `sheet`, `guardrail_origin`, `evaluator_name`, `evaluator_type`, `evaluator_version`, `result_type`, `evaluation_stage`, `guardrail_stage`, `evaluator_stage`, `guardrail_action`, `result_label`), and `op` is limited to `eq` / `neq` / `in` / `not_in`. `TraceFilter.field` (§1) is a free-form name resolved from `orq traces list-fields`. **The two share the JSON shape, not guaranteed field names.** In a live check, reporting rejected the trace field `deployment_environment` with HTTP 400, and trace search rejected the reporting field `deployment` with HTTP 400. Resolve each side at runtime (§0); do not assume a cross-mapping.

### 5.2 `timeseries` vs `scalar`

- `timeseries` (default) buckets the metric by time at `grain` — a series to trend or plot.
- `scalar` collapses the window to one row per group, ordered by value. With `group_by` that is a top-list; with no `group_by` it is a single number. `--limit` caps returned rows.

### 5.3 Worked examples

**Guard every `jq` pipeline with `pipefail`.** On a rejected request the CLI exits 1, writes the error to stderr, and leaves stdout empty. Without `pipefail`, `jq` reads the empty input and exits 0, so the pipeline appears successful.

```bash
set -o pipefail

# total genai cost, last 7 days — one number
orq reporting query --metric genai.cost --from 7d --to now --mode scalar -o json \
  | jq -r '.data[0].metrics["genai.cost"]'

# cost per model, top 10, last 24h
orq reporting query --metric genai.cost --from now-24h --to now \
  --mode scalar --group-by model --sort desc --limit 10 -o json

# requests per day over the window, with a total
orq reporting query --metric genai.requests --from 30d --to now \
  --mode timeseries --grain day --include-totals -o json

# p95 latency for one deployment, hourly
orq reporting query --metric genai.latency.p95 --from now-24h --to now \
  --mode timeseries --grain hour \
  --filters '[{"field":"deployment","op":"eq","values":["checkout-agent"]}]' -o json
```

Each scalar data row has the shape `{dimensions:{…}, metrics:{…}, timestamp:null}` (§5), so its value is under `.metrics["<metric name>"]` — there is no `.value`. With `--include-totals`, the window-wide value is under `.totals.metrics["<metric name>"]`.

Live re-probed on 2026-10-01 with CLI 8.7.0-rc.15 (API 4.15.0-rc.52): the scalar cost example returned a numeric value at `.data[0].metrics["genai.cost"]`; the grouped scalar and daily timeseries examples returned data rows; a query with no window flags returned a seven-day request window; and `--include-totals` returned `.totals.metrics` beside `.data`. `--limit 10000` returned HTTP 400 with CLI exit 1; without `pipefail`, piping that rejected query into `jq` exited 0. Cross-dialect `deployment` / `deployment_environment` filters each returned HTTP 400. Re-run on 2026-10-08 with CLI 11.5.0-rc.6 against production API 4.16: the scalar cost path, the seven-day default, `.totals.metrics` and the `--limit 10000` HTTP 400 were unchanged.

> From PowerShell, `--filters` inline JSON is mangled before it reaches the CLI (§2). Put the whole body in a file there and pass `--from-file`, keeping `--from` / `--to` as flags for the reason above.

### 5.4 `orq telemetry` — the unified envelope (11.5.0 line, probe first)

API 4.16 serves `POST /v3/telemetry/query`, `GET /v3/telemetry/capabilities` and `GET /v3/telemetry/facet-values` in production. The CLI exposes them as `orq telemetry query | list-capabilities | list-facet-values` in the 11.5.0 line (verified on `11.5.0-rc.6`, built against API 4.16.0-rc.58; on 2026-10-08 npm `latest` was still 11.4.0). **The 11.4.0 binary has no `telemetry` command** (`unknown command "telemetry"`); run `orq telemetry --help` once and fall back to `orq reporting query` when it fails. `orq telemetry query --help` states that `ReportingService.QueryReport` and `TraceQueryService.AggregateTraces` "remain supported compatibility contracts", so §5.1–5.3 still hold.

What changes against `orq reporting query`:

| | `orq reporting query` | `orq telemetry query` |
|---|---|---|
| Source | reporting rollups only | `--source TELEMETRY_SOURCE_TRACES` \| `_METRICS` \| `_LOGS` — **required**; omitted it is `UNSPECIFIED` and returns HTTP 400 |
| Metric | `--metric <name>` | `--compute '[{"metric":"<name>","op":"<op>"}]'`, several per query. The allowed ops are each metric's `operations` in `list-capabilities`: every `genai.*` metric takes exactly one (`genai.cost` → `sum`, `genai.requests` → `count`, `genai.latency.p95` → `p95`), while `duration_ms` takes six and `cost.total` two. A wrong op is HTTP 400 naming the allowed one |
| Window | defaults to the last seven days | `--from` / `--to` **required** (HTTP 400 without them); the 30-day workspace retention (§2) rejects older windows with HTTP 400 before the 90-day engine cap applies |
| Row shape | `{dimensions, metrics:{"genai.cost":…}}` | `{group, metrics:{"genai.cost:sum":…}}` — the key is `<metric>:<op>` |
| Totals | `.totals.metrics["genai.cost"]` | `.totals.metrics["genai.cost:sum"]` (`.totals` is itself a row) |
| `--limit` | rows, default 1000, max 5000 | scalar: rows, default 100. Timeseries: max distinct groups, and exceeding it fails the query rather than truncating |
| Truncation | `has_more: true`, empty `.meta.warnings` | `has_more: true` plus a `.meta.warnings[]` entry |
| Extra | — | `--oql` and `--query` trace selection (TRACES source only), `--project-id`, `--interval-seconds` |

**Discover before you query.** `orq telemetry list-capabilities -o json` lists every source with its metrics, each metric's allowed `operations`, `modes` and groupable `dimensions` — the probe this file otherwise asks you to do by reading 400s. On 2026-10-08 it returned 33 TRACES metrics (22 of them `genai.*`, plus `cost.*`, `duration_ms`, `error_count` and others), 40 METRICS metrics and 3 LOGS metrics. The `genai.*` set is not the §5 list: it adds `genai.latency.avg`, `genai.ttft.runs` and evaluator error/fail rates, and has no `genai.usage`. Re-read it; do not trust those counts.

```bash
set -o pipefail
# total genai cost, last 7 days — same number as the §5.3 reporting example
orq telemetry query --source TELEMETRY_SOURCE_TRACES --from 7d --to now --mode scalar \
  --compute '[{"metric":"genai.cost","op":"sum"}]' -o json \
  | jq -r '.data[0].metrics["genai.cost:sum"]'

# cost per model, top 10, last 24h, with a window total
orq telemetry query --source TELEMETRY_SOURCE_TRACES --from now-24h --to now --mode scalar \
  --group-by model --limit 10 --include-totals \
  --compute '[{"metric":"genai.cost","op":"sum"}]' -o json
```

Live-probed 2026-10-08 with CLI 11.5.0-rc.6 against production API 4.16: the seven-day scalar cost returned `356.03784829` through both `orq telemetry query` and `orq reporting query`; the grouped query returned `has_more: true` with a truncation warning at `--limit 3`; a daily timeseries returned one row per day with an RFC3339 `timestamp`; omitting `--from`/`--to`, passing `op: "avg"` for `genai.cost`, and `--from 60d` (workspace retention) each returned HTTP 400.

---

## 6. What does not work

| Primitive | Status |
|---|---|
| `orq traces query-oql` | **Grammar is a stub.** `fetch traces \| limit N` works; `stats`, `summarize`, `aggregate`, and `filter <field> = "<v>"` are all rejected 400. Nothing here needs it. |
| MCP `query_analytics` / `get_analytics_overview` as the sweep | **Does not cover it.** `group_by` is a closed five-value enum (`provider`, `model`, `project_id`, `http_status_code`, `agent_name`) — **no `finish_reason`, no span `status`**, so the terminal-state distribution is unreachable. It also demands a single `project_id` on a workspace-scoped key and returns time-bucketed rollups. Keep it for cost / latency / error *volume* per `agent_name` as a supplement. |
| `orq deployments update` / `create` | **Does not exist.** `orq deployments` exposes only `get-config`, `invoke`, `list`, `stream`. Deployment prompt writes go through `POST /v2/prompts/<id>/versions`; non-prompt deployment config is read-only — recommend in prose. |

---

## 7. Write path — `orq agents update`

`orq agents update <agent-key>` does partial updates of `model`, `instructions`, `system_prompt`, `settings`, `skills`, `knowledge_bases`, `memory_stores`, `fallback_models`, `team_of_agents`, `variables`, `role`, and takes `--version-increment` and `--version-description`.

A live agent's config shape:

```json
{
  "key": "agenttest",
  "model": { "id": "deepseek/deepseek-v4-flash" },
  "instructions": "...", "role": "Web Research Specialist",
  "settings": { "max_cost": 0, "max_execution_time": 300, "max_iterations": 15,
                "tool_approval_required": "none", "tools": [] },
  "knowledge_bases": [], "memory_stores": [], "skills": [],
  "version": "1.0.0", "path": "Prompt Learning vs DSPY"
}
```

### The parameter schema, with real bounds

```
model: { id, parameters: { temperature (0–2), top_p (0–1), max_tokens, max_completion_tokens,
                           reasoning_effort (none|minimal|low|medium|high|xhigh), tool_choice,
                           parallel_tool_calls, frequency_penalty, presence_penalty, seed, stop,
                           top_k, response_format },
         retry: { count (1–5), on_codes } }      # same shape on each fallback_models[] entry
settings: { max_cost, max_execution_time (2–600), max_iterations (1–100),
            tool_approval_required, tools[], evaluators[], guardrails[] }   # evaluators/guardrails carry sample_rate
```

**Clamp every recommendation to these bounds** rather than proposing a value the API rejects. The schema is served **live** by the MCP server, so treat the bounds as current-as-of-probe: re-read it if a write is rejected.

### Read-modify-write every nested object you touch, whole

`orq agents update --help` says *"Only the fields provided in the request body will be updated"* — but does not say whether a **nested** object deep-merges or replaces.

If it replaces, `{"settings":{"max_iterations":20}}` destroys `tools[]`, `max_cost`, `max_execution_time` and `tool_approval_required`. `model` carries the identical hazard: it is `string | {id, parameters, retry}`, so a bare `{"model":{"parameters":{"temperature":0.5}}}` can drop `model.id`.

**Retrieve the current object, change one key, send it back whole.** Correct under either merge behaviour, costs one extra `retrieve`, and means the semantics never have to be resolved.

**`settings.tools[]` does not round-trip. Read shape is not write shape.** A retrieve returns each tool as `{id, action_type, display_name, conditions, requires_approval}`; the update schema requires a **`type`** discriminator and rejects the read-side fields. Sending back exactly what you read fails with a `ZodError` 400 on `settings.tools[N]`. **Load the live write schema from the MCP `update_agent` tool before building any `settings` patch**, then translate:

| Read (`agents retrieve`) | Write (`agents update`) |
|---|---|
| `action_type: "web_scraper"` | `type: "web_scraper"` |
| `requires_approval: false` | `requires_approval: false` (keep) |
| `id`, `display_name`, `conditions` | **omit** — on write, `id`/`key` mean a *custom* tool reference, not a built-in |

**Never drop `tools[]` from the patch to dodge the 400.** That is precisely the silent tool deletion this rule exists to prevent. Translate, do not delete.

**`fallback_models` is another read/write mismatch.** It is written at the top level (`{"fallback_models":["groq/openai/gpt-oss-120b"]}`; a list of `{"id": …}` objects is also accepted) but read back nested, at `model.fallback_models`. A top-level `fallback_models` projection on a retrieve or create response returns `null` even when the fallback is set. Verify a write at `model.fallback_models` (CLI 11.4.1, 2026-10-14).

**Groq rejects a strict tool schema without `additionalProperties: false`.** orq sends every agent tool with `strict: true` (`get-span … -j 'values(span.attributes.gen_ai.tool.definitions)[].function.strict'` returned `true` for a built-in and a code tool). On a `groq/*` model, a tool whose `parameters` object lacks `"additionalProperties": false` fails every model call with HTTP 400 `` `additionalProperties:false` must be set on every object ``, the fallback included. The same call without `strict` succeeds (router probe, 2026-10-14). When adding or editing a code or function tool for an agent that may run on Groq, set it on every object in the schema.

### Version every write

`--version-increment` and `--version-description` are documented as **"Optional"** on the CLI, so a write that omits them **succeeds and publishes no version** — costing the rollback story silently. **ALWAYS pass both.** This is a guardrail the skill enforces, not one the API enforces for you.

A `--version-description` longer than **300 characters** is reported as rejected — `untested`, no probe was run. Keep descriptions short regardless; the field is a changelog line, not a report.

---

## 8. Quick reference

```bash
# window (compute it — never hard-code; 30-day retention)
# GNU coreutils (Linux, Git Bash):
FROM=$(date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%SZ); TO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# BSD/macOS date takes -v instead:
FROM=$(date -u -v-14d +%Y-%m-%dT%H:%M:%SZ);          TO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# PowerShell:
# $FROM = (Get-Date).ToUniversalTime().AddDays(-14).ToString('yyyy-MM-ddTHH:mm:ssZ')
# $TO   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

orq traces list-fields -o json                    # resolve names first
orq traces list-facets -o json

orq traces aggregate  --from-file body.json -o json
orq traces search     --from-file body.json  -o json -j 'data[].trace_id'   # same file, overwritten
orq traces list-spans <trace> -o json -j "data[].{id:span_id,name:name,type:type,start:started_at,status:status}"
orq traces get-span   <trace> <span> -o json -j '{temp:span.attributes.gen_ai.request.temperature}'

orq reporting query   --from-file body.json -o json       # same file, overwritten
orq telemetry list-capabilities -o json                # only when `orq telemetry --help` succeeds (§5.4)
orq agents retrieve   <key> -o json
orq agents update     <key> --from-file patch.json --version-increment patch --version-description "..."
# clean up: delete body.json and patch.json when done
```
