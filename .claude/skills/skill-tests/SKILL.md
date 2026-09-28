---
name: skill-tests
description: Run the orq skills' tests for this branch of assistant-plugins and report per skill — factual drift checks (tests/factual) plus invocation and behavioural evals (tests/evals) that launch real Claude Code / OpenCode sessions through `orq launch`. Use when a maintainer asks to "test the skills", "run the skill evals", "did my SKILL.md change break anything", or before merging a skill change. Maintainer-only; lives in .claude/skills so it never ships to users.
metadata:
  # Boolean on purpose: `npx skills` skips a skill only on `internal === true`, which keeps
  # this out of every install (CI install-sanity checks the installed set).
  internal: true
---

# skill-tests

Runs the two test suites for the skills in this checkout and merges them into one
report per skill. The scripts do the work and the scoring; this skill confirms scope
and cost, launches them, and presents the merged report as written. Do not
re-score, re-interpret or soften a result.

| Suite | Script | What it answers | Cost |
|---|---|---|---|
| Factual | `tests/scripts/run_factual_tests.py` | Do the tools, CLI commands, SDK calls and URLs a skill names still exist? | free, ~1 min |
| Evals | `tests/scripts/run_evals.py` | Does the right skill fire, and does the agent take the right first actions without creating anything? | Claude ~$0.11 per run; OpenCode reports no cost and is charged $0.25 |

## 1. Confirm scope and cost

Ask which skills (default: all with cases) and which agents (`claude`, `opencode`;
default both). Then show the plan without spending anything:

```bash
uv run tests/scripts/run_evals.py --list [--skill <name>] [--agent claude]
```

It prints the case count and the number of agent runs. Estimate cost as runs x
$0.25 (about twice Claude's average, the flat charge for OpenCode) and state it, with the
default cap of $20 (`--max-cost-usd`); a full run of every case on both agents is
72 runs. Wait for the user to confirm before running the evals. The factual suite
needs no confirmation.

The invocation cases (`*-fires`) run 3 times with a 2-of-3 threshold: a smoke
check that catches a skill that stopped firing, not one that fires unreliably. Say
so when presenting them, and point to the flaky bucket for mixed results.

## 2. Preconditions

- `orq --version` is 10.3.1 or newer; if not, tell the user to run `orq update`
  first. A stale CLI reports false drift.
- `ORQ_SKILL_EVALS_KEY` is set (environment or a gitignored `.env` at the repo
  root). It is the `skill-evals` project key; the eval runner refuses to start
  without it. Never substitute another key.
  The experiment upload uses the same key; pass `--no-send` to skip it.
- `ORQ_API_KEY` is set for the factual suite's MCP checks.
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

## 3. Run

Run both from the repo root with the Bash tool (not PowerShell: its `>` writes
UTF-16), and write the JSON outputs to `tests/eval-results/` (gitignored):

```bash
mkdir -p tests/eval-results
uv run tests/scripts/run_factual_tests.py --json [--skill <name>] > tests/eval-results/factual.json
uv run tests/scripts/run_evals.py --json tests/eval-results/evals.json [--skill <name>] [--agent <agent>]
uv run tests/scripts/skill_test_report.py --factual tests/eval-results/factual.json --evals tests/eval-results/evals.json
```

The eval run takes minutes (each run is a full agent session); run it in the
background and report when it finishes. A non-zero exit code from a runner is a
result, not a failure of this skill: go on to the report.

## 4. Present

Show the merged report per skill, in its own buckets:

- **drift**: a factual check failed; the skill names something that no longer exists. Point to the SKILL.md line the report gives.
- **regression**: an eval case fell below its pass threshold. Name the failing scorer and, for an attempted forbidden call, the arguments it was called with.
- **flaky**: passed some runs, failed others. Flag it; do not call it a pass.
- **error**: a run could not complete (timeout, agent crash, an expected orq tool that errored server side), or the cost cap stopped some of a case's runs. Not a verdict on the skill.
- **measured**: borderline cases, reported as a trigger rate only.
- **skipped**: an eval case with no scored run, usually because the cost cap stopped it. It measured nothing; say so, do not call the skill clean.
- **factual skipped**: rows that could not run (usually no `ORQ_API_KEY`). Say how many; a skill with skipped rows is not clean.

Include the orq experiment link and the total cost. For a failing eval run, give
its thread id so the user can open the trace (`orq traces search --query <id>`).

## 5. Skills without cases

The coverage check (`node tests/scripts/validate-skills.mjs`, check 14) lists skills
without a `tests/evals/<skill>/` folder. Cases are hand-written: see an existing
case in `tests/evals/` for the format. Do not write cases on your own initiative.
If the user asks for help bootstrapping, draft candidate prompts in the
conversation only (a positive invocation prompt, a near-miss that should not fire
it, the first actions the skill's own steps call for); the user edits and approves
every case before it is saved.
