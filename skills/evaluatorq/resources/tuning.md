# Tuning: reasoning effort, token budgets, parallelism, timeouts

Probed against Python `evaluatorq` 1.39.0, 2026-09-20. Upstream reference (re-probe against it when evaluatorq releases): [tuning](https://orq-ai.github.io/evaluatorq/tuning/).

Every knob here has a working default. Reach for one when a run is slow, flaky, truncated, or spending more than you want.

The knobs split three ways, and confusing them is the usual reason a setting "does nothing":

| Group | Governs | Lives on |
|---|---|---|
| Target calls | The agent **under test** | `LLMConfig` (red team) / `simulate()` kwargs |
| Pipeline calls | evaluatorq's **own** LLMs — judge, attacker, user simulator, generators | `LLMCallConfig` / `EvaluatorConfig` / `EVALUATORQ_*` env vars |
| Provider options | Anything the provider accepts that evaluatorq does not model | `extra_kwargs` / `extra_body` |

## Reasoning effort: four different knobs

Four settings carry the words "reasoning effort" and apply to four different models. Setting the wrong one is **silent** — the call just runs at the default.

**Never hardcode an effort value from memory.** The accepted ladder is the model's, not evaluatorq's, and it changes per model and per release. Read it from the catalogue before setting one:

```python
from evaluatorq.common.model_catalogue import get_model_info, validate_reasoning_effort

info = await get_model_info("<model-id>")
print(info and info.reasoning_efforts)        # None = catalogue does not say, never "none allowed"

await validate_reasoning_effort(EFFORT, "<model-id>")   # raises only when the catalogue contradicts you
```

`validate_reasoning_effort()` fails the run before generation is paid for, but only when the catalogue actually lists the values — an unlisted model (every `agent/<key>` id) logs a warning and leaves the provider as the authority.

| You want to change | Use | Applies to |
|---|---|---|
| The **jury / judge** in core evaluation | `reasoning_effort=` on `llm_jury()`, `llm_jury_pairwise()`, `PairwiseComparator` | The verdict calls under `evaluatorq()` |
| The **target agent** under test | `--target-reasoning-effort`, `LLMConfig(target_reasoning_effort=...)`, `simulate(target_reasoning_effort=...)` | The agent being red-teamed or simulated |
| The **attacker or judge** in red teaming | `LLMCallConfig(reasoning_effort=...)` on `attacker=`, `EvaluatorConfig(reasoning_effort=...)` (an `LLMCallConfig` subclass) on `evaluator=` | Red-team pipeline calls |
| The **user simulator and judge** in simulation | `EVALUATORQ_REASONING_EFFORT`, or `llm_config=LLMCallConfig(reasoning_effort=...)` on `simulate()` / `generate_and_simulate()` | Simulation-side calls |

```python
from evaluatorq import llm_jury

jury = llm_jury(
    name="helpfulness",
    criteria="Is the answer helpful and correct?",
    judges=["<model-a>", "<model-b>"],
    reasoning_effort=EFFORT,          # from the catalogue — see above
)
```

```python
from evaluatorq.contracts import LLMCallConfig
from evaluatorq.redteam import EvaluatorConfig, LLMConfig, red_team

await red_team(
    target="agent:my-agent",
    llm_config=LLMConfig(
        attacker=LLMCallConfig(model="<model-id>", reasoning_effort=ATTACKER_EFFORT),
        evaluator=EvaluatorConfig(model="<model-id>", reasoning_effort=EVALUATOR_EFFORT),
        target_reasoning_effort=TARGET_EFFORT,
    ),
)
```

**Accepted values are the provider's call, not evaluatorq's.** The value is forwarded verbatim. An unsupported one comes back as a 400, which the executors turn into drop-the-reasoning-block-and-retry-once — the call succeeds *without* reasoning rather than failing, and the rejection is remembered for the rest of the process, so later calls to that model skip the block up front. A silently effort-less run is the failure mode to watch for.

Because jury judges send to the `responses` endpoint, effort renders as a `reasoning` block; on the Chat Completions fallback it renders as a flat `reasoning_effort` field. `LLMCallConfig.request_params()` does that rendering per endpoint, and also picks `max_output_tokens` vs `max_completion_tokens`.

**Reasoning effort reaches a target only on Responses-capable targets** (`agent:<key>` on the orq router). A `deployment:` target goes through the SDK agents endpoint, which has no reasoning parameter; a callable or Vercel endpoint has nowhere to put it. In each case the setting is accepted, a warning names the drop, and the run proceeds.

`EVALUATORQ_REASONING_EFFORT` is **unset by default, and unset means the parameter is not sent at all** — the model applies its own default. There is deliberately no global effort: sending one costs a rejected request plus a retry on every model that does not accept it, and overrides the tuned default on every model that does. It is read at call time (so setting it after import works) and is only a fallback — an explicit `LLMCallConfig.reasoning_effort` wins, including an explicit `None`, which opts that role out on purpose. Set it to `""`, `none`, or `off` to omit the parameter.

**A model the catalogue does not list** (self-hosted, or newer than your workspace catalogue) degrades silently three ways: unpriced calls, Chat Completions instead of Responses, and no pre-validation of effort. The catalogue is orq's `GET /v2/models`, fetched once per process. Fix a missing entry with `register_model()`:

```python
from evaluatorq.common.model_catalogue import ModelInfo, register_model

register_model("my-self-hosted-llama", ModelInfo(
    input_cost_per_1k=0.0002, output_cost_per_1k=0.0008, provider="self",
    supports_responses=False, reasoning_efforts=None,   # None = "unknown", never "nothing allowed"
))
```

Registered entries beat the fetched catalogue, so this also corrects a wrong entry. Costs are USD **per 1000 tokens**. The id is stored unprefixed, so `openai/gpt-x` and `gpt-x` are one entry.

## Token budget for reasoning models

A reasoning model spends completion tokens on thinking before it emits anything, so the usual symptom of too small a budget is an empty answer or a tool call that never arrives — not an error.

| Knob | Default | Covers |
|---|---|---|
| `llm_jury(max_tokens=...)` | `8000` | Each judge call |
| `LLMCallConfig.max_tokens` | `10000` (simulation/red-team roles) | One pipeline role |
| `EVALUATORQ_LLM_MAX_TOKENS` | `10000` | Simulation-side fallback only; `LLMCallConfig.max_tokens` wins |

`LLMCallConfig.max_tokens` reaches the user simulator, the judge and the executive summary; the generators and the recommendations pass size their own budget from the item count instead, and log when your value did not apply.

## Temperature

`LLMCallConfig.temperature` is **unset by default and unset means not sent**. Reasoning-class models reject `temperature` outright (`400 Unsupported parameter: 'temperature' is not supported with this model`), so sending *any* value, even a safe one, breaks them. No evaluatorq call site sends one of its own. `temperature=None` means "leave it unset", never "send null".

One simulation `llm_config` covers every simulation-side role, so lowering temperature to make scoring repeatable also flattens the variation the simulated user exists to produce. To configure one role only, build that agent yourself and pass it as `judge=` / `user_simulator=`; an injected agent carries its own settings and `simulate()` warns that `llm_config` will not reach it.

## Parallelism

```python
await evaluatorq("eval", data=[...], jobs=[...], datapoint_parallelism=10, llm_parallelism=20)
```

- `datapoint_parallelism` (default `10`, formerly `parallelism`, still accepted as a deprecated alias) counts **tasks**, and the bounds nest: at most N datapoints at once, and *within each one* a separate budget of the same size covers its jobs and then its evaluators. Ten datapoints × ten evaluators is a hundred concurrent tasks, not ten. Set `1` for fully sequential.
- `llm_parallelism` (default unbounded) counts **requests** across the whole run, so it holds however the fan-out nests. It is a concurrency bound, not a rate limit: ten slots against 10s calls is ~60 requests/minute, and ~300/minute if the provider speeds up to 2s.

Requests evaluatorq issues itself (judges, juries, simulation, red team) take a slot automatically. A job calling a provider SDK directly is invisible to the budget unless you wrap it — and wrap only the request, since holding a slot across parsing shrinks the budget without reducing load:

```python
from evaluatorq.common.llm_limit import llm_slot

async def my_job(data, row):
    async with llm_slot():
        response = await client.chat.completions.create(...)
    return {"name": "my-job", "output": response.choices[0].message.content}
```

`red_team()`, `simulate()`, `generate_and_simulate()` and `generate()` take `llm_parallelism` with the same meaning.

## Target-call reliability (red team / simulation)

| Knob | Default | Raise it when |
|---|---|---|
| `target_agent_timeout_ms` / `--target-timeout-ms` | `240000` (4 min) | One target call legitimately takes minutes |
| `max_target_retries` / `--max-target-retries` | `2` (0–10) | The target's transport is flaky. A retry never consumes an attacker turn or changes the transcript |
| `max_tool_continuations` | `5` | An orq agent needs more client-driven tool-result rounds to finish a turn |
| `per_simulation_timeout_s` (simulation, Python only) | `None` (unbounded) | A conversation can stall in a loop. On expiry it returns a partial result with `terminated_by="timeout"` rather than raising. `0` is rejected — `None` is the only spelling of unbounded |
| `max_tool_result_chars` | `500` | A tool-heavy agent whose results are cut before the simulator (and the judge scoring the same transcript) can react |

## Pipeline-call reliability

| Knob | Default | Covers |
|---|---|---|
| `LLMConfig.retry_count` / `--retry-count` | `3` (0–10) | Pipeline-owned LLM calls |
| `LLMConfig.retry_on_codes` | `[429, 500, 502, 503, 504]` | Which statuses retry |
| `EvaluatorConfig.retry_count` | `1` | Retries per judge call. `0` falls straight through to `replacement_judges` / `min_successful_judges` |
| `LLMCallConfig.timeout_ms` | `90000` | Per-call timeout for a pipeline role |

**One retry layer, including on a client you inject.** SDK retries and evaluatorq's own would multiply, so `common.judge` and `generate_structured` clone the client with `max_retries=0` first. The clone reuses your transport, auth, base URL, headers and timeout, and your object is never mutated — do not pass `max_retries=0` yourself, and do not expect SDK retries on an injected client to apply.

Two knobs govern cost rather than reliability, because each unit is a live call: `LLMConfig.max_objectives_per_llm_call` (default `8`, ~150 output tokens per objective) and `LLMConfig.max_probe_turns` (default `8`, each turn a live call against the target).

## Provider options: two seams, not interchangeable

```python
llm_jury(name="q", criteria="...", judges=["openai/gpt-5.6-luna"],
         extra_kwargs={"top_p": 0.9},          # top-level SDK argument — REPLACES the key
         extra_body={"cache": {"ttl": 60}})    # request body field — MERGED per key
```

`extra_kwargs` is merged last, so it overrides evaluatorq's computed value. Structural fields are reserved and raise `ValueError`: `model`, `messages` / `input`, `response_format` / `text`, and `extra_body`. `extra_body` must go through the dedicated parameter — passing it inside `extra_kwargs` raises at judge time, and since a judge failure is caught and turned into a verdict, the symptom is a judge that always fails rather than a crash.

Prefer the first-class fields over routing the same key through `extra_kwargs`: `LLMCallConfig(reasoning_effort=...)`, `(max_tokens=...)`, `(temperature=...)`.

The jury evaluators build their `LLMCallConfig` internally, which is why they take `extra_kwargs` / `extra_body` / `reasoning_effort` / `client` as explicit parameters.

## Environment variables worth knowing

| Variable | Default | Purpose |
|---|---|---|
| `ORQ_API_KEY` | — | Datasets, deployments, experiment replay, result upload, tracing, the model catalogue. Wins over `OPENAI_API_KEY` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | — | Standalone route (vLLM, OpenRouter, Azure, Ollama). `OPENAI_BASE_URL` is **not** honoured by `OrqResponsesTarget` or the simulation conversation path — inject a pre-built client there |
| `ORQ_BASE_URL` | `https://my.orq.ai` | orq API base, and the derived OTLP endpoint (`<base>/v2/otel`) |
| `EVALUATORQ_DIR` | `.evaluatorq` in cwd | Run store for red-team / sim reports and the dashboard. Must point at the store dir itself |
| `ORQ_DISABLE_TRACING` | off | `1` sends no spans. Tracing auto-enables with `ORQ_API_KEY` + the `otel` extra |
| `EVALUATORQ_CAPTURE_MESSAGE_CONTENT` | `true` | `false` strips prompts and responses from spans (PII); token counts and latency stay |
| `EQ_DEBUG` | unset | Full traceback on CLI errors instead of one line |

evaluatorq never reads a `.env` itself — load it yourself with `python-dotenv` **before** importing evaluatorq.
