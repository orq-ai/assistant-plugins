# Experiments API (v3)

Shared reference for `orq-run-experiment`, `orq-compare-agents` and `orq-build-benchmark`. Reference pages live under [reference/experiments](https://docs.orq.ai/reference/experiments/create-an-experiment).

The CLI has no `experiments` group, so every call goes through `orq request <METHOD> <PATH>`, which signs the request with the active profile (or `ORQ_API_KEY`, `--profile`, `--server`). Resolve the key first per [run-key-preflight.md](run-key-preflight.md).

How `orq request` behaves:

- Send JSON bodies on stdin (`echo '<json>' | orq request POST ...` or a heredoc). A JSON string as a positional argument fails to parse.
- The response is wrapped as `{ok, status, headers, body_text, body}`, so projections read `body.*`.
- A 4xx still exits 0 with `ok: false` and the reason in `body.message`. Project `ok` and `status` on every write and stop when `ok` is false.
- `-o json --raw -j '<path>'` prints one bare value, for capturing IDs into a shell variable.
- `-j` takes JMESPath. String literals go in single quotes (`'Run 2'`); backticks hold JSON, so a bare string in backticks fails.
- GET calls need `</dev/null` when run from a script, or the CLI waits for stdin.
- DELETE needs `--force` outside an interactive terminal.

## When to use it instead of the MCP tools

- the experiment must be repeatable from a script, CI or cron, with no agent present
- the task is an orq.ai **agent** (agent task column), or several prompt and agent columns sit side by side in one run
- a variant runs as a new run of the same experiment and is compared to a baseline run with deltas
- you need to re-run only failed or new cells, cancel a run, or read per-cell outputs and trace IDs

`create_experiment` (MCP) creates an experiment with prompt models or agents and can queue it in one call (`auto_run`), which is fine for a quick one-off from chat. It cannot add columns to an existing run, re-run only failed or new cells, compare against a baseline run or page through cells.

## Model

An experiment holds runs. A run is a grid: one row per dataset datapoint (at most 1000) and columns of these types: dataset, task (`prompt`, `agent` or `openresponse`), metric (cost, latency, tokens; added with the first task column), evaluator and annotation. Each evaluator scores every task column. Dataset `inputs` fill `{{variables}}` in the task messages.

## 1. Create the experiment

Its first run is a draft named "Run 1".

```bash
read -r EXP RUN < <(echo '{"display_name":"support-replies-v2","dataset_id":"<DATASET_ID>"}' \
  | orq request POST /v3/experiments -o json --raw -j "join(' ', [body.experiment_id, body.selected_run_id])")
```

If either value is empty, rerun without `--raw` and project `{ok: ok, status: status, message: body.message}` to see why. `project_id` defaults to the key's project and is required for keys that span several projects.

## 2. Add task columns

Prompt task. Resolve the model's `refId` from `orq models list` per [model-slugs.md](model-slugs.md); do not type a slug from memory.

```bash
orq request POST /v3/experiments/$EXP/runs/$RUN/tasks -o json \
  -j '{ok: ok, status: status, column_id: body.column_id}' <<'JSON'
{
  "display_name": "baseline prompt",
  "prompt": {
    "prompt_config": {
      "model": "<MODEL_REF_ID>",
      "messages": [
        {"role": "system", "content": "You answer customer questions in two sentences."},
        {"role": "user", "content": "{{user_message}}"}
      ]
    }
  }
}
JSON
```

`messages_source`: `MESSAGES_SOURCE_PROMPT_CONFIG` (default, the prompt's messages only), `MESSAGES_SOURCE_MESSAGES_COLUMN` (the row's dataset messages) or `MESSAGES_SOURCE_COMBINED` (prompt messages, then the row's). `engine`: `text` (default), `jinja` or `mustache`.

Agent task, by agent key. System messages are dropped because the agent's instructions provide them.

```bash
orq request POST /v3/experiments/$EXP/runs/$RUN/tasks -o json \
  -j '{ok: ok, status: status, column_id: body.column_id}' <<'JSON'
{
  "display_name": "support agent",
  "agent": {
    "agent_key": "<AGENT_KEY>",
    "messages_source": "MESSAGES_SOURCE_PROMPT_CONFIG",
    "messages": [{"role": "user", "content": "{{user_message}}"}]
  }
}
JSON
```

Use `"messages_source": "MESSAGES_SOURCE_MESSAGES_COLUMN"` with no `messages` to send the dataset's conversation instead.

## 3. Add evaluator columns

Existing evaluators, by ID. The evaluator's config lives on the evaluator; change it there.

```bash
echo '{"evaluator_id":"<EVALUATOR_ID>"}' \
  | orq request POST /v3/experiments/$EXP/runs/$RUN/evaluators -o json -j '{ok: ok, status: status, column_id: body.column_id}'
```

## 4. Execute and poll

```bash
echo '{"scope":"EXECUTE_SCOPE_FULL","constraints":{"llm":{"concurrency":5}}}' \
  | orq request POST /v3/experiments/$EXP/runs/$RUN/execute -o json -j '{ok: ok, status: status, run_status: body.status}'

for i in $(seq 90); do
  s=$(orq request GET /v3/experiments/$EXP/runs/$RUN -o json --raw -j 'body.status' </dev/null)
  [[ $s =~ ^RUN_STATUS_(COMPLETED|FAILED|CANCELED)$ ]] && break; sleep 10
done; echo "$s"

orq request GET /v3/experiments/$EXP/runs/$RUN -o json -j 'body.cell_status_counts' </dev/null
```

The loop gives up after 15 minutes so an empty `$RUN` or a stuck run cannot hang the shell; if `$s` is not terminal, check the run before polling again. Run statuses: `RUN_STATUS_DRAFT`, `RUN_STATUS_QUEUED`, `RUN_STATUS_RUNNING`, then one of `RUN_STATUS_COMPLETED`, `RUN_STATUS_FAILED`, `RUN_STATUS_CANCELED`. A completed run can still hold failed cells: check `cell_status_counts.failed` before reading scores. `not_run` cells on other columns after a clean run are normal.

Scopes: `EXECUTE_SCOPE_FULL` (every cell), `EXECUTE_SCOPE_PARTIAL` (cells that are not completed or running, so new columns and failed cells), and `EXECUTE_SCOPE_ROW`, `EXECUTE_SCOPE_COLUMN`, `EXECUTE_SCOPE_CELL` with `targets`. A queued or running run rejects a new execute until it finishes or you cancel it:

```bash
echo '{}' | orq request POST /v3/experiments/$EXP/runs/$RUN/cancel -o json -j '{ok: ok, status: status}'
```

`constraints.llm.timeout` (for example `"600s"`) is optional. If set, it must be at least the timeout of every agent in the run, or execute is rejected with `OPTIMIZATION_EXPERIMENTS_RUN_TIMEOUT_BELOW_AGENT_TIMEOUT`. Leave it out unless you need it.

## 5. Read results

Aggregates per task column:

```bash
orq request GET /v3/experiments/$EXP/runs/$RUN/metrics -o json -j 'body.metrics' </dev/null
```

Metric keys are `{task_column_id}_{evaluator_id}` for evaluators and `{task_column_id}_orq_cost`, `{task_column_id}_orq_latency`, `{task_column_id}_orq_input_tokens` and so on for usage. Each value holds one of:

- `boolean`: `.boolean.passed_rate`, a percentage from 0 to 100
- `number`: `min`, `max`, `avg`, `sum` (the total, for example total cost), `median`, `std_dev`, `range`, `count`
- `categorical`: label counts, with no `baseline`

Per-row cells for chosen columns, by display name. Each row comes back with the cells of those columns plus the metric and evaluator cells attached to them, so select cells by field: the task output has `column_id` equal to the task column ID, and an evaluator cell has `type` `CELL_TYPE_EVALUATOR` and `task_column_id` equal to the task column it scored. `<TASK_COLUMN_ID>` is the `column_id` returned in step 2.

```bash
echo '{"columns":["baseline prompt"],"limit":50}' \
  | orq request POST /v3/experiments/$EXP/runs/$RUN/query -o json \
    -j "{has_more: body.has_more, rows: body.rows[].{row_id: row_id, out: cells[?column_id=='<TASK_COLUMN_ID>'] | [0].{status: status, error: error_message, text: value.last_message.content[0].text, trace_id: value.trace_id}}}"

# Per-row evaluator scores for one task column (query the evaluator column by its display name)
echo '{"columns":["<EVALUATOR_DISPLAY_NAME>"],"limit":50}' \
  | orq request POST /v3/experiments/$EXP/runs/$RUN/query -o json \
    -j "body.rows[].{row_id: row_id, score: cells[?type=='CELL_TYPE_EVALUATOR' && task_column_id=='<TASK_COLUMN_ID>'] | [0].value.value}"
```

An evaluator cell's `value` holds `type` (`boolean` or `number`), `value` and, for booleans, `passed`. A failed cell has `status` `CELL_STATUS_FAILED` and the reason in `error_message` (for example a deprecated model). `row_id` is the same across columns, so rows pair up between task columns. When `body.has_more` is true, send the same body with `"starting_after": "<last row_id>"` for the next page. Turn a `trace_id` into a link with [orq-trace-format](../../orq-trace-format/SKILL.md).

## 6. Iterate: new run, change one thing, compare to baseline

```bash
RUN2=$(echo '{"key":"Run 2 - shorter system prompt"}' \
  | orq request POST /v3/experiments/$EXP/runs -o json --raw -j 'body.run_id')

orq request GET /v3/experiments/$EXP/runs/$RUN2/tasks -o json \
  -j 'body.data[].{column_id: column_id, display_name: display_name}' </dev/null

orq request PATCH /v3/experiments/$EXP/runs/$RUN2/tasks/<TASK_COLUMN_ID> -o json -j '{ok: ok, status: status}' <<'JSON'
{"prompt": {"prompt_config": {"model": "<MODEL_REF_ID>", "messages": [{"role": "user", "content": "{{user_message}}"}]}}}
JSON
```

Execute `RUN2` as in step 4, then compare against the first run:

```bash
orq request GET "/v3/experiments/$EXP/runs/$RUN2/metrics?baseline_run_id=$RUN" -o json \
  -j 'body.metrics' </dev/null
```

Each metric gains `baseline: {avg, delta, pct_change, direction}` with `direction` one of `up`, `down`, `same`.

Rules for runs:

- A new run copies the selected run's columns with empty cells, so any execute on it, `EXECUTE_SCOPE_PARTIAL` included, runs and bills every cell.
- PATCH replaces the whole task config. Resend `messages_source`, `engine` and `tools` if the column used non-default values.
- Prompt columns keep their `column_id` in a new run. Agent columns get a new one (and the copied `agent_key` gains a suffix), so their metric keys change and `baseline` is null for them. Compare agents as columns in one run; use the baseline flow for prompt columns.

## Other endpoints

```bash
orq request GET /v3/experiments -o json -j 'body' </dev/null                      # list experiments
orq request GET /v3/experiments/$EXP/runs -o json -j 'body' </dev/null            # list runs, newest first
orq request GET /v3/experiments/$EXP/runs/$RUN/export -o json -j 'body' </dev/null  # export a run
orq request DELETE /v3/experiments/$EXP -o json -j '{ok: ok, status: status}' --force </dev/null
```

The experiment's UI link has the shape shown in [orq-trace-format](../../orq-trace-format/SKILL.md).
