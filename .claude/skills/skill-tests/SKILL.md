---
name: skill-tests
description: Test the orq skills in this assistant-plugins checkout, or set up tests for a skill that has none. Runs factual drift checks (do the tools, CLI commands, SDK calls and URLs a SKILL.md names still exist) and invocation and behavioural evals (does the right skill fire, and are its first actions right) in real Claude Code and OpenCode sessions through `orq launch`, then reports per skill with the limits of what a pass shows. Scope is chosen up front (all skills, a selection or one; factual, invocation or behavioural) and agents, the model each one runs on, repeats, pass thresholds and cost are chosen and confirmed before anything runs. New cases are drafted with the user and saved only after they approve them. Use when a maintainer asks to "test the skills", "run the skill evals", "add tests for a skill", "did my SKILL.md change break anything", or before merging a skill change. Do NOT use to grade the full workflow a skill runs (these tests stop at the first turn), or to test code outside skills/. Maintainer-only; lives in .claude/skills so it never ships to users.
metadata:
  # Boolean on purpose: `npx skills` skips a skill only on `internal === true`, which keeps
  # this out of every install (CI install-sanity checks the installed set).
  internal: true
---

# skill-tests

Runs the test suites for the skills in this checkout and merges them into one report
per skill, or helps the user set up tests for a skill first. The scripts do the work
and the scoring; this skill agrees scope and cost with the user, launches them, and
presents the report as written. Do not re-score, re-interpret or soften a result.

| Suite | Script | What it answers | Cost |
|---|---|---|---|
| Factual | `tests/scripts/run_factual_tests.py` | Do the tools, CLI commands, SDK calls and URLs a skill names still exist? | free, about a minute |
| Invocation evals | `tests/scripts/run_evals.py` | Does a prompt fire the right skill, and does a near miss fire none? | per agent run, see step 3 |
| Behavioural evals | `tests/scripts/run_evals.py` | After firing, are the first tool calls right, and is nothing created too early? | per agent run, see step 3 |

Every flag the scripts accept is listed in the [flag reference](#flag-reference) at the
end. Use it instead of running `--help`; a flag not listed there does not exist.

## 1. Existing tests or new ones?

Ask first: run the tests that exist, or set up new tests for one or more skills?
If new, go to step 2; when the user has approved the new tests, continue at step 3.
If existing, go straight to step 3.

## 2. Set up new tests (with the user)

The user owns every test; you help draft, they review and decide. Nothing is saved
before they approve it.

- **Which skills:** `node tests/scripts/validate-skills.mjs` (check 14) lists skills
  without a `tests/evals/<skill>/` folder. Ask which skill or skills to cover.
- **Factual rows** are extracted, not written: run
  `uv run --no-project python tests/scripts/bootstrap_factual_tests.py --skill <name> --dry-run`,
  show the rows, and let the user prune false positives before writing without
  `--dry-run` (`--new` adds missing rows to an existing CSV and keeps its review). It
  needs `ORQ_API_KEY` to confirm MCP tool names.
- **Eval cases** are hand-written in the format in [Case format](#case-format).
  Read the skill's SKILL.md, then draft in the conversation only:
  - an invocation case: a prompt a real user would type, in their words, not the
    skill description's (a prompt that echoes the description proves nothing);
  - a near miss: a prompt that sounds related but should fire no skill, or should
    fire a neighbouring skill instead (for example build-evaluator vs
    evaluator-alignment); this is what catches a new skill stealing prompts;
  - a behavioural case, if the skill has a clear first step: expected, allowed and
    forbidden tools, taken from the skill's own steps.
  Show each draft, take the user's edits, and write the YAML only once they approve
  it. Then check it loads with `uv run tests/scripts/run_evals.py --list --case <id>`:
  it validates every case and stops with `invalid eval cases:` and the reasons if one
  is malformed. `node tests/scripts/validate-skills.mjs` then confirms check 14 no
  longer lists the skill.

Do not draft cases the user did not ask for.

## 3. Scope, setup and estimates

Agree the scope, then describe the setup of each suite with its estimate. Spend
nothing yet.

**Scope** (ask for what the user has not said):
- **Skills:** all, a selection, or one. The eval runner's `--skill` is repeatable; the
  factual runner's takes one skill. For a selection, run the factual suite on all
  skills (it is free and fast) and present only the selected ones.
- **Suites:** factual, invocation, behavioural, or any mix. The eval runner has no
  kind filter: to run one kind, take the case ids of that kind from `--list` and pass
  each with `--case`.
- **Near misses:** the cases under `_no-skill/` and `_general/` guard every skill but
  belong to none, so `--skill <name>` leaves them out. When filtering by skill, ask
  whether to add them with `--case`; they are what catches one skill stealing
  another's prompts.
- **Agents:** `claude`, `opencode`, or both (`--agent`).

**Factual setup:** one row per thing a SKILL.md names, in `tests/factual/<skill>.csv`,
checked against the live CLI, MCP server, package indexes and docs. Count the rows for
the selected skills (lines in their CSVs minus the header). Free, about a minute.

**Eval setup:** list the selected cases, the number of agent runs, the model per
agent, and each case's runs and threshold (load `.env` first, as in step 6):

```bash
uv run tests/scripts/run_evals.py --list [--skill <name>] [--case <id>] [--agent <agent>]
```

Each run is a fresh agent session with only this checkout's skills and the orq MCP.
Take the run counts, thresholds and cap from this output, not from memory: each
case sets its own, and they grow as cases are added. Invocation cases usually run 3
times at 2 of 3, a smoke check that catches a skill that stopped firing, not one that
fires unreliably; near misses usually need every run to pass. Cases marked
`(measured, not scored)` are borderline: they report a trigger rate and ignore the
threshold. Estimate cost as the run count x $0.25 (the flat charge for OpenCode,
above Claude's usual cost), with a worst case of twice that: a run that errors in a
way a retry can clear is re-run once, and both attempts are billed (see
[Retries](#retries)). State both against the printed cap. Time: each run is a full
agent session, so wall time is roughly runs / `--parallel` (default 2) sessions.

Give the estimate per suite, then the total.

## 4. Confirm the run config

Before running anything, state the config and wait for the user to confirm it:

- **Repeats:** each case's runs as `--list` prints them, or one override for every
  selected case (`--runs <n>`).
- **Pass threshold:** each case's own as `--list` prints it, or one override for
  every selected case (`--pass-threshold <0..1>`). One override applies to every
  kind, including near misses that normally need every run to pass, so say which
  cases it loosens; to give kinds different thresholds, run them separately with
  `--case`. Borderline cases ignore it.
- **Coding agents:** which ones, and in which mode (host, or `--container`).
- **Model per agent:** ask the user which model each selected agent runs on, and
  offer the options:
  - the default: `--list` prints it per agent as `(orq launch default)`, read from
    `orq launch --dry-run` with the skill-evals key, the same way the runs resolve
    it. In container mode it prints `unresolved`: the image's orq picks it.
  - a model from the workspace catalogue, passed as `--claude-model
    <provider/model_id>` and `--opencode-model <provider/model_id>`. To see the
    catalogue, run `orq launch opencode --dry-run --no-mcp --no-skills` with
    `ORQ_API_KEY=$ORQ_SKILL_EVALS_KEY` and read the models in its config. Claude
    Code talks the Anthropic API, so offer it Claude models only; the runner does
    not check this, and a wrong one fails as a run error.

  Very small models ("lite" and "nano" tiers) tend to fail as models (empty
  responses, broken tool calls) before they reach the skill decision, so a run on
  one measures the model, not the skill. Recommend a flash, mini or haiku tier as
  the floor, but still offer any catalogue model: the choice is the user's. `--list`
  and the run print a warning when a lite or nano model is selected.

  Name the model each agent will run on. With the default, say it is not pinned:
  a later run may resolve to another model.
- **Cost cap and upload:** the cap (`--max-cost-usd`), and whether results upload to
  orq (`--no-send` skips it).

Check the config with `--list` and the same flags: it prints the models and each
case's runs and threshold without spending anything.

The factual suite needs no confirmation when it is the only suite run.

## 5. Preconditions

- `orq --version` is 10.3.1 or newer; if not, tell the user to run `orq update`
  first. A stale CLI reports false drift, and the eval runner refuses to start on
  one unless `--allow-stale-orq` is passed.
- `ORQ_SKILL_EVALS_KEY` is set. It is the `skill-evals` project key; the eval
  runner refuses to start without it. Never substitute another key.
  The experiment upload uses the same key; pass `--no-send` to skip it.
- `ORQ_API_KEY` is set for the factual suite's MCP checks.
- Keep these in a gitignored `.env` at the repo root. Only the eval runner reads
  `ORQ_SKILL_EVALS_KEY` from it; `ORQ_API_KEY` and `SSL_VERIFY` must be in the
  environment, so every command in this skill loads the file into the shell first.
- On Windows, set `SSL_VERIFY=0` for both runners if Python's default TLS setup
  aborts (`OPENSSL_Applink`) or TLS interception (Norton) breaks verification. The
  eval runner saves its results before uploading, so a failed upload loses nothing:
  it prints the `--upload <file>` command to retry.
- **Container mode (recommended when Docker is available):** add `--container` to the
  eval run. Each agent then runs in a Docker container that sees only its own
  workdir, instead of on the host behind stopgaps. It needs Docker running and the
  evaluatorq image, built once per evaluatorq version:
  `uv run --with evaluatorq==<pinned version> eq coding-agent build-image` (the
  runner names the exact command if the image is missing). Behind TLS-intercepting
  antivirus the build's `npm install` fails; build from a copy of the Dockerfile
  that adds the antivirus root CA and sets `NODE_EXTRA_CA_CERTS`. Ask the user
  before building: it takes a few minutes and about 2 GB.

## 6. Run

Run the confirmed suites from the repo root with the Bash tool (not PowerShell: its
`>` writes UTF-16), and write the JSON outputs to `tests/eval-results/` (gitignored).
Each Bash call starts a fresh shell, so load `.env` in the same call as the runner
(this applies to every command in this skill, including `--list` and the bootstrap):

```bash
[ -f .env ] && { set -a; . ./.env; set +a; }
mkdir -p tests/eval-results
uv run tests/scripts/run_factual_tests.py --json [--skill <name>] > tests/eval-results/factual.json
uv run tests/scripts/run_evals.py --json tests/eval-results/evals.json [--skill <name>] [--case <id>] [--agent <agent>] [--runs <n>] [--pass-threshold <x>] [--claude-model <id>] [--opencode-model <id>]
uv run tests/scripts/skill_test_report.py [--factual tests/eval-results/factual.json] [--evals tests/eval-results/evals.json]
```

The eval run takes minutes (each run is a full agent session); run it in the
background and report when it finishes. A non-zero exit code from a runner is a
result, not a failure of this skill: go on to the report. Exit codes:

- `run_evals.py`: 0 all cases passed; 1 at least one case failed (a regression);
  2 anything else: no failure but a case errored or the cost cap stopped runs, or it
  never ran. It stops before running, with exit 2 and no summary, on `invalid eval
  cases:` (a malformed case) or a message naming the missing key, orq binary or
  Docker image, or a stale orq; report that stderr instead of running the merger.
  It deletes the `--json` file at start, and the merger refuses a summary without
  `exit_code`, so a previous batch cannot pass for this one.
- `run_factual_tests.py`: 0 all rows passed or skipped; 1 at least one row failed or
  errored, `--skill` names a skill with no CSV, or no rows loaded at all.
- `skill_test_report.py`: 1 a skill has drift or a regression; 2 none, but something
  errored or was skipped (not clean); else 0.

`skill_test_report.py` prints the merged report; present it as step 7 describes. The
eval summary JSON is described in [Eval summary fields](#eval-summary-fields) if you
need a detail the printed report leaves out.

## 7. Present

Show the merged report per skill, in its own buckets:

- **drift**: a factual check failed; the skill names something that no longer exists. Point to the SKILL.md line the report gives.
- **regression**: an eval case fell below its pass threshold. Name the failing scorer and give its explanation from the report's detail; for an attempted forbidden call that includes the arguments.
- **flaky**: passed some runs, failed others. Flag it; do not call it a pass.
- **error**: a run could not complete, or the cost cap stopped some of a case's runs. Not a verdict on the skill. Group by the `error_kind` the report gives per run:
  - `model`: the gateway or model failed (empty_response, rate limit, provider 5xx); the run shows the agent's own error message.
  - `harness`: orq or the agent CLI exited without an agent error (a launch or parse failure).
  - `timeout`: the agent was killed at the time limit.
  - `tool`: the agent called the expected orq tool and the server failed it. A forbidden attempt or wrong skill in the same run still makes it a regression.

  If most of a case's runs are `model` errors, say the skill is **untested** on that model and suggest re-running on a stronger one. Never call the skill failing. Give each errored run's thread id: errored runs have one too.
- **measured**: borderline cases, reported as a trigger rate only.
- **advisory**: a doc URL the skill links failed. Non-gating; mention it.
- **skipped**: an eval case with no scored run, usually because the cost cap stopped it. It measured nothing; say so, do not call the skill clean.
- **factual skipped**: rows that could not run (usually no `ORQ_API_KEY`). Say how many; a skill with skipped rows is not clean.

Include the orq experiment link, the total cost with its sessions and retries, and
the model each agent ran on. Per-case costs include every attempt, so they add up to
the total. For a failing or errored eval run, give its thread id so the user can open
the trace (`orq traces search --query <id>`). A Claude run's thread id is its session
id; an OpenCode run's is the `skill-evals-<case>-opencode-<run>-<time>` id the runner
sends as `X-ORQ-THREAD-ID`.

End the report with what a pass does and does not show, so nobody reads it as more:

- **What it shows:** the prompt routed to the right skill (or, for a near miss, to
  none), the expected tools were called by name within the first `max_turns`, and no
  forbidden tool was attempted.
- **What it does not show:**
  - Anything after the first user message: cases are single turn, so a skill that
    asks a question is never answered.
  - Whether the agent asked or just stopped: a run that ends silently, or is cut
    off at `max_turns` after read-only calls, passes an "asks first" case too.
  - Arguments, order or count of tool calls; only tool names are scored.
  - What the agent said; no scorer reads the reply text.
  - CLI and script driven work: shell and file writes are denied, so skills that
    work through `orq` or scripts are tested for routing only.
  - Forbidden calls on OpenCode: it hides denied tools, so an attempt is only
    observable on Claude.
- A result holds for the model it ran on and the thresholds used. The summary's
  `models` records each agent's model and whether it came from a flag or the
  `orq launch` default (null when the default could not be read, as in container mode).

Closing these gaps is tracked in RES-1648.

## 8. Dig deeper (only when the user asks)

After the report, offer once to inspect the traces of failing, flaky, measured or
errored runs (an errored run has a thread id whenever a session started). If the user wants it, read each run's trace by its thread id (use the
`orq-cli` skill for the trace commands; project to the fields you need rather than
dumping whole spans) and describe what happened: which skill loaded and when, what
the agent said to the user, the tool calls with their arguments, and whether it
ended its turn or hit `max_turns`. Report these as observations next to the
scored result. They answer questions the scorers cannot (did it ask a sensible
question, did it call the right tool the wrong way), but they do not change a
verdict: never re-score a case from a trace.

## Case format

One YAML file per case at `tests/evals/<folder>/<id>.yaml`. The folder is the skill's
name, or `_no-skill` (prompts that must fire no orq skill) or `_general` (anything
else not owned by one skill). Tool names are bare orq MCP names without the agent's
prefix (`list_models`, not `mcp__plugin_orq_orq-workspace__list_models`).

| Field | Required | Default | Meaning |
|---|---|---|---|
| `id` | yes | | Must equal the file name without `.yaml`, unique across all cases. |
| `skill` | yes | | Must equal the folder name. |
| `kind` | yes | | `invocation` or `behavioural`. |
| `prompt` | yes | | The single user message, sent exactly as written. |
| `expect_skill` | yes | | A skill name in `skills/`, `none` (no orq skill may fire), or `any` (behavioural only: do not score which skill fires). |
| `expect_tools` | no | `[]` | Behavioural: every one must be called, and succeed, within `max_turns`. Each must also be in `allow_tools`, or the agent cannot call it. |
| `allow_tools` | no | `[]` | orq tools the agent may run. Everything else from orq is denied; Claude also always gets `Read`, `Glob`, `Grep` and `Skill`. |
| `forbid_tools` | no | `[create_*, update_*, delete_*, invoke_*]` | Glob patterns; attempting one fails a behavioural case, denied or not. Must not overlap `allow_tools`. |
| `runs` | no | 5 | Runs per agent. Invocation cases usually set 3. |
| `pass_threshold` | no | 0.8 | Fraction of scored runs that must pass. Invocation cases usually set 0.66; near misses 1.0. |
| `max_turns` | no | 6 | Claude turns before the run is stopped. Invocation cases usually set 2. OpenCode has no turn limit: it runs until it stops or times out, and is charged a flat $0.25 per run. |
| `borderline` | no | `false` | `true` makes the case measured only: a trigger rate, no pass or fail. |

Any other field, or a field of the wrong type, is rejected. Scoring:

- **Invocation:** passes when the first orq skill that fires is `expect_skill`, or,
  for `none`, when no orq skill fires. Nothing else is scored.
- **Behavioural:** passes when all three hold: the right skill fired first (skipped
  for `any`); every `expect_tools` entry was called without erroring; and no
  `forbid_tools` pattern was attempted and no orq tool outside `allow_tools` ran.

An invocation example, from `tests/evals/orq-build-evaluator/build-evaluator-fires.yaml`:

```yaml
id: build-evaluator-fires
skill: orq-build-evaluator
kind: invocation
prompt: I need an automated pass/fail check on my orq agent's replies for whether it stays polite. Can you set up a judge for that?
expect_skill: orq-build-evaluator
runs: 3
pass_threshold: 0.66  # 2 of 3
max_turns: 2
```

A behavioural example, from `tests/evals/orq-build-evaluator/build-evaluator-asks-first.yaml`:

```yaml
id: build-evaluator-asks-first
skill: orq-build-evaluator
kind: behavioural
prompt: Build an LLM-as-a-judge evaluator in orq that checks answers are grounded in the retrieved context.
expect_skill: orq-build-evaluator
expect_tools: []
forbid_tools: [create_*, update_*, delete_*, invoke_*]
allow_tools: [search_entities, list_models, get_llm_eval]
runs: 5
pass_threshold: 0.8
max_turns: 8
```

**Factual rows** live in `tests/factual/<skill>.csv` with the header
`test_type,target,assertion,description`, one row per check, e.g.
`cli_flag,update,--check,orq update accepts --check` or
`sdk_import,evaluatorq.DataPoint,,DataPoint importable from evaluatorq`. The bootstrap
writes them; when pruning, delete whole rows.

## Retries

Each run gets at most one retry, and only for a failure a second attempt can clear:

- retried: a `harness` error (orq or the agent CLI exited without an agent error),
  and a `model` error that names a rate limit, 429, a 5xx, "overloaded",
  "temporarily" or "unavailable";
- not retried: any other `model` error (`empty_response` included: the same model
  and prompt tend to fail the same way), a `timeout`, a `tool` error, and a launch
  that never started (orq or the agent not found, prompt too long, container failed
  to start).

Both attempts are billed and counted in the run's `cost_usd` and `attempts`. If the
cost cap is reached before the retry, the first attempt's error is reported.

## Eval summary fields

The `--json` file `run_evals.py` writes. Top level: `cost_usd` (total spent),
`sessions` and `retries`, `models` (per agent: `model`, and `source` = `flag` or
`orq launch default`; `model` is null when the default could not be read),
`runs_skipped_by_cap`, `exit_code`, `experiment_url` (or `upload_error` with the retry command), `results_file`, `orq_version`, `agents`, `skills` (per skill: invocation and
behavioural pass counts, flaky cases, errors, cost), and `cases`.

Each entry of `cases`: `case`, `skill`, `kind`, `agent`, `status` (`pass`, `fail`,
`error`, `skipped` or `measured`), `pass_rate`, `threshold`, `flaky`, `errors`,
`errors_by_kind`, `cost_usd`, and `runs`. Each run: `run`, `status`, `scores` (per
scorer: `pass` and `why`), `error`, `error_kind` (`model`, `harness`, `timeout` or
`tool`; null when the run did not error), `thread_id`, `attempts`, `cost_usd`,
`stopped` (`done`, `max_turns` or `error`), `tool_calls` (names; `(denied)` when
refused; for an errored run, the calls it made before failing). A run whose scorer
crashed, or where no scorer gave a verdict, is `error`.

A case whose runs passed but where some errored or were cost-capped is `error`, not
`pass`: a pass on part of the runs is not a pass.

## Flag reference

Complete for each script; run all of them from the repo root.

**`uv run tests/scripts/run_evals.py`** (invocation and behavioural evals)

| Flag | Default | Effect |
|---|---|---|
| `--skill <name>` | all skills with cases | Only this skill. Repeatable. |
| `--case <id>` | all cases | Only this case id. Repeatable. The way to run one kind: pass the ids `--list` shows for it. |
| `--agent claude\|opencode` | both | Only this agent. Repeatable. |
| `--runs <n>` | each case's own (see `--list`) | Override runs per case for every selected case. At least 1. |
| `--pass-threshold <x>` | each case's own (see `--list`) | Override the pass threshold (0 to 1, the fraction of scored runs that must pass) for every selected case. Borderline cases ignore it. |
| `--claude-model <id>` | `orq launch` default (see `--list`) | Gateway model for Claude runs, as `provider/model_id` (Claude models only; not validated). |
| `--opencode-model <id>` | `orq launch` default (see `--list`) | Gateway model for OpenCode runs, as `provider/model_id`. |
| `--parallel <n>` | 2 | Concurrent agent runs; each is a full agent process. |
| `--max-cost-usd <x>` | 20.0 | Stop launching runs once this much is spent. |
| `--allow-stale-orq` | off | Run even when orq is older than 10.3.1. |
| `--list` | off | Print the selected cases, run count, models and thresholds, then exit. Spends nothing. |
| `--json <path>` | `tests/eval-results/<timestamp>.json` | Write the summary here. |
| `--no-send` | off (uploads) | Do not upload the experiment to orq. |
| `--container` | off (host) | Run each agent in a Docker container. |
| `--branch <path>` | this checkout | Plugin root under test, e.g. another worktree. |
| `--upload <file>` | | Retry a failed upload from saved results. Use alone: `run_evals.py --upload <file>`. |

There is no flag for the case kind; select a kind's cases with `--case`.

**`uv run tests/scripts/run_factual_tests.py`** (factual drift)

| Flag | Default | Effect |
|---|---|---|
| `--skill <name>` | all skills | One skill only. Not repeatable. |
| `--type <type>` | all types | One test type only: `cli_flag`, `cli_subcommand`, `doc_url`, `github_repo`, `mcp_tool_args`, `mcp_tool_exists`, `mcp_tool_param`, `npm_package`, `pypi_extra`, `pypi_package`, `sdk_import`, `sdk_method`. |
| `--json` | off (text) | JSON output on stdout; redirect it to a file for the report. |
| `--parallel <n>` | 4 | Max parallel workers. |

**`uv run tests/scripts/skill_test_report.py`** (merged report)

| Flag | Default | Effect |
|---|---|---|
| `--factual <file>` | none | `run_factual_tests.py --json` output. Optional. |
| `--evals <file>` | none | `run_evals.py --json` output. Optional. |
| `--json <path>` | none | Also write the merged report here. |

**`uv run --no-project python tests/scripts/bootstrap_factual_tests.py`** (factual rows for new tests)

| Flag | Default | Effect |
|---|---|---|
| `--skill <name>` | all skills | Process one skill only. |
| `--dry-run` | off | Preview rows without writing. |
| `--new` | off | Append rows missing from existing CSVs, keep the reviewed ones. |
| `--force` | off | Overwrite existing (reviewed) CSVs. Only when the user asks. |

**`node tests/scripts/validate-skills.mjs`** (structure and coverage checks)

| Flag | Default | Effect |
|---|---|---|
| `<path>` (positional) | this repo | Validate another repo-shaped tree. |
| `--fix` | off | Regenerate `skills-lock.json`. Only when the user asks. |
