# Benchmark candidates with evaluatorq

The alternative to step 4 of [SKILL.md](../SKILL.md) when a candidate is not a plain prompt call. Steps 1 to 3 are the same. The helpers here take the tables `rubric_scores` builds from evaluatorq results, not the flat `{row_id: score}` dicts that `paired_row_gap` in SKILL.md takes.

## Run

The template below needs a recent evaluatorq build that exports `evaluatorq`, `job`,
`llm_jury`, `exact_match_evaluator`, and `DatasetIdInput`. Install the current package
from `orq-ai/evaluatorq` (`uv add "evaluatorq @ git+https://github.com/orq-ai/evaluatorq"`,
or a published release once available). The older evaluatorq bundled in `orqkit` does
not export all of these, so a template run fails at import against it. Confirm with
`python -c "import evaluatorq; evaluatorq.llm_jury"` before running.

One `evaluatorq()` call, one job per candidate
model or agent, evaluated on the dataset. Results upload to orq Experiments when
`ORQ_API_KEY` is set.

Start the script with these helpers. The template prints from `rubric_scores`
inside `main()`, and the next section reads the results with all three: `rubric_scores` gives
each candidate's scored, errored and skipped counts, `paired_gap` compares two
candidates on the datapoints both scored, and `tied` applies the tie rule:

```python
import math
import statistics


def rubric_scores(results, evaluator='task-rubric'):
    """Per candidate: scores by datapoint, plus how many errored or were skipped."""
    table = {}
    for index, point in enumerate(results):
        for job in point.job_results or []:
            row = table.setdefault(job.job_name, {'scored': {}, 'errored': 0, 'skipped': 0})
            score = next((s for s in job.evaluator_scores or [] if s.evaluator_name == evaluator), None)
            if job.error or (score is not None and score.error):
                row['errored'] += 1
            elif score is not None:
                row['scored'][index] = float(score.score.value)
    for row in table.values():
        row['skipped'] = len(results) - len(row['scored']) - row['errored']
    return table


MIN_SHARED = 5  # below this a standard error says little, so no gap is reported


def paired_gap(a, b):
    """Mean of a minus b on shared datapoints, its standard error, and how many were
    shared. None under MIN_SHARED."""
    diffs = [a['scored'][i] - b['scored'][i] for i in a['scored'] if i in b['scored']]
    if len(diffs) < MIN_SHARED:
        return None
    return statistics.mean(diffs), statistics.stdev(diffs) / math.sqrt(len(diffs)), len(diffs)


def tied(gap):
    """True when paired_gap cannot separate two candidates, in either direction."""
    return gap is None or abs(gap[0]) <= 2 * gap[1]
```

Then the template, grounded in the current evaluatorq API:

```python
import asyncio
from evaluatorq import DatasetIdInput, evaluatorq, exact_match_evaluator, job, llm_jury

CANDIDATES = ['openai/gpt-5', 'anthropic/claude-opus-4-1', 'google-ai/gemini-2.5-pro']

def make_job(model: str):
    @job(model)
    async def run(dp, _i):
        # call_model: define this yourself. Invoke the candidate on the datapoint
        # input via your orq client / invoke_model and return its text output; keep
        # every candidate identical except the model id. This example dataset uses a
        # 'text' input column for the prompt (see step 2 of SKILL.md), so dp.inputs['text'] is the prompt.
        # For an `agentic` task, return the produced file's text: that text is the
        # answer the judge grades.
        return await call_model(model, dp.inputs['text'])
    return run

rubric = llm_jury(
    name='task-rubric',
    criteria=(
        'Score the answer against these criteria drawn from the team\'s own review notes:\n'
        '- <criterion 1 from intake>\n- <criterion 2 from intake>\n...'
    ),
    # Numeric on purpose. The binary pass or fail rule in `orq-run-experiment` is for
    # finding failure modes in one system. A benchmark ranks candidates against each
    # other, and pass or fail ties most of them on a small set.
    verdict_kind='numeric',
    # Required: a numeric classify judge errors without these. The score runs 0 to 1, and
    # these are its anchors, lowest first. Write them from the intake's good vs bad notes.
    levels=[
        '<what a rejected answer looks like>',
        '<what a usable answer that needs edits looks like>',
        '<what an answer the team would ship as is looks like>',
    ],
    # The judge must NOT be one of CANDIDATES. A model scoring its own answer self-prefers
    # and inflates its grade, and nothing downstream guards against it. Pick a strong model
    # that is not under test.
    model='<judge_model_not_in_CANDIDATES>',
)

async def main():
    results = await evaluatorq(
        'our-task-benchmark',
        data=DatasetIdInput(dataset_id='<orq_dataset_id>'),
        jobs=[make_job(m) for m in CANDIDATES],
        evaluators=[rubric],            # add exact_match_evaluator() for fixed-label tasks
        description='Which model is best at <our task>',
    )
    # The next section reads these. Print them before reporting anything.
    print({model: {**row, 'scored': len(row['scored'])} for model, row in rubric_scores(results).items()})

asyncio.run(main())
```

Every job runs against every datapoint, so each candidate is one column. Verify
the candidate model ids against `list_models` (orq MCP) before running; do not
invent model ids.

## Read the results

A run can report success while every rubric score errored, so read the results that
`evaluatorq()` returns with the helpers above.


If a whole evaluator errored, fix it and rerun.

Open the run in orq Experiments (the call prints the experiment URL) and compare
candidates per category, not just overall. Next to each score, report that candidate's
scored, errored and skipped counts from `rubric_scores`.

Report the winner with its margin and where it is weak, with two exceptions:

- Compare two candidates with `paired_gap`, never by their overall means. When the
  counts differ, a mean over fewer datapoints is not comparable, but the paired gap
  still is: it only uses the datapoints both candidates scored. One error in fifty
  does not block a winner.
- If `tied(paired_gap(a, b))` is true, report those candidates as tied: the absolute
  gap is at most twice its standard error, or fewer than `MIN_SHARED` datapoints were
  shared. Report the shared count `paired_gap` returns next to the gap.
