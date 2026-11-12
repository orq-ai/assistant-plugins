---
name: orq-grade-skill
description: >
  Produce a frozen baseline grading of one repository-tracked agent skill from
  real sessions that loaded it, with raw scores, usage patterns and
  evidence-backed filing decisions, without proposing edits. Sessions come from
  Orq traces tagged with a metadata key (default), local Claude Code sessions, or
  ATIF trajectory files; grader subagents score them in batches and a script
  freezes the baseline and an HTML report. Use when the user asks how well a
  skill works in practice, whether agents follow it, or which of its
  instructions fail. Do NOT use to edit or rewrite a skill (use orq-create-skill),
  to grade an orq agent's own outputs (use orq-analyze-traces), or to run an
  experiment against a dataset (use orq-run-experiment).
allowed-tools: Read, Grep, Glob, Task, Bash(orq traces query-oql:*), Bash(orq traces get-conversation:*)
---

# Grade a skill

You, the main agent, run these steps in order (collect, prepare, grade,
baseline) and stop there. Scripts validate every handoff; subagents do the
judging. Never write a subagent's output file yourself. Report `RUN/baseline.md`,
its top findings and the collection limitations. Do not write issues, edits or a diff.

`SKILL_DIR` below is the absolute path of this skill's directory. Run the scripts
by absolute path from any directory. They declare their dependencies inline
(PEP 723), so `uv run SKILL_DIR/scripts/<x>.py` works in any checkout. Without
`uv`, install the dependencies once with `pip install "evaluatorq>=1.87" "pydantic>=2"`
(Python 3.12 or later) and run `python3 SKILL_DIR/scripts/<x>.py` with the same
arguments. `RUN` is the absolute run directory printed by step 2.
Subagents run elsewhere, so pass them absolute paths.

The process is a strict sequence of stages. Each stage's script freezes the
files the previous stage wrote, so a stage may start only after every subagent
of the previous stage has finished and its output exists. A subagent that is
still running is not finished: wait for it to end before you run the next
script, and never message, re-spawn or replace a subagent while it runs. If a
script reports that its inputs changed after they were frozen, the run is
failed; report it rather than working around it.

Spawn subagents with whatever subagent facility your harness provides, in the
simplest form it accepts. A subagent's prompt is its role file from `RUN/agents/`
followed by the absolute paths it needs; state its name and role in that prompt.
Children already have their tools; do not configure them. If spawning fails,
simplify the call once; if it fails again, stop and report a failed run.
You alone run the `collect.py`/`pipeline.py` stages; children never do.
Every child assignment must contain resolved absolute paths and the actual
`run_id` from `RUN/input.json`. Children verify that ID; a missing path is an
explicit failure, never permission to search another run. Use role-prefixed child
names (`grader-`) for measured accounting.

## Tag the sessions to grade

The default source is Orq traces. Tag every agent run you want graded with one
metadata pair, then collect by that pair:

- Coding agents launched through the Orq router: `orq launch claude --metadata skill-grading=my-skill`
  (the same `--metadata key=value` flag works for the other `orq launch` agents).
- Your own calls to the Orq router: send the request header
  `X-ORQ-METADATA-skill-grading: my-skill`, or put the pair in the request
  body's `metadata` ([request metadata](https://docs.orq.ai/docs/ai-gateway/request-metadata)).

The filter is an [OQL](https://docs.orq.ai/ai-studio/observability/oql) query,
`fetch traces | filter metadata.<key> == "<value>"`. A session only counts when
its trace shows the agent loading the exact committed version of the skill (see
`contracts/NOTES.md`, "Load detection per harness").

## Steps

1. **Collect** loaded sessions of the selected skill (`--skill` is repo-relative):
   `uv run SKILL_DIR/scripts/collect.py --repo <client-repo> --skill <skill> --out <dir>`
   plus one source: `--trace-metadata <key>=<value>` (Orq traces, default window
   `--since 7d --until now`; needs the `orq` CLI logged in),
   `--claude-projects ~/.claude/projects --loaded-from <skill-dir>`
   (Claude Code), `--atif <file>...` (ATIF trajectories), or
   `--responses <index.json>` (Orq `orq traces get-conversation` exports).
   Output: `<dir>/sessions.json` (ATIF sessions), `<dir>/collection.json`; the
   traces source also keeps its exports and index in `<dir>-traces/`.
   For other harnesses convert to ATIF first (see `contracts/NOTES.md`).
2. **Prepare**:
   `uv run SKILL_DIR/scripts/pipeline.py prepare --repo <client-repo> --skill <skill> --sessions <dir>/sessions.json --run-id <id>`
   (add `--comparisons <no-skill.json>` for verified no-skill sessions,
   `--tool-max-chars N` to change the 500-character tool output per call,
   `--batch-size N` to change the 8 sessions per grader, and `--passes K` for K
   repeated grading passes over reshuffled batches).
   Prints `RUN`. The source-session minimum defaults to 10. A prospectively
   declared low-data study may explicitly use `--source-session-minimum 1`;
   preparation freezes this choice in the manifest and source policy. If
   `RUN/outcome.json` says `insufficient_data`, stop and report it. One observed
   episode is not evidence of recurrence. Finding validation, exact citations
   and contradiction checks remain unchanged in either regime.
3. **Grade**: for every batch of every pass in `RUN/grade-plan.json`
   (`passes[].batches[]`), spawn one subagent with `RUN/agents/grader.md`, RUN,
   SKILL_DIR, the batch id, `RUN/<output>` and each `RUN/<transcript>`. Run them in
   parallel and wait for all.
4. **Baseline**: `uv run SKILL_DIR/scripts/pipeline.py baseline --run RUN`. On failure it
   lists every failing batch; re-spawn just those graders with their error, at
   most twice. With K passes, a target is filed only when most passes filed it.
   Output: `RUN/baseline.json`, `RUN/baseline.md`, `RUN/baseline.html` (the
   grading report; rebuild with `uv run SKILL_DIR/scripts/pipeline.py report --run RUN`). A frozen
   baseline is never regraded; use a new run id.

Report the paths to `baseline.html` and `baseline.md`, the top filed issues and
the collection limitations. A step that fails or an output it should have
written that is missing is a failed run. Do not edit, commit or publish client skill files.
