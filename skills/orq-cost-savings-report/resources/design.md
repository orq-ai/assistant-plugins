# orq-cost-savings-report: design

Spec for `scripts/cost_savings_report.py`, which is not built yet. Statements marked **V** were checked against the live orq API and `orq` CLI; everything else is the intended behaviour of the script.

## Scope

| Decision | Choice |
|---|---|
| Who runs it | The customer, on their own workspace |
| Form | The script does all arithmetic and writes `report.json`; the agent writes the narrative; `render` merges both into HTML |
| Window | `to` = run start; `from` = `to` - min(29 days, workspace retention - 1 day) |
| Sample | `--sample N` leaf LLM spans, default and v1 maximum 100 |
| Excluded from the sample | `span.cache_retrieval` (response cache hits), embedding models, decision models (`typesafe/*`, `orq/decider-*`) |
| Routing policy | The Smart Router's `jev-general-v1` decision, reproduced client-side and unchanged |
| Profile | Balanced headline; cost and quality as scenario columns |
| Pool | Same provider and host the customer uses; hardcoded tier table; only models enabled in the workspace |
| Transport | The script shells out to the `orq` CLI with `-o json`, so it reuses the user's CLI login and workspace |

## Layout

```
skills/orq-cost-savings-report/
  SKILL.md
  resources/design.md            this file
  resources/report.schema.json   output contract (with the script)
  scripts/cost_savings_report.py PEP 723 inline deps, including evaluatorq
  tests/requirements.txt         makes CI run the suite
  tests/test_*.py                load the script with importlib.util.spec_from_file_location
  tests/fixtures/                synthetic spans, decisions and catalog entries; never customer data
```

Commands:

```
uv run <skill dir>/scripts/cost_savings_report.py analyze --workspace <key> --out <dir> [--sample 100] [--counterfactual-test]
uv run <skill dir>/scripts/cost_savings_report.py render --out <dir> --narrative <dir>/narrative.md [--redact]
```

`analyze` writes `report.json` and a `narrative-slots.md` listing the slots the agent fills (summary, routing, caching, caveats). `render` refuses to run if a slot is empty.

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
| `orq models list -o json` | a bare array, not `.data` |
| Metric and dimension names | `orq telemetry list-capabilities --source TELEMETRY_SOURCE_TRACES -o json` |

Every telemetry and trace command needs RFC 3339 `--from` and `--to`; without them it returns 400 `from: value is required` (V). A window longer than retention returns 400 `time range exceeds workspace retention`; trace search past retention returns `[]` without an error (V). Read retention from that error or from workspace settings; stop if a stratum's trace search is empty while telemetry shows requests for it.

## Token accounting (V)

`prompt_tokens` includes cached and cache-write tokens. Uncached input = `prompt_tokens` - `cached_tokens` - `cache_creation_tokens`, and `input_cost` bills exactly that. `cached_tokens_cost` and `cache_read_cost` hold the same value. Gemini reports cache reads only.

## Routing counterfactual

### 1. Routing input

From the span's input messages, take the newest user turn that still has text after cleaning:
- Join the turn's text parts with a single space.
- Remove complete `<system-reminder>...</system-reminder>` blocks; an unclosed tag stays.
- Trim each remaining piece and join the non-empty ones with a single space.

With no user text, fall back to the system (or `developer`) prompt, then `instructions`. If that is empty too, count a fallback (`empty_input`). Over 65,000 characters: no decision, `prompt_limit`.

### 2. Score

```
orq decisions create --from-file body.json -o json
```

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
| quality | 0.02 | 0.09 | |

The question text and thresholds are one frozen policy (`jev-general-v1`); changing either makes it a different policy.

### 4. Band and model

Pool validity: every member has `autorouter.v2` metadata with a nonzero `intelligence_index`, and a pool has at least 2 members; otherwise "no routing", listed.

The tier table decides only which models are in the pool. Band position and "stronger" always come from the live index. Sort strongest first, ties by model id. `easy` gets the weakest `len/2` models; the rest split between `medium` and `hard`, hard getting the smaller half. With 3 models that is one per band; with 2, `hard` is empty and resolves to `medium`. An empty target band uses the nearest stronger band, else the strongest present. Inside a band, balanced and cost pick the lowest `autorouter.v2.price`, then the highest index, then model id; quality picks the highest index.

### 5. Fallback

Any decision failure (`decision_timeout`, error, `prompt_limit`, `empty_input`) serves the default model. The counterfactual uses the customer's current model as that default, so a failure counts as "no change". The real router uses the first model in its pool config; the report states this choice.

### 6. Savings per span

Savings = price(span, current model) - price(span, picked model), both from the pricing function on the span's token breakdown (uncached input, cache read, cache write, output). Negative savings are kept. A picked model without a cache-read price takes cached tokens at its input price. Output tokens are assumed similar.

Cache carry-over: a prompt cache belongs to one model. For each sampled span, fetch the previous span of its thread (thread search, then `list-spans`) and score its routing input too. If the previous turn's pick differs from this turn's pick, this turn's cache-read tokens are priced as a cache write on the picked model (uncached input if it has no cache-write price). If the previous span cannot be found, assume it differs whenever this turn's pick is not the current model. The report counts both cases. Savings are therefore exactly 0 only when the pick and the previous turn's pick both equal the current model.

Session-affinity scenario column: pin the thread's first-turn pick for 600 s; an escalation applies from the next turn; hard pins never escalate; at most one re-score per pin TTL.

### 7. Pricing function

Model catalog prices carry `variants[].when`. The evaluator implements the full grammar found in the catalog (V): variables `request_unix`, `input_tokens`, `tier`, `inference_geo`, `speed`, `operation`; operators `==`, `!=`, `>`, `>=`, `<`, `<=`, `&&`, `||`, `/`, `%` and parentheses. Example: an off-peak rule on two DeepSeek models of the form `((request_unix / N + N) % N < N) && (...)`.

| Variable | From the span |
|---|---|
| `request_unix` | `started_at` |
| `input_tokens` | `gen_ai.usage.prompt_tokens` |
| `tier` | `cost.pricing_tier` or `openai.response.service_tier`; `default` and `auto` mean `standard` |
| `inference_geo` | host region from the model id: `us.`, `us-east-1.` and similar map to `us`, `eu.` to `eu`, `global.` to none |
| `speed`, `operation` | span attribute if present, else the variant does not match |

Rules, to confirm against the server-side evaluator before building:
- A variant overrides the base price key by key (many variants override only some keys, V).
- When several variants match, the one with the most conditions wins; ties go to the first listed.
- An unknown variable makes the variant not match, and the span is flagged.
- If the picked model has no variant for the span's tier (for example `priority`), the span is flagged and its stratum counts as not covered, so standard pricing never inflates savings.
- Cache writes use the span's own TTL; if the model lacks it, the shortest TTL it has.

Check: price each sampled span at its own model and compare with `.span.summary.cost`, per model. A model more than 2% off is left out of savings (not covered) and listed with the gap.

### 8. From sample to total

Strata are models (`provider/model`), not project and model: a workspace can have hundreds of (project, model) pairs, most with negligible spend (V).

Allocation of N spans:
1. Rank models by `genai.cost` in the window.
2. Take models in order while each can get at least 5 spans under spend-proportional allocation; that gives at most N / 5 strata (20 at N = 100).
3. Models not taken count as not covered.

Selection inside a stratum:
1. Telemetry trace count per (project, hour) for that model.
2. Draw (project, hour) cells with probability proportional to trace count.
3. In a drawn cell, pick one trace uniformly: run `traces search` on a random 1-minute sub-window inside the hour, widening until a page has traces, and choose one at random from it.
4. In the trace, pick one leaf LLM span of that model uniformly: `type` in {`span.chat_completion`, `span.responses`}, `has_children == false`. The trace root and `span.agent` carry their children's cost.
5. Weight w_i = (leaf spans of that model in the trace), so each span's inclusion probability is equalized.

Estimator per stratum s:

```
rate_s    = sum_i w_i * savings_i / sum_i w_i * price(span_i, current model)
savings_s = rate_s * telemetry genai.cost_s
headline  = sum_s savings_s
```

- Fallbacks, `prompt_limit` and spans already routed by a Smart Router (`auto_router.*` attributes) stay in the sample with savings 0.
- 90% CI: bootstrap 1,000 times, resampling drawn cells within each stratum. Report the cluster count per stratum, and next to the CI a "without the top 3 spans" figure, since a few long-context spans can carry most of the saving.
- Coverage: share of window spend in strata that were sampled and passed the pricing check.
- Per month: headline x 30 / window days.

The headline is always shown with its CI and coverage. At N = 100 the interval is wide; the report says so.

### 9. Buckets

Savings are also shown by ask length (characters / 4: 0-128, 128-512, 512-1024, over 1024 tokens) and by span `prompt_tokens` (0-8k, 8k-32k, over 32k). All buckets count in the headline. Flagged:
- Asks over 1024 tokens: the decision model is least reliable on long asks.
- Prompts over 32k tokens: the router scores only the ask, so a short ask on a long context rates easy even though the picked model still reads the whole context.

### 10. `--counterfactual-test`

Runs routing on the pilot asks and stops. Per ask: recommended model, model used, whether they differ. Summary: deviation rate per used model, split by blended price into "recommends cheaper" and "recommends pricier". Nothing is replayed.

### Decision cost

`typesafe/jev-latest` costs $0.042 per 1M input tokens and nothing for output (V, `orq models list`). Identical routing inputs are scored once. Estimate after building the inputs: unique inputs x (characters / 4 + 330) tokens x $0.042 / 1M. Previous turns fetched for carry-over add up to N more calls.

## Runtime

Single-call timings (V): `traces search` page 2.7 s, `list-spans` 0.57 s, `get-span` 0.47 s, thread search 1.4 s. Per sampled span: about 3 searches for the sub-window, `list-spans`, `get-span`, the previous-turn lookup and 1-2 decision calls, roughly 10-15 s sequential. At N = 100 with 8 workers that is a few minutes. The script prints its estimate next to the classify cost before starting. Every response is cached on disk by trace and span id so a rerun resumes; 429 and 5xx retry with exponential backoff.

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

Join `orq models list` to the catalog on `provider + "/" + model_id`; its `id` is a UUID. The catalog's own `artificial_analysis_intelligence_index` disagrees with the router's index and is not used. A project-scoped API key hides disabled models, which only empties the "enable to compare" list.

### Tier table (hardcoded v1)

Each cell is a regex on the normalized model name.

| Provider | Frontier | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|---|
| OpenAI | `^gpt-[0-9.]+-astra$` | `^gpt-[0-9.]+-sol$` | `^gpt-[0-9.]+-terra$` | `^gpt-[0-9.]+-luna$` |
| Anthropic | `^claude-fable(-[0-9]+)*$` | `^claude-opus(-[0-9]+)*$` | `^claude-sonnet(-[0-9]+)*$` | `^claude-haiku(-[0-9]+)*$` |
| Google | `^gemini-4-neon(-preview.*)?$` | `^gemini-4-carbon(-preview.*)?$` | `^gemini-4-hydrogen(-preview.*)?$` | `^gemini-3\.8-flash(-preview.*)?$` |
| zai | | `^glm-[0-9.]+$` | `^glm-[0-9.]+-flash$` | |
| DeepSeek | | `^deepseek-v[0-9.]+-pro(-[0-9]+)?$` | `^deepseek(-v[0-9.]+)?-flash(-[0-9]+)?$` | |

Normalizing a model id:
1. Drop the provider (`baseten/`, `aws/`, `google/`).
2. Drop a region prefix (`eu.`, `us.`, `global.`, `us-east-1.` and similar), then a vendor prefix (`anthropic.`, `openai.`).
3. Drop version suffixes: `@<date>`, `-v<n>:<n>`, `-global`, and dated snapshots `-YYYY-MM-DD`.

Regression cases: `anthropic/claude-opus-5-5` → opus; `openai/gpt-6.2-sol` → sol; `baseten/deepseek-v4.1-flash-1017` → DeepSeek flash; `google/claude-opus-4-5@20251101` → opus; `aws/eu.anthropic.claude-haiku-5-5` → haiku; `openai/gpt-6-luna-2026-08-01` → luna.

Resolution per cell, on the customer's host: keep models that are enabled, priced and valid for a pool; take the newest by `created`, preferring no region prefix, then the lowest price. No match leaves the cell empty. Newer disabled models in the lineage are listed as "enable to compare", never recommended.

### Pool rule

- Customer on frontier: pool is frontier, tier 1, tier 2.
- Customer on tier 1: pool is tier 1, 2, 3.
- Two-tier provider on the stronger table tier: pool is both tiers.
- Customer on tier 2 or below: no downgrade analysis. The under-powered section reports the share of asks rated `hard` under balanced and the extra cost of sending them to the next model above the current one by live index in the same lineage; with none, the share only.
- Providers not in the table: a "not covered" row with their spend.
- Host: map the customer's model to its canonical id with `offering_of`, then take tier models on the same host; first-party when the customer uses first-party.

Table tier and index order can disagree. Example (V): deepseek-flash has a higher index and a lower price than deepseek-v4-pro. A v4-pro customer's 2-model pool then puts v4-pro in `easy` and flash in `medium` (hard resolves to medium), so easy asks stay on v4-pro and the rest move to flash. A flash customer gets the under-powered section with no stronger model, so the share only. The Pareto line flags v4-pro as dominated.

### Pareto check

One line per model: does any enabled model have an index at least as high and a band price at most as high, one strictly better. Uses evaluatorq's `model_frontier` helpers (`pareto_frontier`, `in_family_upgrades`, `load_garden`); `load_garden` returns every model, so build its client for the same workspace and filter to enabled models. States the fact only.

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

Candidate: 3 or more turns, a model with a cache-read price, a prefix of at least 1,024 tokens, and cache-read ratio (`cached / prompt_tokens`) below 20%.

Per turn k from 2 to n:
- alive_k: the gap since turn k-1 is within the cache TTL.
- read_k = alive_k ? prompt_tokens(k-1) : 0.
- write_k = prompt_tokens(k) - read_k.

Savings = sum_k max(0, read_k - cached_k) x (input - cache read) - sum_k max(0, write_k - actual_write_k) x (cache write - input), with prices from the pricing function at the current model. No cache-write price makes the second term 0.

The rule assumes conversations append. For providers with automatic prefix caching (OpenAI), a low read ratio often means the prefix is not stable, so the OpenAI figure is an upper bound and labelled so.

Action by provider:
- Explicit caching (Anthropic-style): set `cache_control` on the agent's model parameters or the deployment variant. orq forwards breakpoints but does not add them.
- Automatic caching (OpenAI): keep the prefix stable, with the static system prompt and tools first.
- Reads-only reporting (Gemini): listed without a savings figure.

Only agents and deployments on orq count toward caching savings.

### Routing and caching together

Routing savings are scaled to the window; caching savings cover the scanned threads only. They overlap, so the report shows them side by side and never adds them.

## Harness traffic

The gateway records the calling harness as `trace_framework`, groupable on the `orq.gen_ai.*` metrics but not on `genai.cost` (V). `orq.gen_ai.cost` can run below `genai.cost`, so the section names its metric and shows an "unattributed" row to the summary total. The field also carries SDK names; the report lists every value and marks known coding harnesses as "caching not controllable".

## Report sections

1. Summary: spend in the window and per month; routing savings with 90% CI, the top-3-excluded figure and coverage; caching savings beside it; Pareto line.
2. Spend by model, agent, deployment.
3. Routing: balanced headline; cost, quality and session-affinity columns; buckets with the flags above. Action: "create a Smart Router with profile balanced and pool X, Y, Z".
4. Under-powered asks, up to 3 truncated examples; `--redact` hides them.
5. Caching gaps by agent and thread, with the action per provider.
6. Harness traffic.
7. Method and caveats: policy, fallback choice, sample size and strata, not-covered spend, flagged spans, failed caveat checks.

## Test plan

Unit tests on synthetic fixtures, no network:
- Routing input: every text-part shape, plain strings, `developer` role, `<system-reminder>` blocks (complete, unclosed), trimming and joins, fallbacks, empty input, the 65,000-character limit.
- Score and tier: float order; `0.11000000000000001` is medium under balanced; exact t1 and t2 per profile.
- Bands: pools of 2 to 5 models, index ties, empty bands, pick-in-band tie-breaks, invalid pools, and a 2-model pool whose index order reverses the table order.
- Tier resolution: all regression ids above, plus disabled and unpriced models and the region tie-break.
- Pricing: each `when` variable and operator, `||`, the modulo off-peak form, partial overrides, overlapping variants, unknown variables, missing tier variant, missing TTL, `service_tier` `default`.
- Savings: pick equals current model; negative savings; carry-over on a switch, on a switch back, with the previous turn missing, and with no cache-write price; session-affinity pins and escalation.
- Sampling and estimator: allocation with a minimum of 5, weights, weighted ratio on a hand-computed fixture, cell bootstrap, top-3-excluded figure, coverage.
- Caching: read and write per turn, TTL expiry, already-cached and already-written tokens, mixed-model threads.
- Span filter: root and `span.agent` dropped, leaf spans kept, cache hits and decision models dropped, already-routed spans kept with savings 0.
- Output: `report.json` validates against `resources/report.schema.json`; `render` refuses empty slots.

`tests/requirements.txt` lists `pytest` and `jsonschema` so CI runs the suite. Live smoke test (manual): `analyze --sample 20 --counterfactual-test` on an internal demo workspace; pilot checks pass and the JSON validates.

## Open questions

1. The pricing-variant precedence rules above must be confirmed against the server-side evaluator before building.
2. Agent-level numbers have only been checked on a workspace with little agent traffic.
