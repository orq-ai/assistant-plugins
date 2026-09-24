# Inputs: what `data` accepts

Probed against Python `evaluatorq` 1.39.0, 2026-09-20. Upstream reference (re-probe against it when evaluatorq releases): [evaluation-reference](https://orq-ai.github.io/evaluatorq/evaluation-reference/).

`evaluatorq(name, data=..., jobs=..., evaluators=...)` takes one of four input shapes. Everything else — traces, production logs, a CSV — is converted into one of them first.

| `data` value | What it is | Needs `ORQ_API_KEY` |
|---|---|---|
| `list[DataPoint]` or `list[dict]` | Inline rows. A plain dict with `inputs` / `expected_output` keys is accepted anywhere a `DataPoint` is. | no |
| `list[Awaitable[DataPoint]]` | Rows that resolve lazily — a network fetch per row, streamed into the run instead of blocking on all of them first. | no |
| `DatasetIdInput(dataset_id="...")` | Rows from an orq.ai dataset, fetched in pages of 50. | yes |
| `ExperimentInput(experiment_id="...", run_id=None)` | The recorded outputs of a past orq.ai experiment run. Requires `inference=False`. | yes |

## A datapoint

```python
from evaluatorq import DataPoint

DataPoint(inputs={"question": "What is the capital of France?"}, expected_output="Paris")
```

- `inputs` — a free-form dict. Your job reads it (`data.inputs["question"]`); the default judge template renders it as `{{input.all_messages}}`.
- `expected_output` — optional reference. `exact_match_evaluator()` / `string_contains_evaluator()` compare against it, and the judge sees it as `{{input.expected_output}}`.

## Platform datasets

```python
from evaluatorq import DatasetIdInput, evaluatorq

await evaluatorq("qa-eval", data=DatasetIdInput(dataset_id="<DATASET_ID>"), jobs=[...], evaluators=[...])
```

A dataset row's `inputs` dict becomes `DataPoint.inputs` verbatim, and its `expected_output` becomes `DataPoint.expected_output`.

`include_messages=True` additionally copies the row's stored `messages` list into `inputs["messages"]` — use it for chat datapoints whose conversation is not already in `inputs`. It raises if `inputs` already has a `messages` key rather than overwriting it.

The legacy dict form (`data={"dataset_id": "..."}`) still parses, but prefer the model — it is the one the type checker knows.

## Replaying a past experiment (no-inference mode)

To score responses that already exist rather than generating new ones — new evaluators against an old run, a re-grade without paying for generation again:

```python
from evaluatorq import ExperimentInput, evaluatorq

await evaluatorq(
    "replay",
    data=ExperimentInput(experiment_id="<experiment_id>"),  # omit run_id for the latest run
    evaluators=[my_judge],
    inference=False,
)
```

- `experiment_id` — the ID in the experiment URL, `/experiments/<id>` (the REST API calls experiments "spreadsheets", so the same ID appears under `/v2/spreadsheets/<id>`).
- `run_id` — optional; every execution of an experiment is a run. Omit it to replay the latest.
- With `inference=False`, `jobs` is optional and ignored. A row whose recorded response is missing or blank fails loudly instead of being skipped.

`inference=False` also works with `DatasetIdInput` and inline rows when you supply the responses yourself.

## Production traces

Core `evaluatorq()` has **no trace input** — traces are turned into datapoints by the simulation helpers first, and the result is a normal datapoint list you can hand to `evaluatorq()` or to `simulate()`.

```python
from evaluatorq.simulation import (
    datapoints_from_traces,
    extend_from_traces,
    fetch_trace_conversations,
    summarize_conversations,
)

conversations = await fetch_trace_conversations(limit=20, search="", filters=None)  # recorded traffic from orq.ai
summaries = await summarize_conversations(conversations)      # optional: summarize once, reuse below
datapoints = await datapoints_from_traces(conversations, summaries=summaries)   # one datapoint per trace
datapoints += await extend_from_traces(conversations, num_datapoints=10, summaries=summaries)  # new cases matching real traffic
```

From the CLI, `eq sim from-traces … --extend N` is the same two modes. `extend_from_experiment("ex_abc", …)` is the experiment-shaped sibling: it seeds the generators with a past run's personas and scenarios and returns *new* similar-but-not-duplicate datapoints — distinct from `ExperimentInput`, which replays the recorded rows unchanged.

All four take `llm_config=LLMCallConfig(...)`; see [tuning.md](tuning.md). For the full trace workflow use the `orq-simulate-agent` and `orq-analyze-traces` skills.

## What comes back

`evaluatorq()` returns `list[DataPointResult]`:

```
DataPointResult
  .data_point                  DataPoint
  .error                       str | None      — the row failed before any job ran
  .job_results                 list[JobResult] | None
      .job_name                str
      .output                  the job's return value
      .error                   str | None      — this job failed; its evaluators were skipped
      .evaluator_scores        list[EvaluatorScore] | None
          .evaluator_name      str
          .error               str | None      — the scorer itself raised
          .score               EvaluationResult
              .value           str | int | float | bool | EvaluationResultCell | dict
              .explanation     str | None
              .pass_           bool | None     (serialized as `pass`)
              .token_usage     TokenUsage | None
              .raw_output      dict | None     — judge internals, e.g. raw_output["jury"]
```

Guard both levels when you walk it — `job_results` is `None` for a row that failed early, and `evaluator_scores` is empty for a job that raised:

```python
for result in results:
    for job_result in result.job_results or []:
        for score in job_result.evaluator_scores or []:
            print(job_result.job_name, score.evaluator_name, score.score.value, score.score.pass_)
```

With `ORQ_API_KEY` set the same results are uploaded to orq.ai → Experiments; `path="Team/Sprint-42"` groups them, `description=...` documents the run. Judge internals (`raw_output`) are deliberately stripped from the upload and stay local.

## Reporting a failure the job handled

A job that lets an exception raise needs nothing — the row is recorded as failed. A job that *catches* the failure to keep the batch alive must say so, because a returned value looks like a clean run. That is the **raw-dict** job contract:

```python
async def resilient_job(data: DataPoint, row: int) -> dict:
    try:
        answer = await call_my_agent(data.inputs["text"])
    except MyAgentError as exc:
        return {"name": "my-agent", "output": None, "error": str(exc)}   # keeps the partial output, still fails the row
    return {"name": "my-agent", "output": answer, "error": None}
```

Emit `error` on every path (`None` on success) — an omitted key is indistinguishable from a clean run. A row with a non-empty `error` counts in `Failed Jobs` and its **evaluators are skipped**, so a dead target does not cost a judge call per row.

`@job()`-decorated functions cannot use this: the decorator wraps the return value into `{"name", "output"}`, so an `error` key lands *inside* `output` where a judge reads it as the target's own words. A decorated job reports a row failure by raising.

## Gating CI

```python
from evaluatorq.evaluatorq import check_pass_failures

results = await evaluatorq(...)
if check_pass_failures(results, treat_errors_as_failure=True):
    raise SystemExit(1)
```

`evaluatorq()` never exits the process itself. `treat_errors_as_failure=True` also gates on errored rows; the default `False` gates on evaluator `pass_` alone, so a run whose target was dead throughout can pass a gate that leaves it off.
