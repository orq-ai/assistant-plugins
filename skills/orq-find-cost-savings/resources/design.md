# orq-find-cost-savings: design

Spec for `scripts/cost_savings_report.py`. Statements marked **V** were checked against the live orq API and `orq` CLI. Where the script differs from the first draft of this spec, the section says so.

## Scope

| Decision | Choice |
|---|---|
| Who runs it | The customer, on their own workspace |
| Form | The script does all arithmetic and writes `report.json`; the agent writes the narrative; `render` merges both into HTML |
| Window | `to` = run start; `from` = `to` - min(`--window` days, default 30, workspace retention) |
| Sample | `--sample N` leaf LLM spans, default and v1 maximum 100 |
| Excluded from the sample | `span.cache_retrieval` (response cache hits), embedding models, decision models (`typesafe/*`, `orq/decider-*`) |
| Routing policy | The Smart Router's `jev-general-v1` decision, reproduced client-side and unchanged |
| Profile | Balanced headline; cost as a scenario column |
| Pool | Frontier pool from evaluatorq's `model_frontier` (no pool member dominated by another), in four scopes: same or any provider, enabled or all available; headline is same provider, all available |
| Change model | Second lever (JSON key `switch`): move a model's traffic to the cheapest model that dominates it; never added to routing. The page says "change model", not "switch", so it is not confused with routing |
| Transport | Reads shell out to the `orq` CLI with `-o json`, so they reuse the user's CLI login and workspace; decision calls use the `orq-ai-sdk` (`router.decisions.create`) with the CLI login's token from `orq auth token`, else `ORQ_API_KEY` |

## Layout

```
skills/orq-find-cost-savings/
  SKILL.md
  resources/design.md            this file
  resources/report.schema.json   output contract, generated from the script's pydantic models
  scripts/cost_savings_report.py PEP 723 header; depends on evaluatorq (which brings pydantic)
  tests/requirements.txt         makes CI run the suite
  tests/test_*.py                load the script with importlib.util.spec_from_file_location
  tests/fixtures/                synthetic spans, decisions and catalog entries; never customer data
```

Commands:

```
uv run <skill dir>/scripts/cost_savings_report.py analyze --workspace <key> --out <dir> [--window 30] [--sample 100] [--filters '<json>'] [--counterfactual-test] [--json] [--cache-threads 200] [--yes]
uv run <skill dir>/scripts/cost_savings_report.py render --out <dir> --narrative <dir>/narrative.md [--redact]
```

`analyze` writes `report.json` and a `narrative-slots.md` listing the slots the agent fills (summary, routing, caching, caveats). `render` refuses to run if a slot is empty or the analysis stopped. Before the paid decision calls `analyze` prints their count and cost and asks; without a terminal it stops with check `confirmation` unless `--yes` is given. It also stops at once if `typesafe/jev-latest` is not enabled in the workspace (V: it is disabled in some workspaces, and the call then returns 403).

## Field map (V)

| Item | Where |
|---|---|
| Telemetry model | `openai/gpt-6-luna` (provider-prefixed) |
| `list-spans` model | bare `model` (`gpt-5.6-terra`) plus `provider` (`openai`); join as `provider + "/" + model` |
| `list-spans` fields | `.data[]` with `type`, `has_children`, `started_at`, `cost`, `usage`; pages with `--limit`, `--page-token`, `.has_more`, `.next_page_token` |
| `get-span` | wrapped in `.span`; cost at `.span.summary.cost`; billing at `.span.attributes.orq.billing.*`; usage at `.span.attributes.gen_ai.usage.*` |
| Input messages | `.span.attributes.gen_ai.input.messages[]`; text under `.parts[]` shaped `{"type":"text","content"}`, `{"kind":"text","text"}` or `{"type":"input_text","text"}`, or `.content` as a plain string. The system message may have role `system` or `developer` |
| Cache-write TTL | `usage.prompt_tokens_details.cache_creation_<ttl>_tokens` and `orq.billing.cache_write_<ttl>` (for example `30m`) |
| Pricing tier | `cost.pricing_tier` or `openai.response.service_tier` |
| Trace search model | summaries carry bare `models: [...]`; filter on the prefixed id, since the bare form can match other hosts |
| Thread filter | `--filters '[{"field":"thread_id","op":"eq","values":["<id>"]}]'` with `--project`; without `--project` it returns 0 rows |
| Telemetry paging | no cursor; `--limit` up to 5000 and `.has_more` |
| Telemetry filters | `[{"field":"model","op":"eq","values":["<bare model>"]},{"field":"provider","op":"eq","values":["<provider>"]}]`; the prefixed id matches nothing |
| Request counts per cell | `genai.requests` with op `count` (not `sum`), grouped by `project`, `--mode timeseries --grain hour`; `trace_count` cannot be grouped by project. Time-series results are complete only within 5000 rows, 5000 buckets and 50000 values (`orq telemetry query --help`); past that the result has `has_more` or the query fails, so a model used in 7+ projects over 30 days falls back to 6-hour buckets (`--interval-seconds 21600`) and then `--grain day`. `params.cell_grain` records the bucket per model; a `telemetry truncated` caveat is added only when even daily cells are truncated. A draw searches windows inside its bucket |
| Thread and retention on a span | `.span.attributes.orq.thread_id`, `.span.attributes.orq.retention_days` |
| Region and speed on a span | `.span.attributes.gen_ai.usage.inference_geo` (`global` means none), `.span.attributes.gen_ai.usage.speed` |
| `orq models list -o json` | a bare array, not `.data` |
| Metric and dimension names | `orq telemetry list-capabilities --source TELEMETRY_SOURCE_TRACES -o json` |

Every telemetry and trace command needs RFC 3339 `--from` and `--to`; without them it returns 400 `from: value is required` (V). A window longer than retention returns 400 `time range exceeds workspace retention`; trace search past retention returns `[]` without an error (V). Read retention from that error or from workspace settings; stop if a stratum's trace search is empty while telemetry shows requests for it.

## Token accounting (V)

`prompt_tokens` includes cached and cache-write tokens. Uncached input = `prompt_tokens` - `cached_tokens` - `cache_creation_tokens`, and `input_cost` bills exactly that. `cached_tokens_cost` and `cache_read_cost` hold the same value. Gemini reports cache reads only.

## Routing counterfactual

### 1. Routing input

The routing span is the trace's root (`root_span_id` on the search row); when the root carries no input messages (an agent or workflow root), it is the trace's last leaf LLM span by `started_at`, read with `list-spans` and `get-span`. `gen_ai.input` is either a list of messages or `{messages: [...]}` (V: chat completions roots use the list). The whole trace is routed on that one input, so a trace with many LLM calls (telemetry `genai.requests / trace_count` over 3, reported per model as `sampling.calls_per_trace`) gets an `agent-heavy traffic` caveat.

From the span's input messages, take the newest user turn that still has text after cleaning:
- Join the turn's text parts with a single space.
- Remove complete `<system-reminder>...</system-reminder>` blocks; an unclosed tag stays.
- Trim each remaining piece and join the non-empty ones with a single space.

With no user text, fall back to the system (or `developer`) prompt, then `instructions`. If that is empty too, count a fallback (`empty_input`). Over 65,000 characters: no decision, `prompt_limit`.

### 2. Score

`client.router.decisions.create(**body)` from `orq-ai-sdk` (`POST /v3/router/decisions`), with this body:

```json
{
  "model": "typesafe/jev-latest",
  "state": "<routing input>",
  "timeout": {"call_timeout": 1500},
  "retry": {"count": 0},
  "questions": {
    "difficulty": {
      "type": "choice",
      "instructions": "How difficult is this prompt for an LLM to complete well?",
      "criteria": {
        "easy": "Trivial change, any model handles it",
        "medium": "Moderate complexity, benefits from a capable model",
        "hard": "Complex task, needs a strong model"
      }
    }
  }
}
```

The `--questions` flag form fails (V); use a body file. The answer is at `.answers.difficulty.probabilities`. The router allows the decision 1,500 ms with no retry; `call_timeout` and `retry.count: 0` reproduce that server-side, so a timeout error from the API (not client wall time) counts as `decision_timeout`. Tag each call with `name: "cost-savings-report"` so later runs can exclude these traces.

Score = `p(easy)*0 + p(medium)*1 + p(hard)*2`, summed in that order in float64 without rounding. Results within one float bit of a threshold may differ from the router's; this is not handled.

### 3. Tier

| Profile | easy if score <= t1 | medium if score <= t2 | else hard |
|---|---|---|---|
| cost | 0.59 | 1.16 | |
| balanced | 0.11 | 0.92 | |

The question text and thresholds are one frozen policy (`jev-general-v1`); changing either makes it a different policy.

### 4. Band and model

Pool validity: every member has `autorouter.v2` metadata with a nonzero `intelligence_index`, and a pool has at least 2 members; otherwise "no routing", listed.

The pool rule (below) decides only which models are in the pool. Band position and "stronger" always come from the live index. Sort strongest first, ties by model id. `easy` gets the weakest `len/2` models; the rest split between `medium` and `hard`, hard getting the smaller half. With 3 models that is one per band; with 2, `hard` is empty and resolves to `medium`. An empty target band uses the nearest stronger band, else the strongest present. Inside a band, balanced and cost pick the lowest `autorouter.v2.price`, then the highest index, then model id.

### 5. Fallback

Any decision failure (`decision_timeout`, error, `prompt_limit`, `empty_input`) serves the default model. The counterfactual uses the customer's current model as that default, so a failure counts as "no change". The real router uses the first model in its pool config; the report states this choice.

### 6. Savings per trace

The unit is the trace. Its search row carries `usage` and `cost` of the whole trace (V: equal to the sum of its LLM calls on 155 of 155 traces), plus the attributes the pricing function needs (`gen_ai.usage.prompt_tokens_details.cache_creation_<ttl>_tokens`, `openai.response.service_tier`), so no span is priced separately. A trace whose `models` list does not have exactly one model is not in the pool: the search rows of the drawn window are filtered to single-model traces before a trace is picked, and inclusion probabilities are computed over that pool only. Their share is estimated per model as the ratio of the weighted sums, over each draw's first window, of its multi-model trace cost to its whole trace cost (weight 2^k / the cell's spend, the same inclusion as the draw) (`routing.strata[].multi_model_share`; an approximation, since a trace's cost counts all its models while the model's spend counts only its own calls). The router rate is scaled to the model's single-model spend only, and the rest is reported under `routing.not_analysed.multi_model` (spend, share of all spend, per-model share), in `summary.not_analysed.multi_model_per_month`, in the caveat `multi-model traces` and in the page's not-analysed note.

Savings = price(trace, current model) - price(trace, picked model), both from the pricing function on the trace's token breakdown (uncached input, cache read, cache write, output). Negative savings are kept. A picked model without a cache-read price takes cached tokens at its input price. Output tokens are assumed similar.

No cold-cache penalty: the picked model is priced on the same uncached, cached and output tokens as the original span. A cold cache only matters when a router upgrades a thread mid-conversation to a more expensive model, which this report does not model, so no previous turn is fetched and no extra decisions are made.


### 7. Pricing function

Model catalog prices carry `variants[].when`. The evaluator implements the full grammar found in the catalog (V): variables `request_unix`, `input_tokens`, `tier`, `inference_geo`, `speed`, `operation`; operators `==`, `!=`, `>`, `>=`, `<`, `<=`, `&&`, `||`, `/`, `%` and parentheses. Example: an off-peak rule on two DeepSeek models of the form `((request_unix / N + N) % N < N) && (...)`.

| Variable | From the span |
|---|---|
| `request_unix` | `started_at` |
| `input_tokens` | `gen_ai.usage.prompt_tokens` |
| `tier` | `cost.pricing_tier` or `openai.response.service_tier`; `default` and `auto` mean `standard` |
| `inference_geo` | host region from the model id: `us.`, `us-east-1.` and similar map to `us`, `eu.` to `eu`, `global.` to none |
| `speed`, `operation` | span attribute if present, else the variant does not match |

Choosing the price, the same way for the current and the picked model:
- Default to the base price.
- Use a variant only when the span clearly shows what its condition tests: a region in the model id, a batch, priority or flex tier, a long context, a speed setting. A variable the span does not show counts as false, so an unclear span stays on the base price.
- When several variants match, take the one whose condition has the most comparisons, so it aligns closest with how the client calls the model. Example: a US-region batch call with 150k input tokens on a model with `input_tokens > 100000`, `inference_geo == "us"`, `tier == "batch"` and their combinations takes the variant that tests all three. Ties go to the last listed: the catalog lists a newer price after an older one (V: two DeepSeek date cutoffs, where first-listed priced 41% too high).
- A variant overrides the base price key by key; a price it does not list comes from the base (many variants list only some, V).
- If the picked model has no variant for something the span clearly shows (for example the `priority` tier), the span is flagged and its stratum counts as not covered, so a base price never inflates savings.
- Cache writes use the span's own TTL; if the model lacks it, the shortest TTL it has.

The pricing check below catches a model where this choice disagrees with what orq billed.

Check: price each sampled span at its own model and compare with `.span.summary.cost`, per model. A model more than 2% off is left out of savings (not covered) and listed with the gap. V: on 102 spans across 11 models every model priced within 0.33% of what orq billed, and the variant-or-base choice agreed with `orq.billing.input.source` on all 102.

### 8. From sample to total

Strata are models (`provider/model`), not project and model: a workspace can have hundreds of (project, model) pairs, most with negligible spend (V).

Sample size N (traces): `--sample`, else the plan default (see Runtime): free 15, growth/sandbox/pov 100, every other plan 500 (`MAX_SAMPLE`).

Allocation of N traces:
1. Rank models by `genai.cost` in the window. Only models with token totals are ranked (see Change model from token totals).
2. Take models in order while each can get at least 5 traces under spend-proportional allocation; that gives at most N / 5 strata.
3. Models not taken are not covered by the router figure (change model may still cover them, see below).
4. Draw order: every model's first 5 draws, then the rest interleaved by allocation, so a run that stops after any round is spread like the full allocation.

Selection inside a stratum, probability proportional to cost (PPS):
1. Cells: `genai.cost` per (project, hour) for that model (with `--filters`, the same from the scanned rows, always hourly); request count (`genai.requests`) when the model has no recorded cost (`params.cell_weight`). `trace_count` is not used for cells: it groups by project unreliably and is missing for some models (V: 0 traces against 148,800 requests on one model). Hourly cells over the time-series budget fall back to 6-hour, then daily (`params.cell_grain`).
2. Draw a cell with probability C_cell / C_model.
3. `traces search` on the whole cell (filter `model`, limit 100). While it returns the 100-row cap (the newest 100, V), halve the window and take one half at random; the halves form a fixed grid, so a window k halvings deep is reached with probability 2^-k and every search is cached by its window. Below 1 s still at the cap: redraw, counted in a `dense search window` caveat.
4. In the window, pick a trace with probability cost_i / C_window (search-row `cost.total`). Traces with cost 0 are never drawn; they add nothing to spend either.
5. Inclusion p_i = (C_cell / C_model) x 2^-k x cost_i / C_window, weight w_i = 1 / p_i (`samples[].inclusion`, `weight`). A draw that finds an empty cell or half-window (no single-model trace of the model in it) is retried, up to 6 attempts. p_i stays the probability that one attempt picks the trace, not renormalised over the non-empty windows: attempts are independent, so the accepted draws all carry the same factor 1 / (1 - P(empty attempt)), which cancels in the ratio estimator below (tested with an empty cell and an empty half-window in `tests/test_sampling.py`); only `inclusion` itself reads that factor too low. The cost of a window and of the traces in a cell are both estimates of the same spend, so p_i is proportional to cost_i up to how unevenly the halvings split a cell's cost.

Estimator per stratum s (Hansen-Hurwitz ratio):

```
rate_s    = sum_i w_i * savings_i / sum_i w_i * price(trace_i, current model)
savings_s = rate_s * telemetry genai.cost_s
headline  = sum_s savings_s
```

With p_i proportional to cost, rate_s is the mean of savings_i / cost_i over the draws: unbiased for the population ratio (tested on a toy population in `tests/test_sampling.py`).

- Fallbacks, `prompt_limit` and traces already routed by a Smart Router (`auto_router.*` attributes) stay in the sample with savings 0.
- A trace whose model has no pool in any scope (already the cheapest choice) gets no decision call: its router saving is its switch saving, 0 when there is no switch target (`fallback: no_pool`).
- 90% CI: bootstrap 1,000 times, resampling thread clusters within each stratum (search row `thread_id`; a trace without a thread is its own cluster), because traces of one conversation are not independent. `routing.strata[].clusters` is the distinct cluster count. Next to the CI, a "without the top 3 traces" figure drops the 3 draws whose weighted contribution w_i x savings_i x C_s / sum w x price is largest; under PPS that contribution is savings_i / cost_i scaled, so the figure still shows how much of the estimate rests on a few traces.
- Coverage: share of window spend in strata that were sampled and passed the pricing check (router); for change model also the models priced from token totals.
- Per month: headline x 30 / window days.

#### Adaptive stop

Draws run in rounds of 25 (`ROUND`); decision calls for a round are made before the next round is read. After a round, once at least 50 traces are drawn (`STOP_MIN_DRAWS`, counting traces drawn, not attempts) and every stratum has its first 5 drawn, the headline router CI is recomputed (200 bootstrap resamples, `STOP_BOOTSTRAP`). Drawing stops when its half-width is at most 25% of the estimate (`STOP_REL_HALF_WIDTH`, `relative_precision`) or at most 1% of window spend (`STOP_ABS_HALF_WIDTH`, `absolute_precision`), when N is reached (`sample_size`), or past 15 minutes of wall time between rounds (`WALL_TIME_S`, `wall_time`). `report.json` `sampling` records the target, the draws, rounds, the stop reason and the half-width, which comes from the final 1,000-resample range, not the 200-resample stop check; the page states them under Method.

#### Change model from token totals

The change-model lever needs no decision, so it is priced on the whole window, not the sample: per model, telemetry sums of `gen_ai.client.inference.usage.input_tokens`, `.cache_read.input_tokens`, `.cache_write.input_tokens` and `.output_tokens` (V: input includes cache reads and writes, so uncached = input - read - write; the `genai.tokens.*` metrics differ slightly and are not used). Saving = spend_m x (price(totals, m) - price(totals, target)) / price(totals, m), both at the base catalog price in the model id's region: totals carry no request time, context length, service tier or cache TTL, so no variant that tests one applies (an accepted limitation, stated on the page). Pools and switch targets are chosen on these totals, so a model without them (or priced more than 2% off its spend) has no change-model figure and no pool: it is listed in `routing.switch_not_covered` and `routing.not_covered` with the reason, and it is not sampled. A run where no chat model has totals stops with check `token totals`. Totals-priced models have no sampling spread (`switch.from_totals`), so the switch CI is their exact sum. The pareto check uses the same totals. Because it needs no decision calls, a free-plan run gets the full change-model figure even with 15 traces.

### 9. Buckets

Savings are also shown by ask length (characters / 4: 0-128, 128-512, 512-1024, over 1024 tokens) and by span `prompt_tokens` (0-8k, 8k-32k, over 32k). All buckets count in the headline. Flagged:
- Asks over 1024 tokens: the decision model is least reliable on long asks.
- Prompts over 32k tokens: the router scores only the ask, so a short ask on a long context rates easy even though the picked model still reads the whole context.

### 10. `--counterfactual-test`

Runs routing on the pilot asks and stops. Per ask: recommended model, model used, whether they differ. Summary: deviation rate per used model, split by blended price into "recommends cheaper" and "recommends pricier". Nothing is replayed.

### Decision cost

`typesafe/jev-latest` costs $0.042 per 1M input tokens and nothing for output (V, `orq models list`). Identical routing inputs are scored once. Estimate after the first round: unique inputs x (characters / 4 + 330) tokens x $0.042 / 1M, extrapolated to N. The confirmation states the calls, cost and expected wait, that each call sends one sampled prompt text to the decision model and appears as a trace in the workspace, and on a free plan how many of its 50 daily decision calls the run uses.

## Runtime

Plan, from `orq workspaces get` (one call, also the retention): per-minute limit L = `subscription.metadata.rateLimit`, else `features.rate_limit`, else a conservative 20 (source `default`). The plan type (`subscription.metadata.type`) sets the default sample (free 15, growth/sandbox/pov 100, every other type 500) and the free plan's 50 decision calls a day; a type the script does not know keeps its own name, has no daily limit and the default sample of 500. When the rate limit comes from `default`, the default sample is capped at 100 and the confirmation says so; an explicit `--sample` wins. If the read fails, the run follows the free plan with a `plan unknown` caveat.

Decision calls share that limit with the workspace's gateway traffic, so they are paced to 30% of L (`PACE_SHARE`) by one token bucket shared by 4 decision workers (`DECISION_WORKERS`); reads run on 16 workers (`READ_WORKERS`). The SDK client authenticates with the CLI login's token (`orq auth token`, asked for again when it expires), else `ORQ_API_KEY` when no login is available. The credentials are resolved first, before any read: with neither, or without the `orq` binary, the run stops with check `decision calls allowed`. On a 429 the SDK error's `Retry-After` header (seconds or an HTTP date, capped at the wall-time limit; 60 s when absent) holds every worker, and the call is retried; the pace halves once per hold period, so a 429 that arrives while a hold is active does not halve it again. After 6 waits the run stops with check `decision rate limit` instead of counting decision errors. A 401 or 403 stops the run with check `decision calls allowed`. One retry serves every SDK call (`sdk_call`): a 429 waits its `Retry-After`, a 5xx or a timeout backs off with jittered exponential `backoff`, and a 401 `authz_stale` (the login's workspace access changed) asks `orq auth token --refresh` for a new token once. Telemetry queries and `traces list-spans` also go through the SDK client, stored in the read cache like the CLI reads; a failure surfaces as the same `orq call failed` stop. Six reads stay on the `orq` CLI because the SDK drops fields the script uses: `workspaces get` (no `subscription` and no `features.rate_limit`, so no plan type or rate limit), `model-catalog list` (no `pricing.*` and no `artificial_intelligence.*` indices), `models list` (a different, shorter listing: 246 models against 859, without `enabled` and `configuration`), `traces search` (no `agent` and no `gen_ai.*` attributes, and its filter values are typed as strings), `traces get-span` (no `gen_ai.*` attributes, which hold the routing input) and `status` (the active workspace is local CLI state). The SDK's telemetry query also rejects the server's own empty `sort` echo, so the script sends `sort=desc`. Their retry stays in the CLI wrapper (`Orq`). The decision body keeps `retry.count: 0`; no fallback models are set. The number of decision calls the user approved at the confirmation is a hard cap that ends sampling, not the run: before each round the loop checks what is left, sends decisions for as many of the round's inputs as fit and drops the draws left without one, and the run finishes with `sampling.stop_reason` `decision budget` and status ok.

Wait shown up front: reads at about 3 s per trace over 16 workers, plus decision calls at the slower of the pace and 2 s each over 4 workers; on a free plan 15 calls at 6 a minute take about 3 minutes. The confirmation adds that sampling usually stops much earlier once the range is tight. Progress is logged every 10 decisions.

### Filters

`--filters` is a JSON list of `{field, op, values}`, passed as given and ANDed with the script's own filters (`model`, `thread_id`) on every `traces search`. Telemetry rejects fields such as `project_id`, `agent_name` and `metadata.*` ("filter field not allowed for metric"), and its trace-level metrics measure a different total, so a filtered run replaces the telemetry reads with one paged scan of the matching rows (`next_page_token`, 100 rows a page, at most `MAX_SCAN_PAGES`; past that it stops with check `filtered scan`). From the rows: spend per model and per agent (single-model traces only; the multi-model spend is `routing.not_analysed`), token totals per model from `usage`, hourly cells per (project, model) from `start_time`, and threads for the caching scan (their trace count stands in for the request count). Calls per trace and the harness table have no source in the rows and are left out. A 400 from a filtered call stops the run with check `filters` and the server's message. Filters are recorded in `params.filters` and `summary.filters`, the page header shows them as a Scope line, and a rerun with other filters does not reuse an earlier window.

### Cache

Trace and search reads are cached under `<out>/cache/reads`. They contain prompt texts, so they are deleted at the end of every run, a declined confirmation and Ctrl-C included, unless `--keep-cache` is given; a run stopped at `confirmation` outside a terminal keeps them so the approved `--yes` rerun reads the same sample for free. Decision probabilities are cached under `<out>/cache/decisions`, one file per sha256 of the routing input holding probabilities only, and kept until a run ends with status ok, so a stopped run's paid calls are not repeated.

## Pilot checks

On the first min(100, N) scored asks; in v1 that is the whole sample.

Stopping checks. On failure, write the check and its numbers to `report.json` and stop:
- Tier mix is not a single tier.
- Fallbacks below 10%; errors other than timeouts below 2%.
- No routing input contains `<system-reminder>` text; fewer than 10% came from the system-prompt fallback.
- Every recommended model is enabled and priced.

Caveat checks. Failures are listed; the run continues:
- Pricing check per model, as above.
- Reconciliation per drawn cell: leaf span cost sum against telemetry `genai.cost` for that (project, model, hour), within 5%.

## Model pool

### Data sources (V)

| Need | Source | Field |
|---|---|---|
| Model list, pricing, host mapping | `orq model-catalog list` | `pricing` (with `variants`), `offering_of` |
| Enabled in workspace | `orq models list` | `enabled` |
| Index and band price | `orq models list` | `metadata.autorouter.v2.intelligence_index`, `metadata.autorouter.v2.price` (3:1 input/output blend per 1M) |
| Recency | both | `created` |

Join `orq models list` to the catalog on `provider + "/" + model_id`; its `id` is a UUID. The catalog's own `artificial_analysis_intelligence_index` disagrees with the router's index and is not used. A project-scoped API key hides disabled models, so the all-available scopes equal the enabled ones; the report adds a `garden is project-scoped` caveat.

### Model garden

Every model comes from evaluatorq's `model_frontier` (`evaluatorq>=1.98`, PyPI), applied to the `orq models list` cards the script already loads: `garden_from_cards`, `dominates`, `pareto_frontier`, `region_compatible`, `unseatable_reason`, `model_identity`. The tier ratio is 2.0, the value evaluatorq's `MIN_TIER_RATIO` moves to; the script pins it until that release. Index is the score at the catalogue default reasoning effort; price is the 3:1 input/output blend per 1M. Dominance: at least as intelligent and no more expensive, strictly better on one.

A model is a candidate when it is scored, priced in the catalog (input and output), not free, seatable and not pre-release; "disabled in the model catalogue" is allowed, because the all-available scopes recommend enabling it. Pre-release means a name segment `preview`, `beta`, `alpha` or `experimental` (split on `-`, `_`, `.`): the catalog has no release-stage field, and these segments are the providers' own naming. A card with a reasoning-effort parameter but no score at its default effort (evaluatorq's `ranked_on_headline`) is not a candidate, and a caveat lists the models skipped for it. Candidates are one per underlying model (`model_identity`): of several routes the cheapest wins, enabled first. `google` and `google-ai` count as one provider in same-provider scopes; each route keeps its own price. A discounted offering is skipped too: a same-host `offering_of` another catalog model, priced below it on input or output (the `-contributor` offerings). Its terms are not in the catalog. Dated snapshots share the base price and stay.

### Scopes

| Scope | Candidates |
|---|---|
| Same provider, all available (headline) | Same host and region as the customer's model, enabled or disabled |
| Same provider, enabled | Same, enabled only |
| Any provider, all available | Any host, same region (`region_of` and evaluatorq's EU pinning) |
| Any provider, enabled | Same, enabled only |

Every scope reuses the same sampled spans and decisions; only the local re-pricing differs. Any-provider figures reuse the sampled token counts; other families tokenize differently, and the report says so.

### Switch rule

The switch target is the cheapest candidate that dominates the customer's model on its own traffic: index at least as high, and the model's sampled spans re-priced on the candidate (same uncached, cache-read, cache-write and output tokens) cost no more, strictly better on one. The blend ignores cache prices, so a model that wins on the blend can lose on heavily cached traffic. A candidate the spans cannot price (missing tier price) is never a target. With no sampled spans the blend is used. No minimum index gain: a model that is as smart and much cheaper is the recommendation. The dominated-models list uses the same test.

### Pool rule

1. Top seat: the switch target, else the customer's model. Hard asks are never sent below the model in use.
2. Lower seats: candidates on the scope's Pareto frontier, weaker than the top seat, a different model, each at least `MIN_TIER_RATIO` cheaper than the seat above. Price here is the customer model's sampled spans re-priced on the candidate (same tokens, cache reads and writes included), the same cost the switch rule uses, so the frontier, the tier gaps and dominance follow this workspace's traffic. A candidate the spans cannot price is never seated. With no sampled spans, evaluatorq's 3:1 blend. The 2.0 ratio means a seat counts once it is half the price of the seat above, since routing an ask down to it already halves that ask's cost. It is a judgement, not a measured figure.
3. Of the trios that pass, the one whose weakest seat has the highest index wins, ties on total index (RES-1285 criteria, as `derive_trio`).
4. No trio: the best pair, flagged relaxed (hard resolves to medium, so medium and hard asks go to the top seat). No pair: no pool, listed; the router scenario then serves the top seat, so its figure equals the switch.
5. Disabled members and the switch target are listed under "enable first".

No pool contains a model another member dominates on the same price: lower seats are on the frontier and weaker than the top seat, and the script asserts it.

Example (synthetic): traffic on gpt-6-sol where a disabled gpt-6.1-terra scores higher at a lower blended price. The all-available pool tops out at gpt-6.1-terra; an older gpt-5.6-terra that gpt-6-luna dominates is never seated in any scope.

### Under-powered asks

For every sampled model where at least half the scored asks are `hard` under balanced: that share, and the extra cost of sending the hard asks to the next model above it by index (same host and region, enabled or not, flagged).

### Pareto check

Per used model: the models that dominate it, same region, any provider, enabled first, with index and blended price. States the fact only.

## Prompt caching gaps

### Data

| Need | Source |
|---|---|
| Cache-read tokens, cache-read and cache-write cost by model, agent, deployment, thread | `orq telemetry query` metrics `genai.tokens.cached`, `genai.cost.cache_read`, `genai.cost.cache_write` |
| Cache-write tokens by model | span metric `gen_ai.client.inference.usage.cache_write.input_tokens` (not groupable by thread or agent, V) |
| Turns of a thread | thread search, then `list-spans` per trace; a turn is one leaf LLM span |

Grouped scalar queries take one compute per call (V). `agent` and `thread` cannot be grouped in one query (V); join client-side. The rollup groups agents by id, the span metric by agent key; map them with `orq agents list` (V).

### Rule

Unit: (thread, model), turns sorted by `started_at`, a turn being one leaf LLM span. Threads come from telemetry grouped by thread; scan those with 3 or more requests, top 200 by spend in v1. Caching savings are reported for the scanned threads as found, not scaled to the window, and labelled that way.

Candidate: 3 or more turns, a model with a cache-read price, and a prefix of at least 1,024 tokens. Threads that already cache count too: a thread can cache most turns and still miss some (a pause longer than the TTL, a changed prefix), and the saving below only counts input that was not read from cache.

Per turn k from 2 to n:
- alive_k: the gap since turn k-1 is within the cache TTL.
- read_k = alive_k ? prompt_tokens(k-1) : 0.
- write_k = prompt_tokens(k) - read_k.

Savings = sum_k max(0, read_k - cached_k) x (input - cache read) - sum_k max(0, write_k - actual_write_k) x (cache write - input), with prices from the pricing function at the current model. No cache-write price makes the second term 0.

Current caching is reported beside it, over every scanned (thread, model): the share of input tokens read from cache (`cached_share`), what those reads saved against the input price (`saved_by_cache`), and the threads already reading 20% or more from cache (`threads_caching`). Below $1 of savings the page shows these with a check mark instead of a caching narrative.

The rule assumes conversations append. For providers with automatic prefix caching (OpenAI), a low read ratio often means the prefix is not stable, so the OpenAI figure is an upper bound and labelled so.

Action by provider:
- Explicit caching (Anthropic-style): set `cache_control` on the agent's model parameters or the deployment variant. orq forwards breakpoints but does not add them.
- Automatic caching (OpenAI): keep the prefix stable, with the static system prompt and tools first.
- Reads-only reporting (Gemini): listed without a savings figure.

Only agents and deployments on orq count toward caching savings.

### Routing and caching together

Routing savings are scaled to the window; caching savings cover the scanned threads only. They overlap, so the report shows them side by side and never adds them.

## Harness traffic

The gateway records the calling harness as `trace_framework`, groupable on the `orq.gen_ai.*` metrics but not on `genai.cost` (V). The two metrics disagree in both directions (V: on one workspace `orq.gen_ai.cost` summed to about a third more than `genai.cost`), so the section names its metric and shows each framework's share of it; it shows an "unattributed" row to the summary total only when that difference is positive, and a caveat otherwise. The field also carries SDK names; the report lists every value and marks known coding harnesses as "caching not controllable".

## Report sections

The page shows the summary, routing and caching parts. Spend by agent and deployment, under-powered models, harness traffic and the Pareto line are in `report.json` only; `render` does not show them.

1. Summary: spend in the window and per month; routing savings with 90% CI, the top-3-excluded figure and coverage; caching savings beside it; Pareto line.
2. Spend by model, agent, deployment.
3. Routing: balanced headline; cost column; buckets with the flags above. Action: "create a Smart Router with profile balanced and pool X, Y, Z".
4. Under-powered models: share of hard asks and the cost of moving them one model up. `report.json` stores no ask text.
5. Caching gaps by agent and thread, with the action per provider.
6. Harness traffic.
7. Method and caveats: policy, fallback choice, sample size and strata, not-covered spend, flagged spans, failed caveat checks.

## Test plan

Unit tests on synthetic fixtures, no network:
- Routing input: every text-part shape, plain strings, `developer` role, `<system-reminder>` blocks (complete, unclosed), trimming and joins, fallbacks, empty input, the 65,000-character limit.
- Score and tier: float order; `0.11000000000000001` is medium under balanced; exact t1 and t2 per profile.
- Bands: pools of 2 to 5 models, index ties, empty bands, pick-in-band tie-breaks, invalid pools, and a 2-model pool whose index order reverses the table order.
- Tier resolution: all regression ids above, plus disabled and unpriced models and the region tie-break.
- Pricing: each `when` variable and operator, `||`, the modulo off-peak form, partial overrides, overlapping variants (the US-region batch long-context case picks the three-condition variant), a span that shows nothing stays on base, unknown variables, missing tier variant, missing TTL, `service_tier` `default`.
- Savings: pick equals current model; negative savings; same tokens on the picked model; a model with no pool takes its switch figure in the router scenario, and the router setup plan lists it under `replacements` so following the plan yields the figure.
- Sampling and estimator: allocation with a minimum of 5, weights, weighted ratio on a hand-computed fixture, cell bootstrap, top-3-excluded figure, coverage.
- Caching: read and write per turn, TTL expiry, already-cached and already-written tokens, mixed-model threads.
- Span filter: root and `span.agent` dropped, leaf spans kept, cache hits and decision models dropped, already-routed spans kept with savings 0.
- Output: `report.json` validates against `resources/report.schema.json`; `render` refuses empty slots.
- Contract: the `Report` pydantic models in the script are the source of truth. `analyze` validates the report against them (strict) before writing `report.json`, so a break fails the run instead of a caller. `resources/report.schema.json` is generated, never hand-edited; after changing a model, regenerate it from the skill folder with `uv run scripts/cost_savings_report.py schema > resources/report.schema.json`. A test fails when the file and the models differ.

`tests/requirements.txt` lists `pytest` and `jsonschema` so CI runs the suite; `tests/test_analyze.py` runs `analyze` and `render` end to end against a fake `orq` CLI. Live smoke test (manual): `analyze --sample 20 --counterfactual-test` on an internal workspace whose decision model is enabled; pilot checks pass and the JSON validates.

## Open questions

1. Agent-level numbers have only been checked on a workspace with little agent traffic.

## Report display

- Hero: the lever saving more per month carries a "Recommended" tag (Smart Router on ties, unless no router can be set up from the enabled, scored models: then change model, and the gap line says why). When neither lever saves anything (`per_month` <= 0 or null), `summary.recommended` is null: no tag, no spend bar, and the plan caption says no saving was found. A header note says when the window was capped at retention.
- Spend bar and per-model bars: "Possible now" is the saving with models already enabled, "After enabling" the extra once the listed models are enabled. Neither is money already saved. Row text says "Now: ..." for the move available before enabling.
- Actions: `keep` means no cheaper model is as strong; `not_analysed` means the lever's estimate does not cover the model (pricing check failed, no billed cost, or a target has no price for the span's tier). Both are footnotes, not bars.
- Coverage is stored per lever (`summary.router.coverage`, `summary.switch.coverage`, `scopes[].router|switch.coverage`): the levers can leave out different models when a target has no price. The page quotes the recommended lever's coverage; the scope grid shows each lever's when they differ anywhere.
- Scopes keep models to enable per lever: the router needs every disabled pool member and switch target, change model only the switch targets.
- Setup: the Smart Router plan has `routers` and `replacements` (models with no pool, served on their switch target). Its card, prompt and commands create the routers, then change those models.
- `render` reads only `summary` plus the run header (`workspace`, `generated_at`): the pool tables (`scopes[].models`), the moves table (`sampled_moves`) and the caching candidates (`caching.rows`) are in `summary` too.
- What to do: a model with 1 to `MIN_SAMPLE` - 1 (14) sampled calls gets "Small sample: N calls." N is the sum of `routing.moves[].n` where `used` is the model. A model with no recorded moves (N = 0) gets no note and `small_sample: false`: zero is a missing count, not a small one.
- What to do diagram: shares are by sample weight (`routing.moves[].weight`, else `n`) over drawn targets only; unscored picks get no tier and show "+N unscored"; "enable" marks only drawn targets other than the model itself. Possible-now amounts come from the enabled scenario's `per_stratum` (kept in every scenario's router/switch estimate); old reports fall back to the reconciled split.
- Call counts include fallback samples (asks with no routing decision) in every table; the moves table shows "N (incl. M fallback)". `underpowered[].n` counts all samples, `n_scored` only the scored ones.
- `schema_version` is `1.2`: 1.1 dropped the quality profile; 1.2 adds `summary`, every number the page shows (headline, per-lever plan, per-model recommendations for both levers, scopes, spend not analysed). `analyze` computes it; `render` only formats it.
