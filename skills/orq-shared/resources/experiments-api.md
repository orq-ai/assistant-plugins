# Experiments API (v3)

Shared reference for `orq-run-experiment`, `orq-compare-agents` and `orq-build-benchmark`. Reference pages live under [reference/experiments](https://docs.orq.ai/reference/experiments/create-an-experiment).

Every call goes through the CLI's `experiments` group (`orq experiments --help` lists it under Optimization), which signs the request with the active profile (or `ORQ_API_KEY`, `--profile`, `--server`). Resolve the key first per [run-key-preflight.md](run-key-preflight.md). If `orq experiments` is an unknown command, the CLI is too old: upgrade it per [orq-cli](../../orq-cli/SKILL.md) rather than calling the API by hand.

How the commands behave:

- Path parameters are positional: `orq experiments create-task <experiment-id> <run-id>`.
- Body fields are flags. Nested objects (`--prompt`, `--agent`, `--constraints`, `--targets`) take a JSON string; `--from-file body.json` sends a whole body instead. `<command> --example` prints a sample body without sending anything.
- Responses are the bare API body, so projections start at the top level (`-j experiment_id`). A 4xx exits non-zero with the reason on stderr.
- `-o json --raw -j '<path>'` prints one bare value, for capturing IDs into a shell variable.
- `-j` takes JMESPath. String literals go in single quotes (`'Run 2'`); backticks hold JSON, so a bare string in backticks fails.
- Commands with no body need `</dev/null` when run from a script, or the CLI waits for stdin.
- `orq experiments delete` needs `--force` outside an interactive terminal.

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
read -r EXP RUN < <(orq experiments create --display-name support-replies-v2 --dataset-id <DATASET_ID> \
  -o json --raw -j "join(' ', [experiment_id, selected_run_id])" </dev/null)
```

`--project-id` defaults to the key's project and is required for keys that span several projects.

## 2. Add task columns

Prompt task. Resolve the model's `refId` from `orq models list` per [model-slugs.md](model-slugs.md); do not type a slug from memory.

```bash
orq experiments create-task $EXP $RUN --display-name "baseline prompt" -o json --raw -j column_id </dev/null \
  --prompt '{"prompt_config":{"model":"<MODEL_REF_ID>","messages":[
    {"role":"system","content":"You answer customer questions in two sentences."},
    {"role":"user","content":"{{user_message}}"}]}}'
```

`messages_source`: `MESSAGES_SOURCE_PROMPT_CONFIG` (default, the prompt's messages only), `MESSAGES_SOURCE_MESSAGES_COLUMN` (the row's dataset messages) or `MESSAGES_SOURCE_COMBINED` (prompt messages, then the row's). `engine`: `text` (default), `jinja` or `mustache`.

Agent task, by agent key. System messages are dropped because the agent's instructions provide them.

```bash
orq experiments create-task $EXP $RUN --display-name "support agent" -o json --raw -j column_id </dev/null \
  --agent '{"agent_key":"<AGENT_KEY>","messages":[{"role":"user","content":"{{user_message}}"}]}'
```

Use `"messages_source": "MESSAGES_SOURCE_MESSAGES_COLUMN"` with no `messages` to send the dataset's conversation instead.

`agent_tag` pins a published version of the agent. Its value is the `version` string that `orq agents retrieve` reports and that `--version-increment` creates (for example `"agent_tag":"1.0.0"`); without it the column runs the current version. Two columns with the same `agent_key` and different `agent_tag` compare two versions of one agent in one run, with no copy of the agent.

A tagged column only works in the run it was created in. `create-run` copies agent columns to a new agent with a suffixed key (`<AGENT_KEY>_<id>`) that has no published versions, so a tagged column fails there with `agent version not found`. To reuse a tagged column in a new run, point it back with `update-task` (the original `agent_key` plus `agent_tag`) and execute with `EXECUTE_SCOPE_PARTIAL`. The copied agent stays in the workspace after the experiment is deleted; remove it with `orq agents delete`.

## 3. Add evaluator columns

Existing evaluators, by ID. The evaluator's config lives on the evaluator; change it there.

```bash
orq experiments create-eval $EXP $RUN --evaluator-id <EVALUATOR_ID> -o json --raw -j column_id </dev/null
```

## 4. Execute and poll

```bash
orq experiments execute-run $EXP $RUN --scope EXECUTE_SCOPE_FULL --constraints '{"llm":{"concurrency":5}}' \
  -o json -j status </dev/null

for i in $(seq 90); do
  s=$(orq experiments get-run $EXP $RUN -o json --raw -j status </dev/null)
  [[ $s =~ ^RUN_STATUS_(COMPLETED|FAILED|CANCELED)$ ]] && break; sleep 10
done; echo "$s"

orq experiments get-run $EXP $RUN -o json -j cell_status_counts </dev/null
```

The loop gives up after 15 minutes so an empty `$RUN` or a stuck run cannot hang the shell; if `$s` is not terminal, check the run before polling again. Run statuses: `RUN_STATUS_DRAFT`, `RUN_STATUS_QUEUED`, `RUN_STATUS_RUNNING`, then one of `RUN_STATUS_COMPLETED`, `RUN_STATUS_FAILED`, `RUN_STATUS_CANCELED`. A completed run can still hold failed cells: check `cell_status_counts.failed` before reading scores. `not_run` cells on other columns after a clean run are normal.

Scopes: `EXECUTE_SCOPE_FULL` (every cell), `EXECUTE_SCOPE_PARTIAL` (cells that are not completed or running, so new columns and failed cells), and `EXECUTE_SCOPE_ROW`, `EXECUTE_SCOPE_COLUMN`, `EXECUTE_SCOPE_CELL` with `--targets`. A queued or running run rejects a new execute until it finishes or you cancel it:

```bash
orq experiments cancel-run $EXP $RUN -o json -j status </dev/null
```

`constraints.llm.timeout` (for example `"600s"`) is optional. If set, it must be at least the timeout of every agent in the run, or execute is rejected with `OPTIMIZATION_EXPERIMENTS_RUN_TIMEOUT_BELOW_AGENT_TIMEOUT`. Leave it out unless you need it.

## 5. Read results

Aggregates per task column:

```bash
orq experiments get-run-metrics $EXP $RUN -o json -j metrics </dev/null
```

Metric keys are `{task_column_id}_{evaluator_id}` for evaluators and `{task_column_id}_orq_cost`, `{task_column_id}_orq_latency`, `{task_column_id}_orq_input_tokens` and so on for usage. Each value holds one of:

- `boolean`: `.boolean.passed_rate`, a percentage from 0 to 100
- `number`: `min`, `max`, `avg`, `sum` (the total, for example total cost), `median`, `std_dev`, `range`, `count`
- `categorical`: label counts, with no `baseline`

Per-row evaluator scores come from `query-run`. Each row comes back with the cells of the chosen columns plus the metric and evaluator cells attached to them, so select cells by field: an evaluator cell has `type` `CELL_TYPE_EVALUATOR` and `task_column_id` equal to the task column it scored. `<TASK_COLUMN_ID>` is the `column_id` returned in step 2.

```bash
# Per-row evaluator scores for one task column (query the evaluator column by its display name)
orq experiments query-run $EXP $RUN --body-columns "<EVALUATOR_DISPLAY_NAME>" --limit 50 -o json </dev/null \
  -j "rows[].{row_id: row_id, score: cells[?type=='CELL_TYPE_EVALUATOR' && task_column_id=='<TASK_COLUMN_ID>'] | [0].value.value}"
```

`--body-columns` is the body's `columns` field, renamed because `--columns` is the CLI's table-output flag; repeat it for several columns. Evaluator cells and their metrics can land a few seconds after the run reports `RUN_STATUS_COMPLETED`; if a score is missing, read again before treating it as failed. An evaluator cell's `value` holds `type` (`boolean` or `number`), `value` and, for booleans, `passed`. A failed cell has `status` `CELL_STATUS_FAILED` and the reason in `error_message` (for example a deprecated model). `row_id` is the same across columns, so rows pair up between task columns. When `has_more` is true, repeat the call with `--starting-after <last row_id>` for the next page. An evaluator cell carries the judge's `trace_id`; turn it into a link with [orq-trace-format](../../orq-trace-format/SKILL.md).

Task outputs come from the export until optimization-api v4.19.0 reaches production: until then `query-run` returns `value: null` for task cells (`CELL_TYPE_RESPONSE` and `CELL_TYPE_AGENT_RESPONSE`) while evaluator and metric cells are filled. Each export record is one row and task column, with `model` (the column's display name), `input`, `llm_response` and one field per evaluator, named by the evaluator key.

```bash
orq experiments export-run $EXP $RUN --format json -o json \
  -j '[].{column: model, input: input, answer: llm_response}' </dev/null
```

Use `--format json` until the CLI can read the `jsonl` export: with `jsonl` it fails to parse the response (`invalid character '{' after top-level value`).

## 6. Iterate: new run, change one thing, compare to baseline

```bash
RUN2=$(orq experiments create-run $EXP --key "Run 2 - shorter system prompt" -o json --raw -j run_id </dev/null)

orq experiments list-tasks $EXP $RUN2 -o json -j 'data[].{column_id: column_id, display_name: display_name}' </dev/null

orq experiments update-task $EXP $RUN2 <TASK_COLUMN_ID> -o json -j column_id </dev/null \
  --prompt '{"prompt_config":{"model":"<MODEL_REF_ID>","messages":[{"role":"user","content":"{{user_message}}"}]}}'
```

Execute `RUN2` as in step 4, then compare against the first run:

```bash
orq experiments get-run-metrics $EXP $RUN2 --baseline-run-id $RUN -o json -j metrics </dev/null
```

Each metric gains `baseline: {avg, delta, pct_change, direction}` with `direction` one of `up`, `down`, `same`.

Rules for runs:

- A new run copies the selected run's columns with empty cells, so any execute on it, `EXECUTE_SCOPE_PARTIAL` included, runs and bills every cell.
- `update-task` replaces the whole task config. Resend `messages_source`, `engine` and `tools` if the column used non-default values.
- Prompt columns keep their `column_id` in a new run. Agent columns get a new one (and the copied `agent_key` gains a suffix), so their metric keys change and `baseline` is null for them. Compare agents, or agent versions by `agent_tag`, as columns in one run; use the baseline flow for prompt columns. A tagged agent column breaks in a new run until it is pointed back at the original agent (step 2).

## Other commands

```bash
orq experiments list -o json -j 'data[].{id: experiment_id, name: display_name}' </dev/null
orq experiments list-runs $EXP -o json -j 'data[].{run_id: run_id, key: key}' </dev/null   # newest first
orq experiments delete $EXP --force </dev/null
```

The experiment's UI link has the shape shown in [orq-trace-format](../../orq-trace-format/SKILL.md).
