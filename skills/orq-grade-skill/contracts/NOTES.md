# Maintainer notes

## Normalized session (collect.py output, pipeline.py input)

```json
{"id": "actual-session-id", "skill_hash": "sha256 of the client SKILL.md",
 "cwd": "or null", "loads": [{"step": 3, "skill": ".agents/skills/my-skill"}],
 "trajectory": {"schema_version": "ATIF-v1.7", "steps": [...], ...}}
```

`trajectory` is an ATIF trajectory, validated with evaluatorq's
`AtifTrajectory.from_json`; `loads` are its 1-based `step_id`s. Nothing is
truncated. A pointer `{session_id, step, quote}` resolves against the step text:
its message, each call as `name: <JSON input>` plus its raw string inputs, and
its observation results, joined by newlines (`loop.step_text`). No-skill
comparisons carry `skill_usage_verified: true`, a trajectory and no
`loads`/`skill_hash`. Session ids are real session ids, not router request
trace ids, and the cohort needs at least 10 sessions at one skill version.

## Load detection per harness

Detection runs on ATIF steps (`collect.Target.loads`). A name mention, catalog
listing or launch acknowledgement is never a load. The loaded text must contain
the client's SKILL.md without frontmatter, byte for byte; paths are not checked,
so the text is the version identity.

- **Orq traces** (`collect.py --trace-metadata KEY=VALUE [--since 7d --until now]`):
  runs `orq traces query-oql` with `fetch traces | filter metadata.KEY == "VALUE"`,
  groups traces into sessions and keeps the latest trace of each, exports it
  with `orq traces get-conversation <trace-id> -o json` into `<out>-traces/`
  with an `index.json`, and collects that index as below. The client repo stands
  in for `repo_root`, which traces do not record. Shapes captured from real
  tagged sessions (fixtures in `tests/fixtures/traces/`):
  - Claude Code (`orq launch claude --metadata`): one trace per model call, all
    with Claude Code's `session_id`. Load: `Skill` call with `{"skill": name}`,
    output `Launching skill: <name>`, then a user message
    `Base directory for this skill: <dir>\n\n<body>`.
  - Codex (`orq launch codex --metadata`): `session_id` is the trace id; calls
    share `openresponses.prompt_cache_key`, which also covers Codex's side
    calls. Load: `custom_tool_call` `exec` whose JavaScript input runs
    `tools.exec_command({cmd: "cat|sed ... <name>/SKILL.md"})`, the file text in
    its `custom_tool_call_output`. `responses_to_atif` keeps these items only in
    step extras, so `collect.function_items` turns them into function calls
    (`{"script": input}`) first.
  - Orq agents (`orq responses create --model agent/<key> --metadata`): one
    trace per run. Load: `read_skill` `{"name": name}`, output the body as a
    JSON string.
  - storq (`storq --metadata '{"KEY":"VALUE"}'`): one trace per call, chained by
    `openresponses.previous_response_id`. Load: `Read`
    `{"file_path": ".../.storq/plugins/<thread>/<bundle>/skills/<name>/SKILL.md"}`,
    output as `cat -n` lines.

  Session key: `session_id` when it differs from the trace id, else `thread_id`,
  else `prompt_cache_key`, else the first response of the `previous_response_id`
  chain, else the trace id.
- **Orq** (`collect.py --responses <index>`): index entries are
  `{trace_id, session_id, repo_root, conversation_file}`, the file holding
  `orq traces get-conversation <trace-id> -o json`; evaluatorq's
  `responses_to_atif` converts it. A load is native `read_skill` with `name`
  equal to the skill dir, or any call whose arguments name `<skill>/SKILL.md`
  (`Read`, `read`, a shell command), paired with its result by call id; a
  `Skill` call is paired with the user message right after it. `read_skill`
  results arrive JSON-string encoded, `Read` results as `cat -n` lines. Spans carry
  `gen_ai.skill.{id,name,version}`; combined id+version OQL filters have
  returned nothing where single-attribute filters matched, so discover by
  identity and check the version on the span.
- **Claude Code** (`collect.py --claude-projects --loaded-from`): JSONL rows of
  one assistant message merge into one agent step; tool results attach to the
  step holding their call. A load is a `Skill` call with `input.skill` plus a
  meta user step linked by `source_tool_use_id` holding
  `Base directory for this skill: <dir>\n\n<body>`, optionally followed by
  `\n\nARGUMENTS: ...`; `<dir>` must equal `--loaded-from`. Not handled:
  `/skill` direct runs, preloaded subagent skills, `subagents/*.jsonl` and
  thinking blocks.
- **ATIF files** (`collect.py --atif <file>...`): taken as-is; session id from
  `session_id`, cwd from `extra.cwd`. All detectors above apply.
- **Codex local files**: no adapter for `~/.codex/sessions/**/*.jsonl`; collect
  Codex through Orq traces. Full output of the read is required.

## Run directory

`<client>/.orq/skill-loop/runs/<run-id>/<skill>/`. Scripts write `input.json`
(hashes of every snapshot), `original/`, `sessions.json`, `rules.json`, `grade-plan.json`,
`grading/sessions/*.md`, `baseline.{json,md,html}` and, for too few sessions,
`outcome.json`. Graders write `grading/pass-N/batch-NNN.json`. A changed snapshot fails every later step.
JSON Schemas here are generated by `uv run python contracts.py`.

## Running in other harnesses

Grading runs in one harness whose main agent spawns native subagents. What
differs between harnesses:

- Spawn and wait APIs differ: Claude Code `Agent`, Codex
  `spawn_agent`/`wait_agent`, omp `task`. A spawn can fail outright (Codex:
  `collab spawn failed: no thread with id`).
- The subagent model is not always selectable, so graders may run on whatever
  the harness picks; record it when it is known.
- Concurrency caps differ; a batch of graders may run partly serially.
- Subagent cwd, sandbox and write permissions differ. Graders must write their
  batch file, so pass absolute paths and check the file exists after the wait.
- The main agent may do a subagent's work itself; SKILL.md forbids writing a
  subagent's output file, and only the file a subagent wrote counts.
- The main agent's context grows with every batch it waits on; keep replies to
  a path, not the grades.
- Headless runs stall on approval prompts unless approvals are bypassed
  (`codex exec --dangerously-bypass-approvals-and-sandbox`), and MCP servers
  that need auth log errors without stopping the run.
- Each harness stores sessions differently and needs an ATIF adapter plus
  per-harness skill-load detection (above); Codex has none yet.
- Codex trims its skills list when many skills are installed ("Exceeded skills
  context budget. All skill descriptions were removed ..."), so the grading
  skill may not be listed; name the SKILL.md path in the prompt.

Rule: scripts validate and prepare files. They never call a harness or model
themselves, unless the LLM passes the parameters for that call explicitly (no
hidden model calls). Today `collect.py`, `pipeline.py` and `loop.py` call only
`git` (`ls-files`, `status`, `rev-parse`) and, for `--trace-metadata`, the
read-only `orq traces query-oql` and `orq traces get-conversation`.

## Versus Warp's skill-doctor

Same rubric shape (efficiency, quality, skill compliance) and file/don't-file
findings with reusable rules, and the transcripts follow its condensed view.
We differ in two ways: we bind every finding to one exact skill version, and we
freeze the baseline with hashes of every input. We drop its score curve,
basename repo matching and multi-skill sampling. Not built here: a
setup-wide skill inventory, edit proposals and new-skill proposals.
