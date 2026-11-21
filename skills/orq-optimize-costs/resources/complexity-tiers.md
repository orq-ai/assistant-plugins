# Complexity tiers — how the Smart Router sorts a request, and how to reproduce it

The Smart Router does not use a rubric, a token count or a keyword list. It scores each request with a learned prompt-difficulty model and maps the score to one of three tiers. This file states what that model reads, how the tier becomes a model, and the three ways this skill obtains a tier for a trace. Read it before Phase 4.

## What the Smart Router reads

The **routing prompt** is one string, built per request:

1. The newest `user` turn that carries human text. Turns holding only tool output are skipped.
2. Complete `<system-reminder>…</system-reminder>` blocks are removed from that text; the text around them is kept.
3. The system prompt and `instructions` are **excluded**. They are used only when the request has no user text at all.

So two requests with the same last user message route the same way, whatever their system prompt, history or tools. A coding agent's long system prompt does not make its requests "hard".

## From prompt to model

| Step | Rule |
|---|---|
| Score | A learned prompt-difficulty model scores the routing prompt. |
| Mode | The mode shifts the score before it is cut: **cost** by about half a tier toward easy, **quality** by about half a tier toward hard, **balanced** not at all. |
| Tier | The shifted score is cut into `easy`, `medium` or `hard`. The tier recorded on a trace is this post-shift tier. |
| Bands | The pool is ranked by intelligence index, strongest first (ties by model id). The weaker half, rounded down, is the **easy** band; the rest splits into **medium** (the larger half, rounded up) and **hard** (the strongest). |
| Band choice | The tier's band when it holds a model; otherwise the nearest stronger band that does; otherwise the strongest band. |
| Pick | **cost** and **balanced**: the cheapest model in the band by blended price, ties to the higher index, then the id. **quality**: the highest index in the band, ties to the cheaper, then the id. |
| Fallback | When routing is unavailable the request goes to the strongest model in the pool. |

Band sizes by pool size (hard / medium / easy):

| Pool | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|
| Bands | 0 / 1 / 1 | 1 / 1 / 1 | 1 / 1 / 2 | 1 / 2 / 2 | 1 / 2 / 3 | 2 / 2 / 3 | 2 / 2 / 4 |

With two models there is no hard band: hard requests go to the medium band, which is the stronger model. `scripts/estimate_savings.py` implements the band, band-choice and pick rows exactly; it cannot reproduce the score.

The intelligence index and the blended price are the catalogue's `metadata.autorouter.v2.intelligence_index` and `metadata.autorouter.v2.price` (USD per 1M tokens, `(3 × input + output) ÷ 4`). A model without an index cannot join a pool.

## Three ways to get a tier, best first

Record which one you used as `source` in `tiers.json`. Never mix sources in one file.

### 1. `smart_router` — traffic that already ran through a Smart Router

Exact. The router records its decision on the `span.auto_router` span of the trace: `orq.auto_router.tier`, `orq.auto_router.selected_band`, `orq.auto_router.selected_model`, `orq.auto_router.profile`, `orq.auto_router.models` and `orq.auto_router.fallback_used`.

These attributes are **not in the trace field registry** (`orq traces list-fields`), so they cannot be filtered or grouped. Read them per trace:

```bash
orq traces list-spans <trace-id> -o json -j "data[?type=='span.auto_router'].span_id"
orq traces get-span <trace-id> <span-id> -o json \
  -j '{tier:span.attributes.orq.auto_router.tier,band:span.attributes.orq.auto_router.selected_band,model:span.attributes.orq.auto_router.selected_model,profile:span.attributes.orq.auto_router.profile,fallback:span.attributes.orq.auto_router.fallback_used}'
```

[unverified: the probe workspace had no Smart Router traffic on 2026-11-21; the span type and attribute names are the ones the router sets. If the projection returns nulls, read the span unprojected once with `-j 'keys(span.attributes.orq)'` and correct this file.]

`profile` in `tiers.json` is the router's mode. A tier read here already includes that mode's shift.

### 2. `replay` — the real classifier on traffic that did not use a Smart Router

Exact for the mode you replay in, and it costs money and resends user text. Because the tier depends only on the routing prompt and the mode, never on the pool, a replay through **any** Smart Router gives the tier production traffic would get.

Run it only after `AskUserQuestion` approval that states: the number of prompts, that they are resent to the pool's providers, the projected cost, and that a temporary router is created in the workspace (Smart Routers are workspace-wide) and deleted afterwards.

1. Pick the two cheapest eligible models (enabled chat, with an index and a non-zero price) so the replay itself is cheap.
2. Create the router (not pre-approved; prompts):

   ```bash
   orq smart-routers create --key cost-probe-<yyyymmdd> --body-profile SMART_ROUTER_PROFILE_<COST|BALANCED|QUALITY> \
     --models <cheapest> --models <second-cheapest> -o json -j 'smart_router.{id:smart_router_id,ref:model_ref}'
   ```

3. For each sampled routing prompt (built as above), send it as the whole input with a small output cap, and keep the trace id:

   ```bash
   orq responses create --model "<model_ref>" --input "<routing prompt>" --max-output-tokens 16 -o json -j 'telemetry.trace_id'
   ```

   [unverified: replay was not run in the probe workspace. If `telemetry.trace_id` is empty, find the trace with `orq traces search` in the replay window. Some models reject a small `max_output_tokens`; raise it rather than drop the cap.]

4. Read each tier as in source 1.
5. Delete the router: `orq smart-routers delete <smart_router_id>`. **Done when** `orq smart-routers list -o json -j 'data[].key'` no longer lists it.

### 3. `approximate` — label a sample by hand

No spend, no resend, and **not the Smart Router's classifier**. Say so wherever an approximate tier is shown.

Read the routing prompt of each sampled trace with `orq traces conversation <trace-id> -o json`, take the newest user message with human text, strip `<system-reminder>` blocks, and label it from that text alone:

| Tier | The ask is… |
|---|---|
| `easy` | one step with a short or formulaic answer: a greeting, acknowledgement, lookup, rewrite, translation, classification, extraction, reformatting, a yes/no |
| `medium` | several routine steps: summarising a long input, writing standard code or copy, comparing a few options, following a handful of explicit constraints |
| `hard` | open reasoning: debugging, design or architecture, multi-step maths or logic, planning under many interacting constraints, an ambiguous request that needs judgement |

**When unsure between two tiers, choose the harder one.** That keeps the estimate conservative.

Approximate tiers carry **no mode shift**: they describe balanced mode. The script warns when a scenario uses another mode with them.

Sample size: label every trace when a target model has 100 or fewer in the window; otherwise a random 100 per target model, stratified by agent or deployment when the model serves several. Report each tier's share with its 95% Wilson interval and the sample size, e.g. `easy 46% (37–56%, n=100)`.

For a window too large to sample, `orq-analyze-traces`' `scripts/decide.py` can label every trace with a `choice` question whose `criteria` are the three rows above and whose instructions say to judge only the last user message. Follow that skill's spend gate and its `agreement` step against your hand labels; its tiers are still `approximate`.
