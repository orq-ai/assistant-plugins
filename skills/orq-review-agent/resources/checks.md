# The six checks

Every command here was run on 2026-10-12 (`orq` CLI 11.0.2) unless marked `[unverified: …]`. Compute the window at call time; bodies go in `body.json` (no BOM), overwritten per query, per [`trace-queries.md`](../../orq-shared/resources/trace-queries.md) §2.

```bash
FROM=$(date -u -d '-29 days' +%Y-%m-%dT%H:%M:%SZ)   # BSD/macOS: date -u -v-29d ...
TO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
```

29 days keeps the window inside 30-day retention at any time of day.

---

## 1. Run health

Reads the list in [`config-context.md`](../../orq-shared/resources/config-context.md). Resolve field names with `orq traces list-fields -o json` first; the target field is `agent_name` (the agent key) or `agent_id`.

**Population, status and model served — one sweep each:**

```json
{"from":"<FROM>","to":"<TO>",
 "group_by":["model","status"],
 "filters":[{"field":"agent_name","op":"eq","values":["<key>"]}],
 "compute":[{"metric":"trace_id","op":"count"},{"metric":"cost.total","op":"sum"},{"metric":"duration_ms","op":"avg"}]}
```

`orq traces aggregate --from-file body.json -o json -j 'data[].[group,metrics]'` returned `[[{"model":"openai/gpt-5.6-luna","status":"ok"},{"cost.total.sum":0.0045588,"duration_ms.avg":5381.67,"trace_id.count":3}]]`.

**Latency percentiles:** same filter, no `group_by`, `"compute":[{"metric":"duration_ms","op":"p50"},{"metric":"duration_ms","op":"p95"}]`. Use this, not `orq reporting query` (Gotcha 7).

**Terminal states.** Try the aggregate first with `group_by: ["attributes.gen_ai.response.finish_reasons"]` if `list-fields` lists it. When it is missing, or only the null bucket comes back (Gotcha 4), read the `span.agent` span of up to 20 recent traces:

```bash
# ids, newest first; deduplicate (Gotcha 8)
# body: {"from":..,"to":..,"filters":[agent filter],"sort":[{"field":"end_time","order":"desc"}],"limit":40}
orq traces search --from-file body.json -o json -j 'data[].trace_id'

# per trace: the agent span, then its terminal facts
A=$(orq traces list-spans <trace_id> --raw -j "data[?type=='span.agent'].span_id | [0]")
orq traces get-span <trace_id> "$A" -o json \
  -j '{finish: span.attributes.gen_ai.response.finish_reasons, iterations: span.attributes.agent.iterations.count, version: span.attributes.agent.version, dur: span.summary.duration_ms}'
```

Returned `{"finish":["stop"],"iterations":1,"version":"1.0.3",...}`. Delegate the per-trace reads to one subagent when the Task tool is available.

| Finding | Evidence | Tier |
|---|---|---|
| Runs end on `length`, `max_iterations` or `max_time` | count of N sampled, trace ids | Broken |
| `iterations` equals `settings.max_iterations` on most sampled runs | count, the setting | Broken |
| `dur` within 5% of `settings.max_execution_time` × 1000 | count, the setting | Broken |
| Trace `status: error` | count from the sweep, 3 trace ids | Trace-backed |
| Model served ≠ declared (a fallback serving traffic) | the `model` groups vs `model.id` | Trace-backed |

Compare served and declared models by the part after the provider (`openai/gpt-5.6-luna` served vs a declared UUID resolved through `models.json`). Sampled `version` different from the config's `version` means older runs are in the window; say so next to every trace-backed count.

Always record in the report, as facts not findings: trace count, status split, cost sum, duration p50/p95, and the sample size behind the terminal-state counts.

`unobservable` here: terminal states when no `span.agent` span exists; cost or latency when the aggregate errors; TTFT (not read).

## 2. Instructions

Read `instructions` against the 11 guidelines in [`prompting-guidelines.md`](../../orq-shared/resources/prompting-guidelines.md). For each guideline record `present`, `weak` or `missing`, with the quoted line (or "no line" for missing). Guideline 5 (output format) counts as `present` when the agent has tools, per the guideline itself. A `weak` or `missing` guideline is a finding, tier **best practice only** unless a run-health finding is caused by it.

Three mechanical comparisons, tier **config** unless noted:

- **Tools named vs attached.** Every `settings.tools[].display_name` (and `action_type`) the instructions never mention: the model has to guess when to use it. Every tool or capability the instructions require that no attached tool provides (e.g. "use the Web Scraper" with only `google_search` attached): tier **broken**.
- **Variables.** Placeholders `{{name}}` in `instructions`, excluding `{{skill.*}}` and `{{snippet.*}}`, against the keys of `variables`. A placeholder not declared, or a declared variable never used, is a finding.
- **`system_prompt` non-null.** Report it as a fact: the wrapper's relationship to `instructions` is undocumented, and `orq-improve-agent` asks before rewriting around it.

A contradiction between the instructions and the config is a finding in its own right. Label it **predicted broken** only when the arithmetic makes it certain to break every run, not just likely: more mandatory sequential steps than `max_iterations` allows (three one-at-a-time steps under `max_iterations: 2`), or a required minimum length above `max_tokens` (an 800-word minimum under `max_tokens: 800`). Then:

- **No traces:** tier **config**, label `predicted broken`, sorted first in the config tier, so it leads the report unless something is broken outright. The suggestion says it will break every run once traffic arrives.
- **Traces, and run health confirms it** (runs end on the cap): tier **broken**, no label; the traces are the evidence now.
- **Traces, and run health does not confirm it:** tier **config**, no label. The prediction was wrong, or the model works around it; say which the sample suggests.

## 3. Tools

For each `settings.tools[]` entry, read `action_type`:

| `action_type` | What it is | What to check |
|---|---|---|
| `google_search`, `web_scraper` and other built-ins | platform tool, description not editable | numbers only. `orq tools retrieve <id>` returns `404 Tool collection not found.` |
| `mcp` | one tool of an MCP server (`display_name` = tool name, `tool_id`) | the description is not readable with a project key: `orq tools retrieve` on `id` and on `tool_id` both returned 404. Record `unobservable` unless `mcp__orq-workspace__get_agent` returns it [unverified: MCP not connected when probed] |
| function, http, code | a tool entity in the project | `orq tools retrieve <id> -o json` and check its description and parameter schema against the 5 rules in [`tool-description-guide.md`](../../orq-shared/resources/tool-description-guide.md) [unverified: no custom tools in the probed projects] |

Each broken rule is a finding: tier **best practice only**, fixed by `orq-build-agent`. Two tools with overlapping trigger conditions is one finding naming both.

**Calls and errors per tool:** try `group_by: ["tool_name"]` with the agent filter. When only the null bucket returns (Gotcha 6), record `unobservable: per-tool calls not emitted on agent traces`. Do not read `span.tool` spans as calls: the ones observed were `MCPConnect` and `MCPDiscoverTools`.

## 4. Skills

Platform Skills attach to an agent only through placeholders in `instructions`: `{{skill.<display_name>}}`, or the older alias `{{snippet.<display_name>}}`, matched on the Skill's `display_name`, case-sensitive ([`orq-manage-skills`](../../orq-manage-skills/SKILL.md)). The agent's own `skills` field is not that link; ignore it.

1. Extract references with the pattern `\{\{\s*(skill|snippet)\.([A-Za-z0-9_]+)\s*\}\}`.
2. Resolve each name against `display_name` across the whole skill list (names are unique per workspace).
3. **Unresolved reference** → tier **broken**: it renders as empty text. Evidence: the quoted placeholder and "no Skill with this display_name".
4. **Skills in the agent's `project_id` that no placeholder references** → `candidate`, tier **config**. List them; do not judge whether they fit.

`orq skills list` returned `{"data":[],"has_more":false}` in both probed projects, so steps 2–4 have not run against a live Skill [unverified: no Skills in the probed workspaces]. An empty list with references present is still step 3: every reference is unresolved.

## 5. Evaluators

1. List `settings.evaluators[]` with each `execute_on`, `sample_rate`, and the `orq evals get` name and type.
2. Runs and pass rate over the window, one call each:

   ```json
   {"metric":"genai.evaluator.runs","mode":"scalar","group_by":["evaluator"],
    "filters":[{"field":"evaluator","op":"in","values":["<id1>","<id2>"]}]}
   ```

   `orq reporting query --from-file body.json --from 29d --to now -o json -j 'data[]'` returned one row per id, e.g. `{"dimensions":{"evaluator":"01M3KM2H7X1FVP5A4NM46Z3277"},"metrics":{"genai.evaluator.runs":4}}`. Repeat with `genai.evaluator.pass_rate`. Keep the window on the flags, not in the body.

Findings, tier **config** unless noted:

- No evaluators attached.
- An evaluator at `sample_rate: 0`.
- An evaluator with 0 runs while the agent had traffic in the window: tier **trace-backed**.
- Every pass rate in the report carries `unvalidated` and a pointer to `orq-evaluator-alignment`. A low pass rate is a fact, not a finding: the judge may be wrong.

Every evaluator gap is fixed by `orq-recommend-evaluators`. Do not suggest which evaluators to add; that is its job.

## 6. Model

Decide first whether this is a **coding agent** (its instructions or tools are about writing, editing or running code). Coding agents need long context, so pass `--coding` to keep the full current window as the floor; say which you chose and why in the report. Then run the script on the two files from Phases 1 and 2:

```bash
uv run --no-project <skill-dir>/scripts/model_check.py models.json agent.json [--coding] > model-check.json
```

It checks the primary model and each entry of `fallback_models` [unverified: no agent with fallbacks was available to read their shape]. The rule, stated once here and in the script:

> **Dominated** (RES-1338 A1, guardrails agreed with Bauke on RES-887, 2026-10-12): another enabled chat model has an intelligence index at least as high, at a blended price at least 20% lower, and the same capabilities: text in and out, plus image input or output where the current model has it (rarer modalities such as audio are out of scope); tool calling where the current model has it; structured outputs if `response_format` is `json_schema`; a context window of at least min(current, 300k), or the full current window for a coding agent. Both sides are scored at the agent's `reasoning_effort` (per fallback: that entry's own `parameters`) when the catalogue lists an index for that effort, and at their default effort otherwise; each candidate row carries the effort it was scored at. For agents with tools, the same comparison runs on `tau2`.

Read `model-check.json`:

| Result | Report as |
|---|---|
| `intelligence.status: dominated` | finding, tier **config**, with up to 5 `candidates` (refId, index, price, `price_gap`), each labelled `candidate`; fixed by `orq-run-experiment`. Say that A1 can be wrong in practice (the index is a general benchmark), which is why the hand-off is an experiment and not a switch |
| `tau2.status: dominated` | the same, on the agentic benchmark, as a separate finding |
| `not dominated` | "no finding" with the current index and price |
| `unknown` | `unobservable` with the script's reason (`not in the catalogue`, `ambiguous: …`, `ambiguous date suffix: …`, `no intelligence_index or price`). When the result has `closest`, list those names in the Model section as likely matches (they are not checked); for `deepseek/deepseek-v4-flash` it returned five `…deepseek-v4-flash-0731` rows at similarity 1.0 |
| `resolved: date-suffix match on …` or `exact on model_id` | state the matched `refId` next to the result |

Live results on 2026-10-12: `anthropic/claude-haiku-4-5` resolved by date suffix to `anthropic/claude-haiku-4-5-20251001` (index 23.7, price 2) and was dominated by 19 models; `openai/gpt-5.6-luna` (index 38.1 at effort `medium`, price 0.45) was not dominated; `cerebras/qwen-3.8-27b` had no index or price; `deepseek/deepseek-v4-flash` was not in the catalogue.
