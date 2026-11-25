# The six checks

Every command here was run on 2026-10-12 (`orq` CLI 11.0.2) unless marked `[unverified: …]`. Bodies go in `<run-dir>/body.json` (no BOM), overwritten per query, per [`trace-queries.md`](../../orq-shared/resources/trace-queries.md) §2. Compute the window at call time with the bash, BSD or PowerShell form in `trace-queries.md` §8, using **29 days**: that keeps the window inside 30-day retention at any time of day.

---

## 1. Run health

Reads the list in [`config-context.md`](../../orq-shared/resources/config-context.md). Resolve field names with `orq traces list-fields -o json` first; the target field is `agent_name` (the agent key) or `agent_id`.

**Population, status and model served, one sweep:**

```json
{"from":"<FROM>","to":"<TO>",
 "group_by":["model","status"],
 "filters":[{"field":"agent_name","op":"eq","values":["<key>"]}],
 "compute":[{"metric":"trace_id","op":"count"},{"metric":"cost.total","op":"sum"},{"metric":"duration_ms","op":"avg"}]}
```

`orq traces aggregate --from-file body.json -o json -j 'data[].[group,metrics]'` returns one `[group, metrics]` pair per model and status, e.g. `[{"model":"openai/gpt-5.6-luna","status":"ok"},{"cost.total.sum":0.0045588,"duration_ms.avg":5381.67,"trace_id.count":3}]`.

**Latency percentiles:** same filter, no `group_by`, `"compute":[{"metric":"duration_ms","op":"p50"},{"metric":"duration_ms","op":"p95"}]`. Use this, not `orq reporting query` (Gotcha 7).

**Terminal states.** Try the aggregate first with `group_by: ["attributes.gen_ai.response.finish_reasons"]` if `list-fields` lists it. When it is missing, or only the null bucket comes back (Gotcha 4), read every run in up to 20 recent traces.

**One trace can hold several runs** (gotcha 12): a conversation of 5 turns was one trace id with 5 `span.agent` spans. Read each `span.agent` and the last `span.responses` (model call) after it: `list-spans` returns spans in run order with no parent link, so the last call before the next `span.agent` belongs to that run. Stop at the first `span.evaluator` after the agent span: an attached LLM judge's own `span.responses` follows it (gotcha 11), and reading that one reports the judge's error, truncation and tokens as the agent's [unverified: no trace with an LLM judge and a per-run read was probed]. The agent span's `error.type` was only `*errors.errorString`; the cause is on that last call.

These snippets are bash (`tr`, `awk`, `while read`); on Windows run them in Git Bash.

```bash
# ids, newest first; deduplicate (Gotcha 8)
# body: {"from":..,"to":..,"filters":[agent filter],"sort":[{"field":"end_time","order":"desc"}],"limit":40}
orq traces search --from-file body.json -o json -j 'data[].trace_id'

# per trace: each run's agent span and its last model call before any evaluator, then their terminal facts
orq traces list-spans <trace_id> -o json \
  -j "data[?type=='span.agent' || type=='span.responses' || type=='span.evaluator'].join(' ', [type,span_id])" \
  | tr -d '",[]' \
  | awk 'NF==2 { if ($1=="span.agent") { if (a) print a, l; a=$2; l=""; e=0 } else if ($1=="span.evaluator") e=1; else if (!e) l=$2 } END { if (a) print a, l }' \
  | while read A L; do
      orq traces get-span <trace_id> "$A" -o json \
        -j "{finish: span.attributes.gen_ai.response.finish_reasons, iterations: span.attributes.agent.iterations.count, version: span.attributes.agent.version, dur: span.summary.duration_ms, input: span.attributes.gen_ai.input[?role=='user'] | [-1].content[0].text}"
      [ -n "$L" ] || { echo '{"note":"no model call in this run"}'; continue; }
      orq traces get-span <trace_id> "$L" -o json \
        -j '{error: span.attributes.error.type, status: span.attributes.openresponses.status, incomplete: span.attributes.openresponses.incomplete_details, out: span.attributes.gen_ai.usage.output_tokens, max: span.attributes.gen_ai.request.max_tokens}'
    done
```

Returns one pair per run, e.g. `{"dur":34975,"finish":["stop"],"input":"catfood","iterations":5,"version":"1.0.2"}` then `{"error":null,"incomplete":null,"max":4000,"out":3026,"status":"completed"}`. A run that hit a provider error read `{"finish":null,...}` then `{"error":"server_error","status":"in_progress","out":null,...}`; one that hit the output cap read `finish: ["length"]` then `{"status":"incomplete","incomplete":"{\"reason\":\"max_output_tokens\"}","out":4000,...}`. Use the last call's `out`, not the agent span's `gen_ai.usage.output_tokens`: that one sums every call in the run (7,234 on a 4,000 cap). `input` is the run's last user message; when the sample is greetings or other non-task inputs, say so next to the mode (SKILL.md Phase 2). Delegate the per-trace reads to one subagent when the Task tool is available.

| Finding | Evidence | Tier |
|---|---|---|
| Sampled runs whose `finish` is `length`, `max_iterations` or `max_time` | N of the sample, trace ids | Broken |
| Sampled runs with `finish: null` and an `error` on the last call (e.g. `server_error`) | N of the sample, the error types, trace ids | Trace-backed |
| Runs that stopped normally with the last call's `out` at 75% or more of `max` (3,026 of 4,000) | N of the sample, the highest `out` | Trace-backed |
| `iterations` equals `settings.max_iterations`, but `finish` does not name the cap | N of the sample, the setting | Trace-backed |
| `dur` within 5% of `settings.max_execution_time` × 1000 | N of the sample, the setting | Trace-backed |
| Trace `status: error` | count from the sweep, 3 trace ids | Trace-backed |
| Model served ≠ declared (a fallback serving traffic) | the `model` groups vs `model.id` | Trace-backed |

**Served vs declared:** resolve both through `models.json` and compare the benchmarked model (`metadata.autorouter.v2.aa_slug`), not the provider prefix. Not `metadata.autorouter.id`: it names a family (`openai/gpt-5` and `openai/gpt-5.4` share one). `azure/eu.gpt-5.6-luna` serving an agent declared on `openai/gpt-5.6-luna` is the same model on another provider, not a fallback. A group matching an attached LLM judge's `model` (Phase 1, resolved through `models.json`) is the judge, not a fallback (gotcha 11): `openai/gpt-4.1` appeared this way next to the agent's own model. Only a group that matches neither the agent's models nor a judge's is a fallback finding; confirm it with `orq traces list-spans <trace_id> --raw -j 'data[].{type:type,name:name,model:model}'` on one of its traces (a judge call sits after a `span.evaluator` span, named `chat <judge model>`). Sampled `version` different from the config's `version` means older runs are in the window; say so next to every trace-backed count.

Always record in the report, as facts not findings: trace count, status split, cost sum, duration p50/p95, and the sample size behind the terminal-state counts.

Truncation is the last call's `incomplete` above. Every other item on the `config-context.md` list that this check does not read still goes in the report, so its absence never reads as fine.

`unobservable` here: terminal states when no `span.agent` span exists; cost or latency when the aggregate errors; TTFT, `billing.threshold_exceeded` and prompt-cache hit ratio (not read).

## 2. Instructions

Read `instructions` against the 11 guidelines in [`prompting-guidelines.md`](../../orq-shared/resources/prompting-guidelines.md). For each guideline record `present`, `weak` or `missing`, with the quoted line (or "no line" for missing). Guideline 5 (output format) counts as `present` when the agent has tools, per the guideline itself. A `weak` or `missing` guideline is a finding, tier **best practice only** unless a run-health finding is caused by it, fixed by `orq-improve-agent`.

Three mechanical comparisons, tier **config** unless noted:

- **Tools named vs attached.** Every `settings.tools[].display_name` (and `action_type`) the instructions never mention: the model has to guess when to use it; fixed by `orq-improve-agent` (how instructions reference tools). Every tool or capability the instructions require that no attached tool provides (e.g. "use the Web Scraper" with only `google_search` attached): tier **broken**, and the suggestion says which tool to add, an existing built-in if one fits (`web_scraper`, `google_search`) or a new tool described by what it must do. No skill adds tools to an existing agent yet, so the fixing skill is "no owning skill: a coding agent or the orq UI".
- **Variables.** Placeholders `{{name}}` in `instructions`, excluding `{{skill.*}}` and `{{snippet.*}}`, against the keys of `variables`. A placeholder not declared, or a declared variable never used, is a finding.
- **`system_prompt` non-null.** Report it as a fact: the wrapper's relationship to `instructions` is undocumented, and `orq-improve-agent` asks before rewriting around it.

A contradiction between the instructions and the config is a finding in its own right. Label it **predicted broken** only when the arithmetic makes it certain to break every run, not just likely: more mandatory sequential steps than `max_iterations` allows (three one-at-a-time steps under `max_iterations: 2`), or a required minimum length above `max_tokens` (an 800-word minimum under `max_tokens: 800`). Then:

- **No traces:** tier **config**, label `predicted broken`, sorted first in the config tier, so it leads the report unless something is broken outright. The suggestion says it will break every run once traffic arrives.
- **Traces, and run health confirms it** (sampled runs end on the cap): tier **broken**, no label; the traces are the evidence now.
- **Traces, and run health does not confirm it:** tier **config**, no label. The prediction was wrong, or the model works around it; say which the sample suggests.

## 3. Tools

For each `settings.tools[]` entry, read `action_type`:

| `action_type` | What it is | What to check |
|---|---|---|
| `google_search`, `web_scraper` and other built-ins | platform tool, description not editable | numbers only. `orq tools retrieve <id>` returns `404 Tool collection not found.` |
| `mcp` | one tool of an MCP server (`display_name` = tool name, `tool_id`) | the description is not readable with a project key: `orq tools retrieve` on `id` and on `tool_id` both returned 404. Record `unobservable` |
| function, http, code | a tool entity in the project | `orq tools retrieve <id> -o json` and check its description and parameter schema against the 5 rules in [`tool-description-guide.md`](../../orq-shared/resources/tool-description-guide.md) [unverified: no custom tools in the probed projects] |

Each broken rule is a finding: tier **best practice only**, with a suggestion naming the missing part (when to use it, what it does not do, a parameter format, the return value, an error case). No skill edits an existing agent's tool descriptions yet, so the fixing skill is "no owning skill: a coding agent or the orq UI". Two tools with overlapping trigger conditions is one finding naming both.

**Calls and errors per tool:** try `group_by: ["tool_name"]` with the agent filter (Gotcha 6). It returned real `google_search` and `web_scraper` groups on an agent with built-in tools, and only the null bucket on an MCP agent; then record `unobservable: per-tool calls not emitted on agent traces`. The group counts traces, not calls. Count calls from the sampled traces: `span.tool` spans named after an attached tool's `display_name` are calls; `MCPConnect` and `MCPDiscoverTools` are not.

**Tool errors are undercounted.** A `web_scraper` call whose page returned 403 logged its span as `ok`, and its result read `{"code":200,"data":{"title":"Access Denied",...}}`. Neither span status nor the tool's own code shows the failure, so a zero error count is not "no tool failures". Report it as `unobservable: tool failures inside an ok result` unless you read the results of the sampled calls.

## 4. Skills

Platform Skills attach to an agent only through placeholders in `instructions`: `{{skill.<display_name>}}`, or the older alias `{{snippet.<display_name>}}`, matched on the Skill's `display_name`, case-sensitive ([`orq-manage-skills`](../../orq-manage-skills/SKILL.md)). The agent's own `skills` field is not that link; ignore it.

1. Extract references with the pattern `\{\{\s*(skill|snippet)\.([A-Za-z0-9_]+)\s*\}\}`.
2. Resolve each name against `display_name` across the whole skill list (names are unique per workspace).
3. **Unresolved reference** → tier **broken**: it renders as empty text. Evidence: the quoted placeholder and "no Skill with this display_name". Suggestion: create the Skill or fix the name.
4. **Skills in the agent's `project_id` that no placeholder references** → `candidate`, tier **config**. List them; do not judge whether they fit.

`orq skills list` returned `{"data":[],"has_more":false}` in both probed projects, so steps 2 to 4 have not run against a live Skill [unverified: no Skills in the probed workspaces]. An empty list with references present is still step 3: every reference is unresolved.

## 5. Evaluators

1. List `settings.evaluators[]` with each `execute_on`, `sample_rate`, and the `orq evals get` name and type.
2. Runs and pass rate over the window, one call each:

   ```json
   {"metric":"genai.evaluator.runs","mode":"scalar","group_by":["evaluator"],
    "filters":[{"field":"evaluator","op":"in","values":["<id1>","<id2>"]}]}
   ```

   `orq reporting query --from-file body.json --from 29d --to now -o json -j 'data[]'` returns one row per id, e.g. `{"dimensions":{"evaluator":"01M3KM2H7X1FVP5A4NM46Z3277"},"metrics":{"genai.evaluator.runs":4}}`. Repeat with `genai.evaluator.pass_rate`. Keep the window on the flags, not in the body.

   **That pass rate counts the outcome, not the verdict** (gotcha 10). On the same traces you sampled in §1, read each evaluator span:

   ```bash
   for S in $(orq traces list-spans <trace_id> --raw -j "data[?type=='span.evaluator'].span_id"); do
     orq traces get-span <trace_id> "$S" -o json \
       -j '{name: span.attributes.gen_ai.evaluation.name, passed: span.attributes.gen_ai.evaluation.passed, verdict: span.attributes.gen_ai.evaluation.score}'
   done
   ```

   Returns e.g. `{"name":"briefing-complete-six-sections","passed":true,"verdict":{"label":"fail","value":0}}`. Report the verdict pass rate (`verdict.label` is `pass`) over the sample [unverified: only boolean evaluators probed; other output types may label differently] next to the reporting one. When they differ, the reporting number is wrong for this evaluator: say so, and use the verdict rate. Both stay `unvalidated`.
3. **Project evaluators not attached:** from the Phase 2 list, every evaluator in the agent's project that is not in `settings.evaluators[]`. Each one is a `candidate` with its key and `description`, tier **config**, with the suggestion "consider attaching `<key>`: it checks <description>". You have read only its description, so do not claim it fits; `orq-recommend-evaluators` reads what it actually checks before attaching it.

Findings, tier **config** unless noted:

- No evaluators attached.
- An evaluator at `sample_rate: 0`.
- An evaluator with 0 runs while the agent had traffic in the window: tier **trace-backed**.
- Every pass rate in the report carries `unvalidated` and a pointer to `orq-evaluator-alignment`. A low pass rate is a fact, not a finding: the judge may be wrong.

Every evaluator gap is fixed by `orq-recommend-evaluators`. Do not design new evaluators; that is its job.

## 6. Model

Decide first whether this is a **coding agent** (its instructions or tools are about writing, editing or running code). Coding agents need long context, so pass `--coding` to keep the full current window as the floor; say which you chose and why in the report. Then run the script on the two files from Phases 1 and 2:

```bash
uv run --no-project <skill-dir>/scripts/model_check.py <run-dir>/models.json <run-dir>/agent.json [--coding] > <run-dir>/model-check.json
```

It checks the primary model and each entry of `fallback_models` [unverified: no agent with fallbacks was available to read their shape]. The rule lives in the script and is printed once, as the top-level `rule` field of `model-check.json`; quote that field in the report rather than restating it. A capability the current model does not report is required of every candidate; the script lists those in `unreported_flags`, so state them next to the result.

Read `model-check.json`:

| Result | Report as |
|---|---|
| `intelligence.status: dominated` | finding, tier **config**, with up to 5 `candidates` (refId, index, `current_at_same_effort`, price, `price_gap`, the effort both were scored at, and any `aliases` of the same model), each labelled `candidate`, with `candidate_count` printed beside them (5 shown of N dominating); fixed by `orq-improve-agent`. The suggestion names the cheapest strong candidate and says to run `orq-run-experiment` first if the agent has a dataset and evaluators: the index is a general benchmark and can be wrong for this agent |
| `tau2.status: dominated` | the same, on the agentic benchmark, as a separate finding |
| `not dominated` | "no finding: not dominated among `compared` comparable models (`skipped_no_data` skipped for a missing price or score)", with the current index and price |
| `intelligence.status: unknown` with `ineligible` | `unobservable` with the script's `reason`; if every row was rejected by the capability guards, say so, since that is a constraint of this agent, not missing catalogue data |
| `cheaper_provider` with `status: unknown` | `unobservable` with the script's `reason` (the current model has no price, so no provider was compared) |
| `cheaper_provider` with `status: none` | "no finding: no cheaper provider among the priced rows", plus `skipped_no_data` same-model rows not compared for a missing price when above 0 |
| `cheaper_provider` set to a model row (per model, next to `intelligence`) | finding, tier **config**, once per model: the same model is `price_gap` cheaper on that refId; fixed by `orq-improve-agent`. The suggestion says to check the provider's region first (an `eu.` prefix can be a data residency choice) |
| `unknown`, reason `not in the catalogue`, role `primary` | finding, tier **broken**: "the declared model is not available in this project, and the agent cannot run"; fixed by `orq-improve-agent`. The `closest` names are possible replacements, listed in the suggestion; they are not checked |
| `unknown`, reason `not in the catalogue`, role `fallback` | finding, tier **config**: "this fallback is not available in this project, so it cannot take over when the primary fails"; the same fixing skill and `closest` list |
| `unknown`, reason `catalogue empty`, or `catalogue_size` 0 | `unobservable: model catalogue empty`, never a broken finding: re-read `models.json` before trusting any model result |
| `unknown`, any other reason (`ambiguous: …`, `no intelligence_index or price`, `no comparable model: …`, …) | `unobservable` with the script's `reason` |
| `resolved: date-suffix match on …` or `exact on model_id` | state the matched `refId` next to the result |
