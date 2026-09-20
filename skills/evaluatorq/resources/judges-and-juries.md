# Judges and juries

Upstream reference (re-probe against it when evaluatorq releases): [llm-as-a-jury](https://orq-ai.github.io/evaluatorq/llm-as-a-jury/), [jury-presets](https://orq-ai.github.io/evaluatorq/jury-presets/), [pairwise-judging](https://orq-ai.github.io/evaluatorq/pairwise-judging/).

Two LLM-graded evaluators ship with the library, both built on the same panel machinery:

| Helper | Question it answers | Verdict |
|---|---|---|
| `llm_jury()` | "Is this one response good?" | boolean / label / number, plus `pass_` |
| `llm_jury_pairwise()` → `PairwiseComparator` | "Is A better than B?" | `"A"` / `"B"` / `"tie"` / `"inconclusive"` |

A single judge is one model's opinion: noisy call to call, and biased toward outputs from its own provider family. A panel of 3–5 judges from **different** provider families cancels the part of that error that is not shared. Keep the count odd so ties are rare. A single-judge panel runs with no aggregation overhead, so a jury is purely additive cost, never additive complexity.

## Pointwise: `llm_jury()`

It returns a normal `Evaluator`, so it goes straight into `evaluators=[...]`:

```python
from evaluatorq import evaluatorq, llm_jury

correctness = llm_jury(
    name="correctness",
    criteria="The answer is factually correct and directly answers the question.",
    judges=[
        "anthropic/claude-sonnet-5",
        "google/gemini-3.6-flash",
        "deepseek/deepseek-v4-pro",
    ],
)

await evaluatorq("qa-eval", data=[...], jobs=[...], evaluators=[correctness])
```

`llm_jury(model="x")` is shorthand for `judges=["x"]` — the classic single judge. Two or more `judges` makes it a jury. Name neither and it uses the library's default pipeline model.

### Verdict modes

`verdict_kind` is **not** inferred from `labels` — pick the mode explicitly.

| Mode | Configure it with | Judge returns | `pass_` is |
|---|---|---|---|
| Boolean (default) | `verdict_kind="categorical"`, no `labels` | `true` / `false` | the boolean |
| Labeled | `verdict_kind="categorical"`, `labels=[...]` | one of `labels` | `verdict in passing_labels` (`None` if omitted) |
| Numeric | `verdict_kind="numeric"` | a float in `score_range` (default `(0.0, 1.0)`) | `score >= threshold` (default `0.5`) |

`labels` / `passing_labels` with `numeric` raises `ValueError`; `passing_labels` must be a subset of `labels`.

### Panel configuration

| Argument | Default | What it does |
|---|---|---|
| `preset` | `None` | A ready-made panel (see below). Mutually exclusive with `judges` / `model`. |
| `judges` / `model` | — | Judge model IDs / single-judge shorthand. |
| `repetitions` | `1` | Calls per judge. The judge takes its own majority (plurality, or mean/median) before the panel votes — smooths per-call noise. |
| `assignment` | `"all"` | `"all"` = every judge on every datapoint. `"cyclic"` = exactly one judge per datapoint, rotating. |
| `replacement_judges` | `None` | Stand-ins called only when a configured judge fails mechanically. |
| `min_successful_judges` | `None` | Decisive judges required, else **inconclusive**. `None` = 1 for a hand-listed panel, a majority of seats under a preset. A floor on how many answer, *not* the aggregator's threshold. |
| `aggregator` | plurality (categorical) / mean (numeric) | How votes combine. A preset sets `"majority"`. |
| `structured_output` | `True` | Provider structured-output API, with a schema-injected `json_object` fallback for models that reject it. |
| `reasoning_effort` | `None` | Effort on the **judge** model — see [tuning.md](tuning.md). |
| `max_tokens` / `timeout_ms` | `8000` / `90000` | Per judge call. |
| `extra_kwargs` / `extra_body` | `None` | Provider escape hatches — see [tuning.md](tuning.md). |
| `client` | `None` | Your own `AsyncOpenAI`. |

**Presets** seat a cross-family panel, its aggregation rule and a majority quorum in one argument:

```python
llm_jury(name="correctness", criteria="...", preset="Balanced Trio")
```

`"Balanced Trio"` (default choice, cheapest sensible panel), `"Strong Jury"` (≈5× the cost; for verdicts that outlive the run — customer-facing benchmarks, preference data you will train on), `"Open-Weight / Portable"`, `"EU Region"`, `"Single-Provider Trio"` (for one-vendor workspaces; three tiers of one provider correlate and cannot vote away self-preference). Inspect them with `from evaluatorq import PRESETS, get_preset`; `get_preset(n).seated_efforts()` reports the reasoning effort each seat was costed at. Presets are pointwise panels, so `assignment="cyclic"` with one is rejected.

**Cyclic assignment** gives each datapoint one judge, rotating through the panel: judge bias still cancels across the dataset, at single-judge cost. Use it for run-level numbers (a benchmark mean, a pass rate). Do **not** use it when an individual verdict must stand on its own — each per-item verdict is one judge's opinion, and `stats` / `raw_agreement` come back `None`.

**Do not build a panel out of `orq/*` routers.** A router picks a model per request, so three routers can return three cards from one vendor — which destroys the independence that is the only reason to poll three judges. A single router judge is fine, except in a run you intend to reproduce: a re-run may be scored by a different model.

### How a verdict is decided

1. Each judge votes (reducing its own `repetitions` to one vote first).
2. Each judge that failed *mechanically* pulls in one `replacement_judges` model.
3. The panel aggregates — plurality for categorical, mean or median for numeric.
4. Fewer than `min_successful_judges` decisive votes → **inconclusive**.

A judge may also **abstain**: a clean answer that declines to choose. Not a failure, triggers no replacement, excluded from the tally.

### What the judge sees

The default prompt renders criteria, input, output and expected output. Override it with `prompt=` using this namespace (Mustache-style, dotted paths):

| Variable | Contents |
|---|---|
| `{{criteria}}` | Your `criteria=` string |
| `{{input.all_messages}}` | The datapoint's inputs as a JSON message list |
| `{{input.expected_output}}` | `DataPoint.expected_output`, or `""` |
| `{{input.system_instructions}}` | System instructions, or `""` (pointwise jury does not set this) |
| `{{output.response}}` | The response text being judged |
| `{{output.messages}}` | Full output transcript (text, reasoning, tool-call turns) as JSON |
| `{{output.tools_called}}` | Tool calls made producing the output (name / arguments / result / id) as JSON |
| `{{output.error}}` | The target's error, or `""` |

**This is evaluatorq's namespace, not the orq platform's.** A platform evaluator's prompt editor renders a different, shorter set — `skills/orq-build-evaluator/resources/judge-prompt-template.md` §6 is canonical there. `{{criteria}}`, `{{output.messages}}` and `{{output.error}}` exist only here; reusing them in a platform prompt renders as literal text.

`{{log.*}}` aliases of the same values also resolve, but they are the legacy spelling — use `input.*` / `output.*`. Bare `{{input}}` / `{{output}}` no longer substitute anything: they reach the judge as literal text, with a warning logged.

Tool calls and the structured transcript are only reachable through a custom `prompt=` — the default template does not include them. That is the knob to reach for when judging *how* an agent got there rather than what it finally said.

### What comes back

`value` carries the aggregated verdict, `pass_` the gate, and `explanation` gets a one-line panel summary appended (`[jury: 3/3 judges, raw agreement 100%]`). The per-judge detail rides on `raw_output["jury"]` — validate it back into the typed model rather than indexing raw keys:

```python
from evaluatorq.contracts import JURY_RAW_OUTPUT_KEY, JuryResult

payload = (score.score.raw_output or {}).get(JURY_RAW_OUTPUT_KEY)   # None when no panel ran — see below
if payload:
    jury = JuryResult.model_validate(payload)
    print(jury.judges_succeeded, "/", jury.judges_configured, jury.raw_agreement, jury.stats)
    for vote in jury.votes:
        print(vote.model, vote.value, vote.explanation)
        for rep in vote.repetitions:       # populated when repetitions > 1
            print("   ", rep.value, rep.explanation)
```

Two states return no jury payload, and they are different:

- **The target errored** → `inconclusive`, `raw_output` is `None`, no panel ran. Scoring "the agent said nothing" as an answer would be wrong, so neither jury calls its judges. Use `.get(JURY_RAW_OUTPUT_KEY)` if your code walks every result.
- **The panel ran and reached no verdict** → `inconclusive`, and `raw_output` carries a second key `"evaluation_error"` (`EVAL_ERROR_RAW_OUTPUT_KEY`) naming the judge errors and how many failed. Written only when a judge actually failed — a panel that abstained cleanly, or split too evenly, produced a non-verdict rather than an outage.

`raw_output` is kept in local dumps and **stripped from the orq.ai upload**: judge internals stay on your machine.

## Pairwise: `llm_jury_pairwise()`

Use it when absolute grading is hard to calibrate but "which is better" is obvious, or when you are comparing two prompts / models / versions directly.

```python
from evaluatorq import build_report, llm_jury_pairwise

comparator = llm_jury_pairwise(
    criteria="The answer is accurate, complete, and directly addresses the question.",
    judges=["anthropic/claude-sonnet-5", "google/gemini-3.6-flash", "openai/gpt-5.6-luna"],
)

comparison = await comparator.compare(
    question="What is the capital of France?",
    response_a="The capital of France is Paris.",
    response_b="The capital of France is Berlin.",
)
print(comparison.winner)           # "A" | "B" | "tie" | "inconclusive"
report = build_report([comparison])
print(report.a_win_rate, report.inconclusive_rate)
```

**Swap and reconcile.** Every judge is run twice per pair — `(A, B)` and `(B, A)` — and the second is un-swapped back into the canonical frame. A judge that agrees with itself casts that vote; a judge that contradicts itself has no real preference, so it **abstains** and the flip is recorded as position bias. Both orderings run concurrently, so swapping costs calls, not wall clock. `swap=False` skips it and gives up the position-bias metric.

Panel arguments mirror `llm_jury()`, plus `max_concurrency` (cap on in-flight judge calls across concurrent `compare()` calls; each pair fans out judges × orderings × repetitions).

Reading a comparison:

```python
for vote in comparison.votes:
    vote.model, vote.vote, vote.flipped, vote.completed, vote.replacement, vote.explanation
comparison.token_usage   # summed across both orderings and any replacements
```

Rolling up with `build_report(comparisons)`: `a_win_rate` / `b_win_rate` (over comparisons decided A or B), `tie_rate`, `inconclusive_rate`, `mean_agreement`, and `per_judge` entries with `a_rate`, `b_rate`, `position_bias`, `tie_rate`, `consistency` / `consistency_raw`. **Read the win rates next to `inconclusive_rate`** — a mostly-noise run can still show a flattering `a_win_rate`, because the rates are computed over decided comparisons only.

`build_report(comparisons, aggregation="bt-sigma")` fits a Bradley-Terry model with a per-judge discriminator over the run's own votes — unsupervised, no extra LLM calls — and down-weights judges that are internally inconsistent. Check `report.bt_sigma.converged` and `fit_warnings` before trusting the sigmas; a capped fit still produces numbers. With `repetitions >= 2` and `swap=True` the weights come from measured self-agreement instead of the two-item fit. It protects against noisy judges, not against a majority sharing the same systematic bias.

To keep a pairwise run and view it in `eq dashboard`:

```python
from evaluatorq.pairwise_run import new_run

run = new_run(run_name="prompt-v2 vs prompt-v3", label_a="prompt-v2", label_b="prompt-v3",
              judges=[...], criteria="...")
run.add(comparison, question=q, response_a=a, response_b=b)
run.save()   # -> .evaluatorq/pairwise-runs/<timestamp>_<name>.json
```

Set `label_a` / `label_b`: nothing in the judging data records what was in each slot, so `"A won"` is unreadable without them.

For ranking more than two candidates, use `evaluatorq.ranking.fit_bt()` with `cycle_rate()` as its consistency diagnostic. `run_pairwise()` is the ordering-independent engine underneath the comparator — reach for it to drive swap-and-reconcile with a non-LLM judge.

## Reliability

- `raw_agreement` — how lopsided one vote was.
- `report.summary.jury_reliability.krippendorff_alpha` (red-team runs) — whether judges agree more than chance. `1.0` perfect, `~0` chance (the panel adds no signal), `< 0` systematic disagreement, usually meaning the judges read the rubric differently and the prompt or panel needs work. `None` when undefined (single judge, or fewer than two multi-judge samples).
- Pairwise `consistency` is self-agreement under fixed conditions — not accuracy, not difficulty. A judge can be steadily wrong. `None` means no measurable repeats, not zero.

For validating a judge against **human labels** rather than against itself, use the `orq-evaluator-alignment` skill.

## An orq.ai platform evaluator as a scorer

When the judge is defined on the platform rather than in code, call it from a plain scorer:

```python
import os
from typing import Any
from orq_ai_sdk import Orq
from evaluatorq import DataPoint, ScorerParameter

async def orq_eval_scorer(params: ScorerParameter) -> dict[str, Any]:
    data: DataPoint = params["data"]
    orq = Orq(api_key=os.environ["ORQ_API_KEY"])
    result = await orq.evals.invoke_async(        # evals.invoke_async — NOT evaluators.invoke
        id="<EVALUATOR_ID>",
        query=data.inputs["query"],
        output=str(params["output"]),   # whatever the job returned — index it (output["response"]) if the job returns a dict
        reference=data.expected_output or "",
    )
    return {"value": 1.0 if result.value else 0.0, "explanation": result.explanation or ""}
```

## Endpoint and retries

`llm_jury()` and `PairwiseComparator` send judge calls to the orq router's `responses` endpoint, which is the endpoint the router prices — so a verdict records cost the same way the call it judges does. There is no opt-out; `run_judge` falls back to Chat Completions on its own for a client that does not route through the router, a model the catalogue does not qualify for Responses, or a model that 400s on the endpoint.

Retries live in `run_judge`, not the SDK: it clones whatever client it is given with `max_retries=0` so the two layers never multiply. Default budget is one retry per judge call (`LLMCallConfig.retry_count=1`).

## Structured (multi-axis) results

A judge that must return several named sub-scores at once returns an `EvaluationResultCell` from a custom scorer — `llm_jury()` does not produce one, since a jury returns a single aggregated verdict:

```python
from evaluatorq import EvaluationResult, EvaluationResultCell

EvaluationResult(
    value=EvaluationResultCell(type="rubric", value={"relevance": 0.8, "coherence": 0.9}),
    pass_=True,
    explanation="Multi-criteria rubric",
)
```

Know the trade before choosing it: **sub-scores are never aggregated.** The summary table prints the literal `[structured]` — no per-key mean or trend anywhere in the terminal. Three float evaluators give three averages; one cell with three keys gives a placeholder, and `pass_` is the only aggregated signal a cell contributes to a CI gate. Top-level sub-score values are `str | int | float` or a dict one level deeper whose own values are `str | float` only — **not `bool`** (`True` is silently coerced to `1`) and not a list (raises). Set `"evaluator_type": "python_eval"` on the evaluator so the cell also lands in blob storage: the `orq.score` span attribute is dropped whole past 512 characters, silently, and a ten-key rubric clears that easily.
