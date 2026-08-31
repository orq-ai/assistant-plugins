# Trace Queries — the `orq` CLI contract

Shared reference for `orq-analyze-traces` and `orq-improve-agent`. Everything here was probed live against `orq` CLI 4.13.1 on 2026-08-26, and re-verified against **4.14.0** the same day with no behavioural drift observed.

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
- **Full conversation content:** `mcp__orq-workspace__get_span span_id=... mode=full` — returns structured message arrays with all turns, tool calls, tool responses, system instructions, and per-message `finish_reason`. This is also the only reliable path for `finish_reason` on agent traces when `agents get-response` is unavailable.
- **Related entities:** CLI `orq tools retrieve`, `orq knowledge-bases retrieve`, `orq memory-stores retrieve`, `orq evals get` — resolve the IDs from the agent config into full definitions.

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
orq traces list-fields --json     # the queryable fields
orq traces list-facets --json     # the facetable subset
```

> **Canonical source: `skills/orq-cli/SKILL.md`, "The trace filter contract" onward.** That skill owns the CLI's general query constraints — field discovery, the `end_time desc` sort, the 30-day retention `400`. This file states each as a one-line rule because it is read at call time, and links there for the detail. **Correct them there first**; a rule fixed in one file and missed in the other is exactly the stale guidance §0 is about.

**ALWAYS** resolve names from those two commands before building a query body. **NEVER** copy a field name out of this document into a query. When a name you expected is absent, say so and stop — do not fall back to a filter that silently matches nothing.

**A mid-deploy query returns a confident, empty, non-erroring result.** During the 2026-08-26 rollout, `attributes.*` facets returned `[]` and `attributes.*` filters returned zero rows, both without an error. Re-probe before trusting a zero: run the same query with the filter removed and confirm the population is non-empty.

### Every capability claim in this file must carry the command that produced it

This document has twice described behaviour that was never run: a capability cell filled in by inference, and a *"the only way to X"* sentence that a five-minute check refuted. Both read as verified because they sat beside genuinely verified text.

**Rule: a cell in a capability table, or any sentence of the form "X is the only way to Y", must be accompanied by the command that produced it. If it was not run, write `untested` in the cell.** `untested` is useful. A plausible guess formatted as a measurement is worse than a blank, because the next reader cannot tell the two apart.

---

## 1. The layered read

| Layer | Command | Cost | Answers |
|---|---|---|---|
| **1. Population sweep** | `orq traces aggregate --from-file <body>.json --json` | one call, one row per group | Distributions for any signal, **scoped to the target** via `filters` |
| **2. Row selection** | `orq traces search --from-file <body>.json --json -j 'data[].trace_id'` | one call, ids only | Which traces exhibit the swept condition |
| **3. Config detail** | `orq traces get-span <trace-id> <span-id> --json -j '<projection>'` | one call/span, **projected** | Per-span knobs layers 1–2 cannot see |
| **3a. Span order** | `orq traces list-spans <trace-id> --json -j '<projection>'` | one call/trace, **projected** | The ordered state sequence, for `where` |
| **4. Content + terminal state** | `orq agents get-response <agent-key> <agent_execution_span_id> --json -j '<projection>'` | one call/trace, **projected** | **Agent targets only.** The real `finish_reason` and the message text, neither of which exists on any span — see §3.5 |

### Why `aggregate` and not `list-facet-values`

`list-facet-values` is cheaper but **takes no filter value** — its only flags are `--filter-operator`, `--from`, `--to`, `--limit`. A facet sweep is therefore always workspace-wide, and it covers only 28 of 57 fields (missing `agent_id`, `tool_name`, `duration_ms`, `cost.total`, `agent.iterations.count`, `eval_*`).

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

> The target field is `agent_id` or `agent_name` — there is no `agent_key` in the trace registry, even though the agent CLI and `orq reporting query` both address an agent by its key. Resolve which one exists before filtering (§0).

> A group with no value for the `group_by` field comes back as `"group": {}`. That is the **null bucket**, not an error. Count it or drop it deliberately — never read it as a parse failure.

---

## 2. Invocation details that fail loudly

**Bodies go in a file, never on stdin.** Piping JSON from PowerShell fails with `invalid character 'ï'` — the UTF-8 BOM. **Use one file per query type and overwrite it each time** — `body.json` for sweeps, `body.json` again for the next sweep. Never create a new file per call.

```powershell
[System.IO.File]::WriteAllText("$PWD\body.json", $json)   # no BOM, overwrite
orq traces aggregate --from-file body.json --json
# next query: overwrite the same body.json
```

The same applies to `orq reporting query`: inline `--filters` JSON from PowerShell is mangled before it reaches the CLI. Use `--from-file`.

**Delete `body.json` when the run is done.**

**`--from` / `--to` are required, RFC3339, and bounded by 30-day retention** (`skills/orq-cli/SKILL.md`, "Traces expire after 30 days"). **NEVER hard-code a `--from`** — it ages past the boundary and starts erroring. Compute the window at call time.

**`filters[].values` must be an array of STRINGS, even for a numeric field.** `values: [0]` is rejected with `HTTP 400: invalid value for string field values: 0`; `values: ["0"]` is accepted. A field's declared `type: "number"` in `list-fields` does not change this.

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
orq traces get-span <trace-id> <span-id> --json -j '{
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
orq traces list-spans <trace-id> --json \
  -j "data[].{id:span_id,parent:parent_span_id,name:name,type:type,start:started_at,status:status}"
```

Measured on an 83-span agent trace: **115,868 → 22,666 bytes** with an eight-field projection; the six fields above land well under 10 KB. One call per failed trace.

The response is `{data[], has_more, next_page_token, object}`. Each row is a **flat canonical summary with no nested `attributes` object at all** — `span_id`, `parent_span_id`, `name`, `operation`, `type`, `status`, `started_at`, `ended_at`, `duration_ms`, `model`, `provider`, `cost`, `usage`, `trace_id`, `has_detail`.

- **The state discriminator is `type` + `name`.** `type` gives the category (`trace` for the root, `span.chat_completion`, `span.agent_tool_execution`); `name` gives the step (`Read`, `Bash`, `chat claude-opus-4-6`). `operation` was identical to `name` on every span observed — treat it as redundant.
- **Sort client-side by `started_at`.** Rows came back ascending on the trace tested, but that is n=1 and undocumented.
- `span.attributes.orq.span_type` and `span.attributes.gen_ai.tool.name` are **not here** — they are `get-span` territory, and `where` does not need `get-span` at all.

> **Two limits on the probe behind this section.** It ran against Claude Code sessions and AI-Router passthroughs, **not an orq-hosted agent invocation**, so the richer span vocabulary of a real agent trace is unverified. On those traces the hierarchy was flat (every `parent_span_id` pointed at the root), so "after which state" rests on timestamp order rather than parent links.

---

## 3.5. `agents get-response` — the layer that is not a span

**On an orq Agent target, the terminal state and the message text are not in the trace store at all.** `traces get`, `get-span`, `list-spans` and the MCP read path all return metadata only. Both live on the agents endpoint:

```bash
# task-id is the span.agent_execution span id. The "| [0]" and --raw are REQUIRED:
# without them -j returns a formatted JSON array, which word-splits and 404s.
AE=$(orq traces list-spans <trace-id> --raw -j "data[?type=='span.agent_execution'].span_id | [0]")
orq agents get-response <agent-key> "$AE" --json -j 'finish_reason'
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

> **It does NOT give a tool-call inventory. Never use it as one.** A `tool_call` part appears only when the run was cut off *mid-call*. A normally-completed run shows none even when tools certainly ran — a control agent whose instructions mandate three `web_scraper` calls, with 5 chat-completion turns, returned **zero** `tool_call` parts. With `span.agent_tool_execution` never emitted either, **whether an agent's tools ran is currently unobservable from the CLI.** Record it as `unobservable`; do not infer it from either source.

**Every bad id returns the same 404.** `{"message":"Agent response not found for this task"}` comes back identically for a trace id, a root span id, a chat-completion span id, the correct span id with the wrong agent key, and a garbage string. The error cannot tell you which mistake you made — re-derive the id from `list-spans` rather than guessing.

**Project it.** A full response embeds every scraped page and one observed run carried 176k tokens in a single turn. `-j 'finish_reason'` is a few bytes; an unprojected `--json` is not. **Never call this without `-j`.**

## 4. Where the config knobs live

`orq traces list-fields` returns 57 fields (re-read it; do not trust that number).

**Top-level, filterable:** `status`, `model`, `base_model`, `provider`, `agent_id`, `agent_name`, `deployment_environment`, `project_id`, `session_id`, `operation`, `name`, `product`, `tool_name`, `duration_ms`, `cost.total`, `tokens.total`, `agent.iterations.count`, `eval_passed`, `eval_score`, `eval_label`, `evaluator_key`.

**Attribute-scoped** (facetable, filterable, groupable): `attributes.gen_ai.response.finish_reasons`, `attributes.gen_ai.usage.input_tokens`, `attributes.gen_ai.tool.type`, `attributes.orq.span_type`, `attributes.orq.model.id`, `attributes.orq.billing.*`, `attributes.http.response.status_code`, `attributes.type`.

**Not in the registry at all — per-span only:** `temperature`, `top_p`, `max_tokens`, and the entire `openresponses.*` block.

> **Queryable is not groupable is not computable.** The three capabilities are independent, and `list-fields` does not distinguish them — a field listed there may still be rejected by `group_by` or `compute`. Verified:
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
| `span.attributes.gen_ai.tool.definitions` | full tool array → tool-inventory count |
| `span.attributes.gen_ai.usage` | `prompt_tokens_details.cached_tokens`, `reasoning.output_tokens` |
| `span.attributes.openresponses` | `truncation`, `reasoning` (`{"effort":"medium",…}`), `tool_choice`, `tools`, `parallel_tool_calls`, `max_output_tokens`, `frequency_penalty`, `presence_penalty`, `service_tier`, `status` |

> **Parsing gotcha.** Inside `openresponses`, `reasoning` / `text` / `tool_choice` / `tools` are **JSON-encoded strings**, and `max_output_tokens` is a **string** (`"8000"`) while `gen_ai.request.max_tokens` is an **int** (`8000`). **Prefer the `gen_ai.*` normalised view**; fall back to `openresponses.*` only for the knobs it alone carries (`truncation`, `reasoning.effort`, `tool_choice`, `parallel_tool_calls`).

### Coverage limit — these knobs are not everywhere

They exist on **orq-hosted invocation spans**. They are **absent** on:

- **OTLP-ingested** third-party spans — `gen_ai` carries only model/provider/usage; no `request.temperature`, no `openresponses` (verified on an `orq.claude_code.session` trace).
- **AI-Router passthrough** spans — same gap; the projection above returned `temp:null, max:null, finish:null` (verified on a `chat.cerebras` trace).
- **orq Agent invocation** spans — `gen_ai.request` carries only `{temperature, max_tokens, model, stream}`. **No `top_p`, no `finish_reasons`, and no `openresponses` block at all**, so `truncation` and `reasoning_effort` are unreadable from spans. `span.agent_tool_execution` is never emitted, so tool activity is invisible too. Verified across 55 traces on each of two agents. **Everything in this bullet is available from `agents get-response` (§3.5) instead — treat it as the fallback before recording any of these as `unobservable`.**

**Anything unreadable is `unobservable` with a reason. Absence never reads as a pass.**

---

## 5. `orq reporting query` — the supplement

A catalogue API over analytics rollups. It carries three signals the trace registry does not, and it **cannot replace the sweep**.

- **18 metrics:** `genai.requests` · `genai.tokens` · `genai.cost` · `genai.errors` · `genai.error_rate` · `genai.latency.p50|p95|p99` · **`genai.ttft.avg|p50|p95`** · **`genai.evaluator.runs|pass_rate|score.avg`** · `genai.guardrail.runs|block_rate|triggered` · `genai.usage`. Enumerate at runtime by passing an invalid `--metric` and reading the 400.
- **`group_by` for `genai.requests`:** `model`, `provider`, `agent`, `deployment`, `evaluator`, `project`, `tool`. **Rejected:** `status`, `finish_reason`, `agent_name`, `entity`, `error_type` — which is exactly why it cannot do the terminal-state sweep. Dimensions are per-metric; probe rather than assume.
- **Use `mode: "scalar"`.** It returns one flat row per group — `{dimensions:{…}, metrics:{…}, timestamp:null}` — instead of the time buckets `query_analytics` forces. Add `include_totals` for a window-wide total.
- **Target scoping:** `filters: [{"field":"agent","op":"eq","values":["<key>"]}]`. `values` is a **list and must be non-empty**; a scalar `value` is a 400.
- `from` / `to` are both required; the 30-day retention bound applies as in §2.

**TTFT lives here, and an all-zero result is not "fast".** A live `genai.ttft.p50` scalar query returned 20 model groups with **every value `0`** and a window total of `0` — consistent with the metric being populated only for streaming requests. Treat an all-zero TTFT result as **`unobservable: [ttft]`**, never as a measurement.

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

orq traces list-fields --json                    # resolve names first
orq traces list-facets --json

orq traces aggregate  --from-file body.json --json
orq traces search     --from-file body.json  --json -j 'data[].trace_id'   # same file, overwritten
orq traces list-spans <trace> --json -j "data[].{id:span_id,name:name,type:type,start:started_at,status:status}"
orq traces get-span   <trace> <span> --json -j '{temp:span.attributes.gen_ai.request.temperature}'

orq reporting query   --from-file body.json --json       # same file, overwritten
orq agents retrieve   <key> --json
orq agents update     <key> --from-file patch.json --version-increment patch --version-description "..."
# clean up: delete body.json and patch.json when done
```
