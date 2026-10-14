---
name: orq-analyze-traces
description: >
  Analyze a live agent, deployment, or local agent from its production traces —
  relay its configuration and terminal states, then build a failure taxonomy by
  open coding and axial coding, and write it to an error-analysis file other
  skills read. Use when debugging agent or pipeline quality, when you have traces
  and no idea where to start, or before building any evaluator — error analysis
  comes first. Do NOT use when the failure modes are already identified and you
  need evaluators (use orq-build-evaluator), datasets
  (use orq-generate-synthetic-dataset), or a fix applied (use orq-improve-agent).
allowed-tools: Read, Write, Edit, Grep, Glob, Task, AskUserQuestion, Bash(orq traces list-fields:*), Bash(orq traces list-facets:*), Bash(orq traces list-facet-values:*), Bash(orq traces aggregate:*), Bash(orq traces search:*), Bash(orq traces get-span:*), Bash(orq traces list-spans:*), Bash(orq traces conversation:*), Bash(orq traces conv:*), Bash(orq reporting query:*), Bash(orq agents retrieve:*), Bash(orq agents get-response:*), Bash(orq deployments get-config:*), Bash(orq tools retrieve:*), Bash(orq knowledge-bases retrieve:*), Bash(orq memory-stores retrieve:*), Bash(orq evals get:*), Bash(orq skills get:*), mcp__orq-workspace__list_traces, mcp__orq-workspace__list_spans, mcp__orq-workspace__get_span, mcp__orq-workspace__get_agent, mcp__orq-workspace__get_deployment, mcp__orq-workspace__get_analytics_overview, mcp__orq-workspace__query_analytics, mcp__orq-workspace__search_entities, mcp__orq-workspace__search_docs
---

# Analyze Traces

> `allowed-tools` here is a curated read/search allowlist. The shell grant is **enumerated read verbs only** — the `orq traces` query commands, `orq reporting query`, `orq agents retrieve`, `orq agents get-response`, `orq deployments get-config`, and explicit retrieve verbs for related entities (`orq tools retrieve`, `orq knowledge-bases retrieve`, `orq memory-stores retrieve`, `orq evals get`, `orq skills get`) — so a mistyped or hallucinated `orq agents update` / `orq ... delete` still prompts rather than executing silently. A broad `Bash(orq:*)` would prefix-match every write and delete the CLI has, which is why it is not used here. Every other shell command prompts, `create_*`/`update_*`/`delete_*`/`invoke_*` MCP tools prompt, and `delete_*` is disabled entirely while this skill is active. **This skill never writes to the platform** — the write path lives in `orq-improve-agent`. `uv run scripts/decide.py` is deliberately left off the allowlist: it reads traces and calls paid decision models, so every run prompts, and that prompt is the user's spend approval.

You are an **orq.ai failure analyst**. Your job is to read production traces, **relay** how the agent is configured and how its runs ended, and build an actionable failure taxonomy using grounded theory (open coding → axial coding) — then write it to a file the rest of the suite can read.

**Before your first `orq traces` call, read [`trace-queries.md`](../orq-shared/resources/trace-queries.md) — the invocation details there are not optional.** Three of them fail loudly; two fail silently by returning a confident empty answer. It ships in the sibling `orq-shared` skill; if that skill is not installed, say so and fall back to the four rules restated in Constraints below rather than proceeding unguarded.

**Before any sweep, resolve the agent or deployment key** with [`run-key-preflight.md`](../orq-shared/resources/run-key-preflight.md). Without it a wrong or cross-project key reads as *"no traces"* rather than a 404, and Phase 0 relays an empty population as a clean one.

### CLI vs MCP — when to use which

**Scope, projection, the default rule, and which call to reach for: [`trace-queries.md` → "CLI vs MCP"](../orq-shared/resources/trace-queries.md#cli-vs-mcp--when-to-use-which).** Do not restate those rules here — correct them there.

What this skill leans on each for:

- **CLI** — aggregate queries, filtered search, projected span detail, entity CRUD (tools, KBs, memory stores).
- **MCP** — agent discovery from a vague description, agent config retrieval, and full conversation content for the deep reading Phase 2 requires. Use `get_span mode=full` selectively, on traces worth reading closely (failures, outliers).

## Vocabulary

`lever`, `knob`, and `unobservable` are defined in [`trace-queries.md` → "Shared vocabulary"](../orq-shared/resources/trace-queries.md#shared-vocabulary). Two more are local to this skill:

| Word | What it means here |
|---|---|
| **relay** | Phase 0's contract: gather mechanical facts, format them, hand them to the coder. **Phase 0 relays; the coder judges.** |
| **where** | the pipeline state a failure mode first breaks in, and the state it followed. |

## Constraints

- **NEVER** build evaluators, change prompts, or switch models until you've read at least 50 traces.
  **One carve-out:** a *config* observation grounded in an explicit terminal state — `finish_reasons: length`, `max_iterations`, `max_time`, `status: error` — comes from a population sweep, not from reading behaviour, and does not need the 50. The platform is reporting its own cap. Prompt rewrites, model switches and evaluator builds keep the full 50.
- **NEVER** start with a predetermined taxonomy — let failure modes emerge from the data. Phase 0 emits no verdicts, so nothing in it can become a predetermined code.
- **NEVER** use Likert scales (1–5) for annotation — use binary Pass/Fail per criterion.
- **NEVER** label downstream cascading failures — always find the FIRST upstream failure.
- **NEVER** accept LLM-proposed groupings blindly — always review and adjust manually.
- **NEVER** call `get-span` or `list-spans` without a `-j` projection. One trace's spans measured 115,868 bytes unprojected.
- **ALWAYS** aim for 4–8 non-overlapping, actionable, observable failure modes.
- **ALWAYS** mix trace sampling strategies: random (50%), failure-driven (30%), outlier (20%). With window decisions available, the failure-driven share comes from the selection pass (Phase 1); the random half stays random.
- **NEVER** report a window rate for a mode the user has not cleared after seeing its agreement numbers (Phase 4). A decision model that disagrees with the traces you read measures the wrong thing at scale.
- **ALWAYS** resolve trace field names at runtime from `list-fields` / `list-facets`, and fail loudly on a name that no longer resolves.
- **ALWAYS** write the artifact (Phase 5). An unwritten artifact silently pushes every downstream skill back into re-running its own error analysis.

**Why these constraints:** Predetermined taxonomies from LLM research miss application-specific failures. Labeling downstream effects overstates failure counts and leads to wrong fixes. Binary labels have higher inter-annotator agreement than scales. A stale field name returns zero rows *without erroring*, which reads as "clean".

## Workflow Checklist

```
Agent Analysis Progress:
- [ ] Phase 0: Relay the config context block (sweep + detail)
- [ ] Phase 1: Collect traces (target 100), selection pass over the window when decisions are available
- [ ] Phase 2: Open coding — read and annotate (freeform notes)
- [ ] Phase 3: Axial coding — group into failure modes, one decision question per mode
- [ ] Phase 4: Quantify, locate, and classify; calibrate, then label the whole window
- [ ] Phase 5: Write error-analysis.md and hand off
- [ ] Phase 6: Iterate until the taxonomy stops moving
```

## Done When

- Every item in the Phase 0 relay list is either a measured value or named in `unobservable` with a reason
- 50+ traces read with freeform annotations; 20+ bad traces annotated with specific failure descriptions
- 4–8 non-overlapping, actionable failure modes defined with Pass/Fail criteria
- Taxonomy stable across 2+ coding rounds (no new categories emerging)
- With decisions available: every mode has its agreement numbers shown to the user, and every mode the user cleared has a `window` rate; otherwise `decisions` says why not
- `error-analysis-<key>-<timestamp>.md` written, its front matter parses, and its lead table alone is enough to decide what to do first

**Companion skills:**
- `orq-improve-agent` — apply a prompt or config fix from the artifact this skill writes
- `orq-build-evaluator` — build an evaluator for a persistent failure mode that has none
- `orq-recommend-evaluators` — turn the `fix: evaluator` modes in this artifact into a ranked, approval-gated evaluator set
- `orq-evaluator-alignment` — realign an evaluator that already exists and disagrees with human judgement
- `orq-build-agent` — re-architect: task decomposition, a new pipeline stage, `team_of_agents`
- `orq-generate-synthetic-dataset` — generate test data when no production data exists
- `orq-run-experiment` — measure whether a change actually improved anything
- `orq-cli` — pull traces from the shell for anything that must run again without an agent present (CI, cron, scripts) or bulk export to a file
- `orq-red-team` — adversarial testing to surface failure modes proactively
- `orq-simulate-agent` — generate multi-turn traces when production data is sparse

## When to use

Three distinct branches:
- **"what's failing?" / "why are my outputs bad?"** — you have traces and no idea where to start
- **"debug my agent"** — a named agent, deployment, or local agent is underperforming in production
- **Before building any evaluator** — error analysis must come first

## When NOT to use

- **Know the failure modes and want a fix applied?** → `orq-improve-agent`
- **Know the failure modes and need an evaluator?** → `orq-build-evaluator`
- **Want to run an experiment?** → `orq-run-experiment`
- **Want to build a new agent?** → `orq-build-agent`

## Target modes

The target is **optional**. Three modes, and the config read degrades across them:

| Mode | Config read | Config write | Behaviour |
|---|---|---|---|
| **orq agent** (key or id given) | `mcp__orq-workspace__get_agent key=<key>` (primary, workspace-scoped) or `orq agents retrieve <key> -o json` (fallback, project-scoped — 404s if agent is in another project) | `orq agents update` (in `orq-improve-agent`) | Full loop. |
| **orq deployment** | `mcp__orq-workspace__get_deployment key=<key>` (primary, workspace-scoped) or `orq deployments get-config` (fallback, project-scoped — 404s if the deployment is in another project) | prompt versions via HTTP only | Analysis is full; config fixes are recommended in prose. |
| **local / no orq entity** | **ask the user** | none | Full analysis; fixes are handed back as a diff to apply by hand. |

**In local mode, ask for the config — do not give up.** A local agent still has a temperature, a `max_tokens` and a tool list; they live in the user's code rather than in orq. Ask the user to paste them or point at a config file. The declared-vs-observed comparisons then work normally.

Terminal states (`max_iterations`, `max_time`, `error`, `finish_reasons`) live on the trace itself and need **no declared config at all** — they are readable in every mode.

If the user declines to supply a config, the affected items go in `unobservable`. **Never as a pass.**

## Core Principles

### 1. Read Before You Automate
Never build evaluators, change prompts, or switch models until you've read at least 50 traces and understand the failure patterns.

### 2. Focus on the First Upstream Failure
In multi-step pipelines, a single upstream error cascades. Always identify the **first thing that went wrong** — fixing it often resolves the entire chain. **One config bug produces the same cascade one level up:** a `max_iterations` cap can generate six behavioural symptoms. That is what Phase 0 exists to make visible before coding starts.

### 3. Let Failure Modes Emerge from Data
Grounded theory: open coding → axial coding. Do NOT start from a predetermined taxonomy. Your application's failure modes are unique.

### 4. Binary Labels, Not Scales
Pass/Fail per specific criterion. Likert scales introduce noise and slow you down.

### 5. Phase 0 Relays; the Coder Judges
There are no flags, no thresholds and no pass/fail per check. A 12% truncation rate is a crisis for a summariser and irrelevant for a classifier that emits one token — that call needs the agent's `instructions`, `role` and the traces, all of which the coder has and a constant in a skill file does not.

## Window decisions (`scripts/decide.py`)

Reading finds the failure modes; it cannot count them over a window of thousands of traces. A decision model can: it answers typed yes/no or choice questions about one trace for about $0.001, with several questions in one call. `scripts/decide.py` runs one such call per trace over the whole window. It is the measuring instrument; **discovery stays with you**, because a decision model answers the questions it is given and cannot name a failure nobody asked about.

**Run it with uv from the working directory**, as `uv run <this skill's directory>/scripts/decide.py …`, so its input and output files land next to the artifact and not in the plugin cache. It needs `ORQ_API_KEY`; `ORQ_API_BASE_URL` overrides the host.

| Subcommand | Does | When |
|---|---|---|
| `probe` | one tiny call per model: are decisions reachable with this key? | Once, before Phase 1 |
| `estimate --search-body body.json [--questions q.json]` | counts the window, measures a sample of conversations, projects cost per route | Before every `label` |
| `label --search-body body.json --questions q.json --out <pass>.jsonl` | one call per trace, appended to a resumable JSONL: re-run the same command to finish an interrupted pass. `--trace-ids <file>` (one id per line) labels a list instead of the window | Phase 1 selection, Phase 4 |
| `pick --labels sel.jsonl --question failed --write picks/` | flagged traces plus a random slice, as reading picks; `--write` also saves `flagged_ids.txt` and `random_ids.txt` | Phase 1 |
| `agreement --labels … --questions … --agent-labels coded.json [--random-ids picks/random_ids.txt]` | per mode: TP, FP, FN, TN, precision, recall, Cohen's kappa, by route and for the random picks alone | Phase 4 calibration |
| `rates --labels … --questions …` | per mode: count and share of labelled traces, by route; what could not be labelled, and why | Phase 4 |

**One out file per questions file.** Each row records which questions it answers. `label` refuses an `--out` that holds answers to a different questions file, and `agreement` and `rates` leave such rows out, so a rewritten question is never answered by stale rows. Use `sel.jsonl` for the selection pass, `calib.jsonl` for calibration, and `window.jsonl` for the window pass, and pick a new name after rewriting a question.

`--search-body` is the same `traces search` body you build for Phase 1 (`trace-queries.md` §1): RFC3339 `from`/`to` and the target in `filters`. The script sets the sort and pages through every trace.

**Input routing, and why it is not chunking or trimming.** Jev (`typesafe/jev-latest`) takes at most about 32k tokens of state. A trace that fits goes to Jev whole. A larger one goes to `openai/gpt-6-luna` (1.05M context, about 2.4× Jev's price per token) whole; a Jev call that still overflows is retried there. Chunking with max pooling makes long traces look worse, because every extra chunk is another chance of a false flag; trimming makes them look better, because the middle and the tool output go. Either way a mode's rate starts to track trace length. Each row records `model` and `route` (`short` / `long`), so agreement and rates are read per route.

**Questions file** (`q.json`): `{key: {type, instructions, criteria?, threshold?}}`. `type` is `noul` (yes/no; flagged at or above `threshold`, default 0.5, never sent) or `choice` (`criteria` is `{label: description}`; the label is the mode, `none` flags nothing). `score` questions are refused. A choice answer with no label, or a label outside `criteria`, counts as not labelled, never as a clean trace. Write instructions as a question about the trace, from the mode's Fail definition: *"Did the assistant claim the refund was issued without a successful refund tool result?"*

**Cost gate.** Run `estimate` first and show the user the trace count and projected cost before any `label`. `capped_at_max_traces: true` (in `estimate` and `label`) means only the newest `--max-traces` (default 5,000) were counted. Say so, ask whether to raise the cap, and if it stays, report the window as those traces and not the full date range. The estimate prices input tokens only, so treat the long-route share as a floor.

**Fallback.** If `probe` reports a model unavailable (no key, HTTP 401/403/404, a gateway without `/v3/router/decisions`), or the user declines the spend, run the skill as before: random-first sampling in Phase 1, rates over `read` in Phase 4. Say which path ran in the artifact (`decisions: unavailable | declined | used`).

**Clean up** `body.json`, the questions files, `coded.json`, `read_ids.txt`, `picks/` and the `.jsonl` files when the artifact is written, unless the user wants to keep them for a rerun.

## Steps

### Phase 0: Relay the Config Context Block

Runs **before** open coding. Two passes: a **sweep** over the whole window, then **detail** on ~10–20 spans drawn from the traces you were going to read anyway — no extra sampling budget.

All sweep signals go through `orq traces aggregate` with the target in `filters` (`trace-queries.md` §1). Resolve every field name at runtime.

**Gather and relay. Do not judge.** Collect the mechanical facts, format them, hand them to the coding agent. That is the whole phase.

1. **Terminal states — how runs ended, as a distribution.**
   - `attributes.gen_ai.response.finish_reasons` — `stop` / `length` / `tool_calls` / `max_iterations` / `max_time`
   - `status` — `ok` / `error` / `unset`, with the error messages
   - `attributes.orq.billing.threshold_exceeded`

   `max_iterations` and `max_time` are **explicit terminal reasons** — the platform is saying the run hit the cap. No distribution-shape inference needed.

2. **Declared config — what the agent is set to** (from `mcp__orq-workspace__get_agent`, `orq agents retrieve`, `orq deployments get-config`, or the user in local mode):
   - `model.id` and `fallback_models`
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

   > **Relay evaluator rates as unvalidated.** A judge's agreement with human judgement drifts — with foundation-model updates, and with the team's own definition of the criterion. Relay the rate, relay the sample rate, and say neither is ground truth.

4. **Name everything unreadable.** Anything that cannot be read goes in `unobservable` **with a reason**. Never omit it — absence must never read as "fine".

5. **Present it as one compact block above the open-coding table**, ending with a standing instruction rather than a verdict:

   > *These are mechanical facts about how this agent is configured and how its runs ended. Read them alongside the traces. Where a cluster of failures traces back to one of these, say so and name the knob — one config cause often produces several behavioural symptoms, and fixing the cause beats coding six of them.*

**Phase 0 is complete when every item in steps 1–3 is either a measured value or listed in `unobservable` with a reason.** That criterion is exhaustive on purpose: it is the defence against a mid-deploy trace query returning a confident, empty, non-erroring result that a looser check would wave through as clean.

### Phase 1: Collect Traces

6. **Get a quick health check** with `get_analytics_overview` before reading individual traces — overall error rate, request volume, top models. A 5% error rate on 10K requests/day is a different situation from 0.1% on 100.

7. **Gather traces.** Target **100** for theoretical saturation.

   From production: `orq traces search` (see `trace-queries.md` §1 layer 2) or `list_traces`. With no production data, use `orq-generate-synthetic-dataset` and run the inputs through the pipeline.

   | Strategy | How | When |
   |---|---|---|
   | **Random** | Uniform sample from all traces | Default; establishes the baseline failure rate |
   | **Outlier** | Sort by response length, latency, or tool-call count; sample extremes | Edge cases hiding in unusual traces |
   | **Failure-driven** | Filter on guardrail triggers, error status, negative feedback | Failures exist but the patterns are unknown |
   | **Uncertainty** | Traces where existing evaluators disagree or score near thresholds | Refining evaluators, borderline cases |
   | **Stratified** | Equal sampling across segments, features, time periods | Representative coverage across dimensions |

   **Mix:** random (50%), failure-driven (30%), outlier (20%).

   **With decisions available, the failure-driven share comes from a selection pass over the whole window**, not from status filters alone. Status filters find loud failures; a wrong answer with `status=ok` needs someone to read the conversation, and a selection pass reads all of them.

   1. Failure-driven picks start with the free signals Phase 0 already swept: `status: error`, `max_iterations`, `max_time`, `finish_reasons: length`, guardrail triggers.
   2. Write one generic question, with no taxonomy in it (`sel.json`): `{"failed": {"type": "noul", "instructions": "Did the assistant fail to do what the user asked, or claim something its tool results do not support?"}}`. `estimate`, get the user's go-ahead, then `label --search-body body.json --questions sel.json --out sel.jsonl`.
   3. `pick --labels sel.jsonl --question failed --flagged <n> --random <m> --write picks/` draws the flagged picks at random from the flagged traces. The random half is drawn uniformly from every other trace in the window, including the ones the model could not label, so silent failures the question misses, and the longest traces, still reach you.

   The selection question only decides what you read first. It is not a failure mode and never appears in the artifact as one.

8. **Ensure trace completeness.** For each trace you need the original input, the final output, all intermediate steps (LLM calls, tool calls with args and responses, retrieved documents, reasoning), and the metadata (latency, tokens, model, cost).

   **For deep reading of specific traces, use `mcp__orq-workspace__get_span` with `mode=full`.** This returns structured message arrays with all conversation turns, tool calls and responses, system instructions, and per-message `finish_reason`. Use it selectively on traces that need full content (failures, outliers), not on every trace. For the span tree and metadata, CLI `list-spans` with `-j` projection stays the compact path.

### Phase 2: Open Coding — Read and Annotate

9. **Read each trace and write freeform notes.** Read end-to-end, ask "good or bad?" (binary), and if bad, "what specifically went wrong?" — 1–3 sentences. Focus on the **first upstream failure**.

   ```
   | Trace ID | Pass/Fail | Freeform Annotation |
   |----------|-----------|---------------------|
   | abc123   | Fail      | "Dropped persona on simple factual question, plain English" |
   | def456   | Pass      | "Good — maintained character on a technical topic" |
   | ghi789   | Fail      | "Called search instead of calculator" |
   ```

   **Record the passing ids too.** They are the other half of the regression set (Phase 5).

10. **When stuck articulating what's wrong**, use these as prompts — not as categories to fill: hallucination · instruction non-compliance · persona/tone drift · tool misuse · context loss · over/under-verbosity · guardrail bypass · structural errors.

11. **Stop at saturation:** at least **20 bad traces** annotated, and new traces stop revealing fundamentally new failure types. Typically 50–100.

### Phase 3: Axial Coding — Structure the Taxonomy

12. **Group annotations into failure modes.** Some clusters are obvious ("wrong tool" + "hallucinated tool" = Tool Selection Errors). Some need splitting ("hallucinated facts" vs "hallucinated user intent"). Some need merging.

    **This is where a Phase 0 fact becomes an attribution.** If a cluster traces back to a relayed fact, say so and name the knob — that becomes `caused_by` + `knob` on the mode. It is your judgement, never Phase 0's.

13. **Use LLM assistance carefully.** After 30–50 traces, paste the annotations in and ask for groupings. **NEVER accept them blindly.** The LLM spots patterns; you make the taxonomy decisions.

14. **Define each failure mode precisely:**
    ```
    Failure Mode: [Name]
    Description: [1-2 sentence definition]
    Pass: [What "not failing" looks like]
    Fail: [What "failing" looks like]
    Example: [A concrete trace excerpt]
    ```

15. **Ensure each mode is** non-overlapping (a trace belongs to 0 or 1), actionable, observable (two people agree), and few (4–8, not 20+).

    **With decisions available, turn the taxonomy into a questions file (`modes.json`).** Because the modes are non-overlapping, the natural form is one `choice` question whose `criteria` are the modes plus `none`, each described by its Fail definition. Use one `noul` question per mode instead when a mode is easier to ask on its own, and expect `rates` to report traces flagged for more than one mode. Keys and labels are the mode slugs you will use in the artifact.

### Phase 4: Quantify, Locate, and Classify

16. **Label all traces against the taxonomy** and compute error rates per mode: count ÷ traces read. Because modes are non-overlapping, the per-mode **counts** sum to `failed` and can never exceed it — so the rates sum to `failed ÷ read`.

    **Then, with decisions available, measure each mode over the whole window.**

    1. **Calibrate on what you read.** Write your own labels as `coded.json`, `{trace_id: mode slug or "none"}` for every trace read, using the exact slugs in `modes.json`. Write its keys one per line to `read_ids.txt`. Then run `label --trace-ids read_ids.txt --questions modes.json --out calib.jsonl` and `agreement --labels calib.jsonl --questions modes.json --agent-labels coded.json --random-ids picks/random_ids.txt`. No extra labelling is needed: the traces you read are the reference. Fix anything `agreement` lists under `unknown_agent_labels` before reading the numbers.
    2. **Show the user the agreement per mode** (agent positives, precision, recall, kappa, by route and for the random picks) and decide together which modes go window-wide. There is no fixed bar. Weigh:
        - how many positives the number rests on (kappa on 3 positives says little)
        - whether misses or false flags matter more for this mode
        - whether the long route agrees as well as the short one
        - **the `random` row over `all`.** The read set is enriched with traces the selection pass flagged, so precision on `all` overstates precision at the window's real prevalence.
    3. **A mode that does not agree well enough** is acted on, not dropped. Read the disagreements. If the question is vague, or the definition leans on context the model cannot see, rewrite the question, label the same traces into a new out file, and re-run `agreement`. If the model is wrong on clear cases, say so and keep that mode's rate over `read`.
    4. **Label the window with the same `modes.json` that was calibrated.** A trimmed questions file would be a different instrument from the one the agreement measured, and it saves nothing, because all questions share one call. Run `estimate --questions modes.json`, get the go-ahead, then `label --search-body body.json --questions modes.json --out window.jsonl` and `rates`. Report a window rate only for the modes the user cleared: their count over the traces labelled. Report what `rates` lists under `not_labelled`, and the route split, next to it.

    The read-based rate stays: it is what the agreement was measured on, and it is the only rate for modes that were not cleared.

17. **On a multi-step agent, record `where` on each failure mode** — the state where the first failure occurred, and the state it followed:

    ```
    where: ExecSQL after GenSQL
    ```

    Build it from the ordered span sequence of the failed traces — **one projected `list-spans` call per failed trace**, verbatim from `trace-queries.md` §3:

    ```bash
    orq traces list-spans <trace-id> -o json \
      -j "data[].{id:span_id,parent:parent_span_id,name:name,type:type,start:started_at,status:status}"
    ```

    The state discriminator is `type` + `name`. Sort client-side by `started_at`. **Degrade by having nothing to say:** on a single-step agent write the bare `where: <state>` and drop the `after` clause, or omit `where` entirely. Where span structure is genuinely unusable, add `span_order` to `unobservable`.

18. **Assign a lever to each failure mode** — the `fix` field, one of seven values:

    | `fix` | Meaning |
    |---|---|
    | `prompt` | The instructions under-specify the task |
    | `config` | A knob is wrong — `max_tokens`, `temperature`, `max_iterations`, `reasoning_effort` |
    | `tools` | Tool inventory or tool descriptions |
    | `retrieval` | Knowledge base attachment or retrieval quality |
    | `structure` | Re-architecture — decomposition, a new pipeline stage, `team_of_agents` |
    | `evaluator` | The mode needs an automated check, or an existing judge is the problem |
    | `code` | Application code outside the platform |

    On a `fix: config` mode, also record **`knob`**, **`current`** and **`suggest`** — that triple is what lets `orq-improve-agent` build a patch without re-reading a trace. Clamp `suggest` to the real bounds (`max_iterations ≤ 100`, `max_execution_time ≤ 600`, `temperature ≤ 2`).

19. **On `fix: evaluator` modes only, add `classification`** — `specification` / `generalization-code-checkable` / `generalization-subjective` / `trivial-bug`. `orq-build-evaluator` consumes it: code-checkable-vs-subjective is what decides which evaluator gets built. It earns its place nowhere else, because `fix` already carries the routing.

### Phase 5: Write the Artifact and Hand Off

20. **Write `./error-analysis-<target-key-or-"local">-<YYYYMMDD-HHMMSS>.md`** in the working directory. One file: YAML front matter for skills, prose body for humans.

    **Persist only what a failure mode points at.** The Phase 0 sweep was consumed by the coder before this file was written; every fact attributed to a cluster is already on that mode as `caused_by` / `knob` / `current`. Persisting the rest makes the artifact a dump. The one exception is `unobservable`, which is load-bearing — it is what stops a gap reading as a pass.

    ````markdown
    ---
    target: { mode: agent, key: support-bot, version: 1.4.0 }    # mode: agent | deployment | local
    window: { from: 2026-08-12T00:00:00Z, to: 2026-08-26T00:00:00Z }
    traces: { swept: 3412, read: 80, failed: 20, labelled: 3390 }   # labelled: by the decision model
    decisions: used                       # used | unavailable | declined
    unobservable: [reasoning_effort]      # could not be read — never absent, never assumed fine

    failure_modes:                        # rates are over `read`; modes are non-overlapping,
      - name: Drops persona on factual questions   # so their COUNTS sum to `failed`, never past it
        rate: 0.125                                # 10 / 80
        window: { rate: 0.094, count: 319, labelled: 3390, routes: { short: 3301, long: 89 },
                  agreement: { agent_positives: 10, precision: 0.82, recall: 0.9, kappa: 0.83 } }
        fix: prompt              # prompt | config | tools | retrieval | structure | evaluator | code
        evidence: [cc27bfcc, a6502141]

      - name: Answers cut off mid-sentence
        rate: 0.075                                # 6 / 80
        fix: config
        where: ExecSQL after GenSQL       # optional — omit on single-step agents
        caused_by: finish_reasons.length  # your attribution back to a Phase 0 fact
        knob: model.parameters.max_tokens
        current: 8000
        suggest: 16000
        evidence: [b59b6a76]

      - name: Wrong tool on ambiguous queries
        rate: 0.05                                 # 4 / 80
        fix: evaluator
        where: DecideTool
        classification: generalization-subjective  # evaluator modes only
        evidence: [4d10ffa1]

    passing: [7f31aa02, 91b0cd44, e3c0187b]   # read and judged good — the don't-regress half
    ---

    # Error analysis — support-bot
    80 read · 75% pass · 3412 swept · 12–26 Aug

    | # | Failure mode | Rate (read) | Rate (window) | Fix | Where |
    |---|---|---|---|---|---|
    | 1 | Drops persona on factual questions | 12.5% | 9.4% of 3,390 | prompt | — |
    | 2 | Answers cut off mid-sentence | 7.5% | not cleared | config — `max_tokens` 8000→16000 | ExecSQL |
    | 3 | Wrong tool on ambiguous queries | 5% | not cleared | evaluator → alignment | DecideTool |

    Not observable this run: `reasoning_effort` — OTLP spans carry no request params.

    ## 1. Drops persona on factual questions — 12.5%
    ...definition, Pass/Fail criteria, a real trace excerpt...
    ````

    **Contract rules:**
    - **`fix` is the only routing field.** Seven values, one taxonomy.
    - **`knob` + `current` + `suggest`** appear only on a `fix: config` mode, and only because you judged it.
    - **`caused_by` names the Phase 0 fact; `knob` names the fix.** Both are your judgement. Absent is the normal case.
    - **`rate` is over `read`**, and because modes are non-overlapping the per-mode **counts** sum to `failed` — the rates sum to `failed ÷ read` (here 0.125 + 0.075 + 0.05 = 0.25 = 20/80).
    - **`window` appears only on a mode the user cleared in Phase 4**, and its `rate` is over `labelled`, not `read`. It does not change `rate`: consumers that read `rate` keep working, and the sum rule above holds for `rate` only. Its `agreement` is what the clearance rested on; keep it next to the number. With `decisions: unavailable` or `declined`, no mode has `window` and `traces.labelled` is absent.
    - **`unobservable` is a list, not a boolean.** A consumer must check it before claiming the run was clean.
    - **`passing` is the other half of the regression set.** `evidence` records what failed; `passing` records what was read and judged good. Without both, the closing experiment runs against whatever dataset happens to exist.
    - **The body leads with the work list.** One scannable table — mode, rate, lever, location — then the depth below it.

21. **Hand off — the artifact is the router.** Name a destination per failure mode and let the user go wherever they want first. Do not forward on their behalf.

    | `fix` | Destination |
    |---|---|
    | `prompt` · `config` · `tools` · `retrieval` | `orq-improve-agent` |
    | `structure` | `orq-build-agent` |
    | `evaluator` — none exists for this mode | `orq-build-evaluator` |
    | `evaluator` — one exists and disagrees, flips, or is the mode's only evidence | `orq-evaluator-alignment` |
    | `code` | Report it; out of platform scope |
    | *no data to work with* | `orq-generate-synthetic-dataset` |

    **Always close by recommending `orq-run-experiment`**, against a dataset built from this file's `evidence` + `passing` ids.

    > **A mode whose only evidence is `eval_*` is not actionable.** If no trace you read and coded backs it, route it to `orq-evaluator-alignment` and re-run the analysis. An unaligned judge cannot distinguish "the agent got worse" from "the judge drifted", and acting on it changes the agent to satisfy the judge.

### Phase 6: Iterate

22. **Iterate until the taxonomy stops moving** — definitions sharpen, edge cases get clarified, and you stop minting new categories. When a round produces no new modes and no redefinitions, it is stable.

## Common Pitfalls

| Pitfall | What to Do Instead |
|---|---|
| Skipping open coding — jumping to generic categories | Read traces, write freeform notes, let patterns emerge |
| Letting Phase 0 become the taxonomy | Phase 0 relays facts; the coder decides which of them matter |
| Coding six symptoms of one config cause | Attribute the cluster to the knob — that is what Phase 0 is for |
| Using Likert scales for annotation | Binary pass/fail per specific failure mode |
| Freezing the taxonomy too early | Keep iterating until a round adds nothing |
| Excluding domain experts from analysis | The person who knows "good output" best should do the analysis |
| Unrepresentative trace sample | Sample across time, features, user types, difficulty |
| Labeling downstream cascading failures | Always find and label the FIRST upstream failure |
| Building evaluators for every failure mode | Only automate persistent generalization failures |
| Reading a zero as "clean" | Re-probe. A stale field name and a mid-deploy query both return empty without erroring |
| Reporting the analysis in the conversation only | Write the artifact. Otherwise every downstream skill re-runs this |
| `get-span` / `list-spans` without `-j` | Always project. One trace's spans measured 115,868 bytes raw |

## Documentation & Resolution

**Lookup order: [`doc-resolution.md`](../orq-shared/resources/doc-resolution.md).** Live queries first — for this skill that means `orq traces …` and the orq MCP read tools, then [`trace-queries.md`](../orq-shared/resources/trace-queries.md) — the verified CLI contract for everything in this skill.

