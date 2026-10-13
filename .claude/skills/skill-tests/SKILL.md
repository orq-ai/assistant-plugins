---
name: skill-tests
description: Test orq skills in this checkout, or set up tests for a skill that has none. Runs factual drift checks for tools, CLI commands, SDK calls and URLs named in SKILL.md, plus invocation and behavioural evals in real Claude Code and OpenCode sessions through `orq launch`. Reports whether the right skill fired and whether its first actions followed the case, with the limits of what a pass shows. Scope, agents, models, repeats, thresholds and cost are chosen and confirmed before a run. New cases are drafted with the user and saved only after approval. Use when a maintainer asks to "test the skills", "run the skill evals", "add tests for a skill", "did my SKILL.md change break anything", or before merging a skill change. These tests stop at the first turn and cover skills/ only. Maintainer-only; lives in .claude/skills so it never ships to users.
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

Each script's `--help` lists every flag it accepts; do not pass a flag it does not
list. `run_evals.py` also takes `--upload <file>` alone, to retry a failed upload.

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
  show the rows, and let the user prune false positives (delete whole rows) before
  writing without `--dry-run` (`--new` adds missing rows to an existing CSV and keeps
  its review). It needs `ORQ_API_KEY` to confirm MCP tool names.
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
  it. Then check it loads with `uv run tests/scripts/run_evals.py --list --case <id>`
  (it stops with `invalid eval cases:` and the reasons if one is malformed), and that
  `node tests/scripts/validate-skills.mjs` no longer lists the skill in check 14.

Do not draft cases the user did not ask for.

## 3. Scope, setup and estimates

Agree the scope, then describe the setup of each suite with its estimate. Spend
nothing yet.

**Scope** (ask for what the user has not said):
- **Skills:** all, a selection, or one. The eval runner's `--skill` is repeatable; the
  factual runner's takes one skill. For a selection, run the factual suite on all
  skills (it is free and fast) and present only the selected ones.
- **Suites:** factual, invocation, behavioural, or any mix. The eval runner has no
  kind filter: to run one kind, pass that kind's case ids from `--list` with `--case`.
- **Near misses:** the cases under `_no-skill/` and `_general/` belong to no skill, so
  `--skill <name>` leaves them out. When filtering by skill, ask whether to add them
  with `--case`; they are what catches one skill stealing another's prompts.
- **Agents:** `claude`, `opencode`, or both (`--agent`).

**Factual setup:** one row per thing a SKILL.md names, in `tests/factual/<skill>.csv`,
checked against the live CLI, MCP server, package indexes and docs. Count the rows for
the selected skills (lines in their CSVs minus the header). Free, about a minute.

**Eval setup:** list the selected cases, the number of agent runs, the model per
agent, and each case's runs and threshold (load `.env` first, as in step 6):

```bash
uv run tests/scripts/run_evals.py --list [--skill <name>] [--case <id>] [--agent <agent>]
```

Each run is a fresh agent session with this checkout's skills and the orq MCP. Claude
runs also see Claude Code's bundled skills (dataviz, code-review, loop, ...), as every
real install does, and a prompt one of those claims fires no orq skill.
Take run counts, thresholds, cost and the cap from this output, not from memory.
Cases marked `(measured, not scored)` are borderline: they report a trigger rate and
ignore the threshold. `--list` prints an "about" (prompt mostly cached) and an "up
to" (nothing cached, as on DeepSeek and Gemini) cost per run and in total, priced
from the workspace catalogue assuming every run uses all its turns. Give both; actual
costs have landed between 0.4x the "about" figure and the "up to" figure. A model
missing from the catalogue (and Claude in container mode, where its model is
`unresolved`) is charged a flat $0.25 placeholder per run; say so. The worst case is
twice the bound, since a retried run bills both attempts (see [Retries](#retries)).
If `--list` warns the estimate exceeds the cap, raise the cap or cut the selection
before running. Wall time is roughly runs / `--parallel` (default 2) agent sessions.
Give the estimate per suite, then the total.

## 4. Confirm the run config

Before running anything, state the config and wait for the user to confirm it:

- **Repeats:** each case's runs as `--list` prints them, or one override for every
  selected case (`--runs <n>`).
- **Pass threshold:** each case's own, or one override for every selected case
  (`--pass-threshold <0..1>`). The override also loosens near misses that normally
  need every run to pass, so say which cases it changes; to give kinds different
  thresholds, run them separately with `--case`.
- **Coding agents:** which ones, and whether the run uses `--container`. Host mode
  requires an explicit `--unsafe-host` opt-in: agents can read local files outside
  the plugin checkout even when MCP tools are allowlisted.
- **Model per agent:** ask which model each selected agent runs on. The default is
  what `--list` prints as `(orq launch default)` (`unresolved` in container mode: the
  image's orq picks it); it is not pinned, so a later run may resolve another model.
  Any workspace catalogue model can be passed with `--claude-model` /
  `--opencode-model` as `provider/model_id`; to see the catalogue, read the models in
  the config `orq launch opencode --dry-run --no-mcp --no-skills` prints. Offer Claude
  Code only Claude models; a wrong one fails as a run error. Lite and nano tiers tend
  to fail as models before they reach the skill decision, so recommend a flash, mini
  or haiku tier as the floor, but the choice is the user's.
- **Cost cap and upload:** the cap (`--max-cost-usd`), and whether results upload to
  orq (`--no-send` skips it).

`--list` with the same flags prints the resulting models, runs and thresholds without
spending anything. The factual suite needs no confirmation when it is the only suite run.

## 5. Preconditions

- `orq --version` is 10.3.1 or newer; if not, tell the user to run `orq update`
  first. A stale CLI reports false drift, and the eval runner refuses to start on
  one unless `--allow-stale-orq` is passed.
- `ORQ_API_KEY` is set, in the environment or a gitignored `.env` at the repo root.
  The eval runner refuses to start without it, or when an active `orq auth profile`
  on the host would override it (`orq auth profile clear` fixes that); the factual suite skips
  its MCP checks without it. Every command in this skill loads `.env` first.
- The key's workspace needs a `skill-evals` project (the experiment lands there) and
  the fixture agent `support-bot` the `orq-improve-agent` cases read; without it,
  `improve-agent-reads-config-first` comes back as `tool` errors, not a regression.
  Check with `orq agents retrieve support-bot`. To recreate it: model
  `anthropic/claude-haiku-4-5`, role "Customer support agent", `max_iterations` 2, no
  tools, and instructions that demand three separate steps per request and an answer
  of at least 800 words, so the fault shows in the config alone.
- On Windows, set `SSL_VERIFY=0` if Python's TLS setup aborts (`OPENSSL_Applink`) or
  TLS interception breaks verification. The eval runner saves results before
  uploading and prints the `--upload <file>` command if the upload fails.
- **Container mode (required unless the user explicitly accepts unsafe host access):**
  `--container` runs each agent in a Docker container that sees only its own
  workdir. It needs Docker and the evaluatorq image, built once per evaluatorq
  version with `uv run --with evaluatorq==<pinned version> eq coding-agent build-image`
  (the runner prints the exact command if the image is missing). Ask before
  building: a few minutes and about 2 GB. Behind TLS-intercepting antivirus,
  add its root CA to a copy of the Dockerfile and set `NODE_EXTRA_CA_CERTS`.

## 6. Run

Run the confirmed suites from the repo root with the Bash tool (not PowerShell: its
`>` writes UTF-16), and write the JSON outputs to `tests/eval-results/` (gitignored).
Each Bash call starts a fresh shell, so load `.env` in the same call as the runner:

```bash
[ -f .env ] && { set -a; . ./.env; set +a; }
mkdir -p tests/eval-results
uv run tests/scripts/run_factual_tests.py --json [--skill <name>] > tests/eval-results/factual.json
uv run tests/scripts/run_evals.py --container --json tests/eval-results/evals.json [--skill <name>] [--case <id>] [--agent <agent>] [--runs <n>] [--pass-threshold <x>] [--claude-model <id>] [--opencode-model <id>]
uv run tests/scripts/skill_test_report.py [--factual tests/eval-results/factual.json] [--evals tests/eval-results/evals.json]
```

Run the eval run in the background and report when it finishes. A non-zero exit code
from a runner is a result, not a failure of this skill: go on to the report.

- `run_evals.py`: 0 all cases passed; 1 a case failed; 2 anything else (a case
  errored, the cap stopped runs, or it never ran). If it stops before running
  (`invalid eval cases:`, a missing key, orq binary or Docker image, a stale orq),
  there is no summary: report the stderr instead of running the merger.
- `run_factual_tests.py`: 0 all rows passed or skipped; 1 a row failed or errored, or
  no rows loaded.
- `skill_test_report.py`: 1 drift or a regression; 2 none, but something errored or
  was skipped; else 0.

The summary JSON's fields are listed in the `run_evals.py` docstring, for a detail the
printed report leaves out.

## 7. Present

Show the merged report per skill, in its own buckets:

- **drift**: a factual check failed; the skill names something that no longer exists. Point to the SKILL.md line the report gives.
- **regression**: an eval case fell below its pass threshold. Name the failing scorer and give its explanation; for an attempted forbidden call that includes the arguments.
- **flaky**: passed some runs, failed others. Flag it; do not call it a pass.
- **error**: a run could not complete, or the cost cap stopped some of a case's runs. Not a verdict on the skill. Group by `error_kind`:
  - `model`: the gateway or model failed (empty_response, rate limit, provider 5xx).
  - `harness`: orq or the agent CLI exited without an agent error.
  - `timeout`: the agent was killed at the time limit.
  - `tool`: the orq server failed an expected tool call.

  If most of a case's runs are `model` errors, say the skill is **untested** on that model and suggest a stronger one. Never call the skill failing.
- **measured**: borderline cases, reported as a trigger rate only.
- **advisory**: a doc URL the skill links failed. Non-gating; mention it.
- **skipped**: an eval case with no scored run, usually because of the cost cap. It measured nothing; do not call the skill clean.
- **factual skipped**: rows that could not run (usually no `ORQ_API_KEY`). A skill with skipped rows is not clean.

Include the orq experiment link, the total cost with its sessions and retries, and the
model each agent ran on. The total is what orq traced per run; name how many runs
kept an estimate instead (traces unreadable, or a retried Claude run), since those
are not actual costs. For every failing or errored run give its thread id, so the
user can open the trace (`orq traces search --query <id>`): a Claude run's session id,
or the `skill-evals-<case>-opencode-<run>-<time>` id an OpenCode run sends as
`X-ORQ-THREAD-ID`.

End with what a pass does and does not show, so nobody reads it as more:

- **Shows:** the prompt routed to the right skill (or, for a near miss, to none), the
  expected tools were called by name within `max_turns`, and no forbidden tool was
  attempted, on the model it ran on.
- **Does not show:**
  - Anything after the first user message: a skill that asks a question is never answered.
  - Whether the agent asked or just stopped: a silent stop, or a cut-off at
    `max_turns` after read-only calls, passes an "asks first" case too.
  - Tool arguments, order or count, or the reply text; only tool names are scored.
  - CLI and script driven work: shell and file writes are denied, so those skills are
    tested for routing only.
  - Forbidden calls on OpenCode: it hides denied tools, so attempts are only
    observable on Claude.

Closing these gaps is tracked in RES-1648.

## 8. Dig deeper (only when the user asks)

After the report, offer once to inspect the traces of failing, flaky, measured or
errored runs. If the user wants it, read each run's trace by thread id (the `orq-cli`
skill has the trace commands; project the fields you need rather than dumping spans)
and describe which skill loaded and when, what the agent said, the tool calls with
their arguments, and whether it ended its turn or hit `max_turns`. These are
observations next to the scored result; never re-score a case from a trace.

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
| `expect_tools` | no | `[]` | Behavioural: every one must be called, and succeed, within `max_turns`. Each must also be in `allow_tools`. |
| `allow_tools` | no | `[]` | orq tools the agent may run. Everything else from orq is denied; Claude also always gets `Read`, `Glob`, `Grep` and `Skill`. |
| `forbid_tools` | no | `[create_*, update_*, delete_*, invoke_*]` | Glob patterns; attempting one fails a behavioural case, denied or not. Must not overlap `allow_tools`. |
| `runs` | no | 5 | Runs per agent. Invocation cases usually set 3. |
| `pass_threshold` | no | 0.8 | Fraction of scored runs that must pass. Invocation cases usually set 0.66; near misses 1.0. |
| `max_turns` | no | 6 | Agent turns before the run is stopped (Claude `--max-turns`; OpenCode's agent `steps`). Invocation cases usually set 2. |
| `borderline` | no | `false` | `true` makes the case measured only: a trigger rate, no pass or fail. |

Any other field, or a field of the wrong type, is rejected. An invocation case passes
when the first orq skill that fires is `expect_skill` (or, for `none`, when none
fires). A behavioural case passes when the right skill fired first (skipped for
`any`), every `expect_tools` entry was called without erroring, and no `forbid_tools`
pattern was attempted and no orq tool outside `allow_tools` ran.

```yaml
# tests/evals/orq-build-evaluator/build-evaluator-fires.yaml
id: build-evaluator-fires
skill: orq-build-evaluator
kind: invocation
prompt: I need an automated pass/fail check on my orq agent's replies for whether it stays polite. Can you set up a judge for that?
expect_skill: orq-build-evaluator
runs: 3
pass_threshold: 0.66  # 2 of 3
max_turns: 2
```

```yaml
# tests/evals/orq-build-evaluator/build-evaluator-asks-first.yaml
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

## Retries

A run is retried at most once, and only for a `harness` error or a `model` error
naming a rate limit, 429, a 5xx, "overloaded", "temporarily" or "unavailable". Other
errors (`empty_response`, `timeout`, `tool`, a launch that never started) replay the
same way and are not retried. Both attempts are billed and counted in the run's
`cost_usd` and `attempts`. The cap reserves each pending run's upper estimate
before launch, then reconciles it with the agent's own cost where trustworthy
(Claude Code on Anthropic models) or the estimate elsewhere. A run that costs
more than its reservation can exceed the cap; it is flagged and no later run
starts. The summary replaces estimates with traced costs when available.
