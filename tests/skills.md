# Skill Smoke Tests

Conversational tests for each skill. Trigger each with a simple scenario and verify the first phase responds correctly. **No pre-existing resources are modified.**

Requires `setup.md` to have run first (seed data for `orq-run-experiment` test).

---

## `create-skill`

### Scenario 1: New CLI skill from scratch

- Ask: "Create a skill for the `gh` CLI"
- Verify Phase 1: round 1 asks create-vs-update; round 2 asks for the surface (confirms `gh`) and scope mode (fast/thorough) in one call
- Verify Phase 2: runs `gh --help` and subcommand help to build the inventory
- Verify Phase 3: searches `skills/*/SKILL.md` for existing `gh` skills
- Verify: writes the inventory to `.create-skill/<surface>.md`, not into `skills/` and not directly into the skill

### Scenario 2: Update existing skill

- Provide: a skill directory with an existing SKILL.md that documents an API
- Ask: "Update the skill, the API added new endpoints"
- Verify Phase 3: finds the existing skill, diffs inventory against it
- Verify: scopes Phase 4 testing to only the new/changed operations
- Verify: uses `Edit` on the existing file rather than overwriting

### Scenario 3: Untestable operations

- Provide: an API surface where auth is unavailable
- Ask: "Create a skill for this API (thorough)"
- Verify Phase 4: marks untestable operations as `[unverified: <reason>]`
- Verify: reports the count of unverified operations before proceeding
- Verify Phase 5: carries `[unverified]` markings into the output skill

### Scenario 4: Adversarial review catches an untested claim

- Provide: a surface whose docs describe a response field the live surface does not return
- Ask: "Create a skill for this (thorough)"
- Verify Phase 5: the adversarial review flags the doc-only claim, and the agent either re-tests it or marks it `[unverified]` — the claim never ships unmarked
- Verify: no example in the draft is invented; each traces to a recorded call in the scratchpad

### Scenario 5: Registration and cleanup

- Run inside this repo
- Verify Phase 6: updates `agents/AGENTS.md`, the `README.md` table, `tests/skills.md`, `skills-lock.json`, and bumps all four `plugin.json` manifests plus `CHANGELOG.md`
- Verify Phase 6: `node tests/scripts/validate-skills.mjs` passes afterwards
- Verify Phase 7: `.create-skill/` no longer exists

---

## `orq-setup-observability`

### Scenario 1: Python OpenAI app — AI Router path

- Provide: a small Python file using `openai.OpenAI()` with no existing tracing
- Ask: "Add orq.ai tracing to my app"
- Verify Phase 1: scans the project, identifies OpenAI SDK, reports no existing tracing
- Verify Phase 2: recommends **AI Router** mode (framework supports it, fastest path)
- Verify Phase 3: changes `base_url` to `https://api.orq.ai/v2/router`, uses `provider/model` format (e.g., `openai/gpt-4o`)
- Verify: does NOT use `from orq_ai_sdk.tracing import traced` (wrong import path)
- Verify: does NOT hardcode `service.name=my-app`

### Scenario 2: LangChain app — Observability path

- Provide: a Python file using `langchain_openai.ChatOpenAI()` calling a provider directly
- Ask: "I want to add tracing but keep my existing LLM calls"
- Verify Phase 2: recommends **Observability** mode (user wants to keep existing calls)
- Verify Phase 3: sets OTEL env vars, installs OpenInference instrumentor
- Verify: instrumentor is initialized BEFORE framework client creation
- Verify: warns about existing OTEL config if any `OTEL_*` vars already exist

### Scenario 3: Verify code correctness

- Ask: "Show me how to use the @traced decorator"
- Verify: import path is `from orq_ai_sdk.traced import traced` or `from orq_ai_sdk import traced`
- Verify: parameters shown are `name`, `type`, `capture_input`, `capture_output`, `attributes`
- Verify: does NOT show `user_id` as a direct `@traced` parameter (should be in `attributes={}`)
- Verify: does NOT use `orq_traced_input()` or `orq_traced_output()` (these don't exist)
- Verify: `capture_input` / `capture_output` defaults documented as `True`

### Scenario 4: Sensitive data handling

- Provide: a Python function that takes `card_number` and `user_email` as arguments
- Ask: "Add tracing to this function"
- Verify: uses `capture_input=False` and/or `capture_output=False`
- Verify: explains that defaults are `True` (all inputs/outputs sent to orq.ai unless disabled)

### Scenario 5: Existing OTEL configuration

- Provide: a project with existing `OTEL_EXPORTER_OTLP_ENDPOINT` pointing to Datadog
- Ask: "Add orq.ai observability"
- Verify: detects existing OTEL configuration in Phase 1
- Verify: warns about overwriting before setting new env vars
- Verify: asks user for confirmation before proceeding

---

## `orq-invoke-deployment`

### Scenario 1: Deployment invocation (happy path)

- Ask: "Invoke my deployment `customer-support` with variable `customer_name` set to 'Jane'"
- Verify Phase 1: uses `search_entities` to browse, then verifies the key with the run key via the [run-key preflight](../docs/run-key-preflight.md)
- Verify Phase 2: identifies `{{customer_name}}` as a required input, maps it
- Verify Phase 3: generates Python SDK code using `client.deployments.invoke(key=..., inputs={...})`
- Verify: code uses `os.environ["ORQ_API_KEY"]`, never hardcodes the key
- Verify: code includes `identity={"id": ...}`

### Scenario 2: Agent invocation with multi-turn

- Ask: "Send a message to my agent and then follow up"
- Verify: uses `client.agents.responses.create()`, NOT `client.agents.invoke()`
- Verify: message format uses A2A parts structure (`parts: [{kind: "text", text: ...}]`), not OpenAI-style `content`
- Verify: saves `task_id` from first response and passes it in the follow-up call

### Scenario 3: Model (AI Router) invocation

- Ask: "Call GPT-4.1 directly through the AI Router"
- Verify: uses `provider/model` format (e.g., `openai/gpt-4.1`), not bare model name
- Verify: points OpenAI client at `base_url="https://api.orq.ai/v2/router"`
- Verify: does NOT use orq SDK for this path — uses `openai.OpenAI()`

### Scenario 4: Streaming recommendation

- Ask: "I need to call a deployment for a chatbot UI"
- Verify: recommends `stream=True` for user-facing invocations

---

## `evaluatorq`

### Scenario 1: Write a Python evaluation script

- Ask: "Help me evaluate my agent `my-support-agent` using evaluatorq"
- Verify Phase 1: asks for agent key, browses via `search_entities`, then verifies with the run key via the [run-key preflight](../docs/run-key-preflight.md)
- Verify Phase 3: generates a Python script with `@job`, `DataPoint`, `evaluatorq()`, and `await` call
- Verify: uses `orq.evals.invoke_async()` (inside async scorers) or `orq.evals.invoke()` (sync), never `orq.evaluators.invoke()`
- Verify: suggests `dataset_id` if dataset exists, inline only for quick tests

### Scenario 2: Run red team from CLI

- Ask: "Run a red team test on my agent using the evaluatorq CLI"
- Verify: shows `eq redteam run --target agent:<KEY> --mode dynamic` (not `run adaptive` or `--framework`)
- Verify: explains that the 5 detail files (`01_agent_context.json` … `05_summary_report.json`) come from `--save detail`
- Verify: shows `eq redteam ui report.json` to view results (not `eq redteam report summarize`)

### Scenario 3: Run simulation from CLI

- Ask: "Use eq sim to simulate users talking to my agent"
- Verify: shows `eq sim generate` with `--agent-description` and target flag
- Verify: explains `--num-personas`, `--num-scenarios`, `--max-turns`
- Verify: mentions exactly one of `--agent-key`, `--openai-model`, `--vercel-url` is required

### Scenario 4: Routing — not evaluatorq's job

- Ask: "Compare my two agents against each other"
- Verify: routes to `orq-compare-agents`, not generates a script here

---

## `orq-compare-agents`

### Scenario 1: orq.ai vs orq.ai comparison

- Ask: "Compare my two orq.ai agents `agent-gpt4o` and `agent-claude` head-to-head"
- Verify Phase 1: identifies both agents, browses via `search_entities`, then verifies each key with the run key via the [run-key preflight](../docs/run-key-preflight.md)
- Verify Phase 4: generates evaluatorq script with two `@job` functions, each using `agents.responses.create()` (not `agents.invoke()`)
- Verify: script uses `from orq_ai_sdk import Orq` (not `from orq import ...`)
- Verify: both jobs use the same evaluator for fair comparison

### Scenario 2: External vs orq.ai comparison

- Ask: "Compare my LangGraph agent against my orq.ai agent"
- Verify: generates one LangGraph job pattern and one orq.ai job pattern
- Verify: delegates dataset creation to `orq-generate-synthetic-dataset` (does not create inline)
- Verify: delegates evaluator creation to `orq-build-evaluator` (does not design from scratch)

---

## `orq-build-agent`

- Ask: "Build a simple FAQ agent for a pizza restaurant"
- Verify: asks clarifying questions about purpose, users, success criteria
- Verify: calls `list_models` when selecting model

## `orq-build-evaluator`

- Ask: "Build an evaluator that checks if output is valid JSON"
- Verify: recommends code-based evaluator (`create_python_eval`), not LLM judge
- Ask: "Build an evaluator for tone and helpfulness"
- Verify: suggests splitting into separate evaluators (one per criterion)
- Ask: "Build an evaluator that classifies tone as professional, casual or aggressive"
- Verify: picks `categorical`, and creates it with `POST /v2/evaluators` carrying `categorical_labels` — never `create_llm_eval`, which cannot supply the labels the API requires at creation
- Verify: the labels it proposes are mutually exclusive and include a catch-all
- Verify: validation targets per-label precision/recall, NOT TPR/TNR (undefined for 3+ labels), and does not offer the prevalence-correction formula
- Ask: "Write me a judge prompt for persona consistency"
- Verify: the prompt uses `{{input.*}}` / `{{output.*}}` variables, not `{{log.*}}` and not bare `{{input}}` / `{{output}}`
- Verify: after creating an LLM evaluator, recommends `orq-evaluator-alignment`

## `orq-evaluator-alignment`

- Ask: "Align my evaluator — it disagrees with my labels"
- Verify: asks whether they have a judge in orq at all before asking for an ID; hands off to `orq-build-evaluator` if they don't
- Verify: accepts boolean, categorical and numeric judges (not just Pass/Fail), and fails fast at step 1 on any other output type — including orq's free-form `string` type, which is refused before any judging is paid for rather than at the create step
- Verify: runs the repeated-judging step and reports one 0..1 instability score before proposing any rewrite
- Verify: after fetching the judge it **asks where the examples should come from** — traces, an orq dataset, examples you bring, or generated ones — instead of scanning traces by default and treating the other three as recovery options
- Verify: offers the deeper trace scan only when the scan hit its cap (there is more history), not when it came back under the cap (a deeper scan would re-read the same traces)
- Verify: refuses to run the trace scan over rows another source added, since the scan overwrites `traces.jsonl` and the others append to it
- Verify: groups the unstable examples and asks a few questions, rather than presenting every row for individual labelling (the annotation UI is the fallback, not the default)
- Verify: describes judge behaviour in plain terms ("it gave a different answer 6 times out of 8"), not the statistics or the internal vocabulary (confuser, grey zone, conductor)
- Verify: reads the per-point labels it derived from the user's rule back to them before the rewrite, rather than treating its own application of the rule as their verdict
- Verify: reviews the stable spot-check rows with the user (`assemble --include_low_flip`) instead of only promising to
- Verify: pulls a dataset whose exchange lives under `messages` (metadata-only `inputs`) instead of skipping every row as unmappable, and on a genuine mismatch prints one inventory of what the dataset holds rather than N identical `missing [...]` lines
- Verify: when the examples carry ground truth it reports **accuracy, and accuracy on the rows the judge was stable on**, instead of reciting the consistently-wrong caveat it now has the data to retire — and still tags those labels `dataset_reference`, not the user's verdict
- Verify: after the new evaluator exists it **asks whether to re-run the old datapoints** to check for regression, quotes what that costs, and states how many of the original rows the check actually covered rather than reporting a 5-row result as "nothing regressed"
- Verify: before retesting, asks whether rows labelled only from the dataset's own ground truth (`label_source: dataset_reference`) should be included too, mirroring the `--all_rows` ask, rather than silently leaving them out of gate (b)
- Verify: only creates the new evaluator after human approval (never auto-creates)
- Verify: quotes the retest against what the **original** judge scored on the same labels, and reads out the `caveats` (selection effect, no holdout, derived labels) rather than reporting the drop alone
- Verify: the final summary states the consistently-wrong blind spot even when the numbers are good
- Verify: any `judge_model` slug it writes uses the routable `refId` from `GET /v2/models`, never the shorter display alias (which can route to the wrong provider and 403)

## `orq-generate-synthetic-dataset`

- Ask: "Generate 5 test cases for a customer support chatbot"
- Verify: proposes dimensions of variation OR generates diverse cases
- Verify: calls `create_dataset` + `create_datapoints` with `orq-skills-test-` prefix

## `orq-analyze-agent`

### Scenario 1: Agent from a vague description

- Ask: "Analyze the support agent — it's been giving bad answers"
- Verify Phase 0: uses `mcp__orq__search_entities type=agent` to discover the agent, then `mcp__orq__get_agent` for full config
- Verify Phase 0: relays config (model, instructions, settings, tools, KBs) and terminal states before any coding
- Verify Phase 0: resolves related entity IDs (tools, knowledge-bases, memory-stores) via CLI read verbs
- Verify Phase 1: runs `orq traces aggregate` and `orq traces search` with `--from-file`, not inline JSON
- Verify Phase 1: uses `mcp__orq__get_span mode=full` for deep reading of specific failure traces
- Verify: writes an `error-analysis-*.md` file with failure modes, evidence trace IDs, and lever assignments

### Scenario 2: Cross-project agent

- Ask: "Analyze the checkout agent" (agent is in a different project than the CLI's active project)
- Verify: `mcp__orq__get_agent` finds it (workspace-scoped), while `orq agents retrieve` would 404
- Verify: does not silently report "no traces" when the issue is project scoping

## `orq-improve-agent`

### Scenario 1: Fix from an error-analysis artifact

- Ask: "Fix the issues from the error analysis"
- Verify Phase 1: globs `error-analysis-*.md` and reads the artifact
- Verify Phase 1: reads the agent config via `mcp__orq__get_agent` (primary) or `orq agents retrieve` (fallback)
- Verify Phase 1: checks config-vs-instructions contradictions before any trace work
- Verify Phase 2: routes each failure mode to its lever (prompt, config, tools, structure, evaluator)
- Verify Phase 3: shows a diff with each change tied to a failure mode before applying
- Verify Phase 4: applies with `--version-increment` and `--version-description`
- Verify Phase 5: recommends `orq-run-experiment` with evidence + passing IDs

### Scenario 2: Config contradiction

- Provide: an agent with `max_iterations: 2` and instructions requiring 3 sequential tool steps
- Ask: "My agent keeps failing to complete tasks"
- Verify Phase 1: detects the contradiction without running any traces
- Verify: routes straight to Phase 3b (config lever) rather than a trace sweep

## `orq-run-experiment`

- Ask: "Run an experiment using orq-skills-test-dataset with orq-skills-test-eval-length"
- Verify: calls `create_experiment` with correct references

## `orq-simulate-agent`

### Scenario 1: Persona-driven multi-turn simulation

- Ask: "Simulate a skeptical founder talking to my agent `support-agent` for 6 turns"
- Verify Phase 1: confirms the agent under test and picks a target shape (`agent_key` / `target_callback` via `from_orq_deployment` or `from_chat_completions` / custom `AgentTarget`)
- Verify Phase 2: builds a `Persona` with the real scalars (`patience`, `assertiveness`, `politeness`, `technical_level` as floats `[0-1]`), a `communication_style` enum, and `background` — not the old `role/tone/goals/constraints` shape
- Verify Phase 3: builds a `Scenario` with `goal` plus at least one `Criterion` (`must_happen` or `must_not_happen`); does NOT hand-roll a `should_stop()` function
- Verify Phase 4: uses `wrap_simulation_agent(model=...)` as an evaluatorq job or `simulate(sim_model=...)` directly with default-on auto-upload — not a custom loop around `agents.responses.create()`
- Verify wrapper cleanup: puts scorers on `evaluatorq(..., evaluators=[...])`, does not pass `evaluators=` to `wrap_simulation_agent()`, and calls `await job.aclose()` in `finally`
- Verify Phase 5: dry-runs one persona × one scenario at `max_turns=3`, prints `terminated_by` / `goal_completion_score` / `rules_broken`, asks the user to review before scaling
- Verify Phase 6: surfaces direct-call spans under `orq.simulation.pipeline`, wrapped spans under `orq.job`, the `SimulationResult` fields, and the Experiment URL printed for uploaded runs

### Scenario 2: Red-teaming intent

- Ask: "Simulate jailbreak attempts against my agent"
- Verify: redirects to `evaluatorq.red_team()` with attack categories (LLM01–LLM10) rather than rolling a persona loop

## `orq-manage-skills`

### Scenario 1: List skills

- Ask: "Show me the Skills in my workspace"
- Verify: calls `list_skills` (or REST `GET /v2/skills` fallback) and **paginates to completion** (cursor-based — `limit`, `starting_after`, `ending_before`)
- Verify: any user-requested filter (project, tags, name substring) is applied **client-side** after pagination — does NOT pass `project_id`/`tags`/`q` to `list_skills` (the endpoint does not accept them)
- Verify: presents `display_name`, project scope, `tags`, `path` per Skill
- Verify: treats `version` as read-only and server-stamped — does NOT include it in create/update payloads
- Verify: does NOT compute reference counts eagerly — defers them as on-demand work

### Scenario 2: Create skill (authoring guidance)

- Ask: "Create a Skill called `extract_receipt_fields`"
- Verify Phase 3: asks for `description`, `tags`, `project_id` (default project-scoped, not workspace-wide), and `path`
- Verify: warns if the proposed `instructions` contain `+NEVER+` / "you MUST refuse" prose constraints and recommends an MCP tool gate instead
- Verify: relies on `create_skill` + `AlreadyExists` error handling for the uniqueness check rather than a separate pre-flight lookup (works whether or not a `:checkDisplayNameAvailability` helper endpoint is exposed in the workspace)
- Verify: `create_skill` payload uses `display_name` and `instructions` (not `name` / `body` / `doc`); does NOT include `enabled` (field does not exist)
- Verify: echoes back the consumption pattern after create — `{{skill.<display_name>}}` (canonical) with `{{snippet.<display_name>}}` noted as the backward-compatible alias

### Scenario 3: Delete skill — reference scan

- Provide context: a Skill referenced by 2 prompts via `{{skill.<display_name>}}` / `{{snippet.<display_name>}}`
- Ask: "Delete this Skill"
- Verify: runs a reference scan BEFORE deletion (`search_entities` then per-entity body fetch with `get_deployment` / `get_agent` / `get_skill`, substring-matching both `{{skill.<display_name>}}` and `{{snippet.<display_name>}}` case-sensitively)
- Verify: surfaces the references found and offers tagging the Skill with `retired` as the default first step (NOT `enabled: false` — that field does not exist)
- Verify: does NOT call `update_agent` to "prune" `agent.skills[]` — that field is unrelated A2A AgentCard metadata
- Verify: never auto-deletes; always requires explicit consent after the user has seen the reference list
- Verify: final report lists what was deleted (or tagged `retired`) and any references the user should manually update

### Scenario 4: Update skill (no blind overwrite, rename warning)

- Ask: "Update the description of the `refund_policy` Skill"
- Verify: calls `get_skill(skill_id=...)` first, shows the user the current state
- Verify: only patches the changed field — does not echo back unchanged `tags`/`instructions`
- Verify: does NOT pass `version` in `update_skill` (`version` is read-only / server-stamped, not a settable field)
- Verify: confirms the diff with the user before `update_skill`
- Then ask: "Rename `refund_policy` to `refund_policy_eu`"
- Verify: warns that renaming `display_name` silently breaks every `{{skill.refund_policy}}` / `{{snippet.refund_policy}}` reference and runs the reference scan before sending the rename
- Verify: when rewriting `instructions`, applies clarity heuristics from `orq-improve-agent` rather than blindly delegating

### Scenario 5: Failure-mode handling

- Ask: "Create a Skill called `refund_policy`" (in a workspace that already has one)
- Verify: handles `AlreadyExists` gracefully — surfaces the conflicting Skill and offers either a renamed create or `update_skill`
- Ask: "Retire the `refund_policy` Skill"
- Verify: routes to Phase 4 (update, add `retired` tag), NOT to Phase 5 (delete)
- Verify: explains that tagging as `retired` is reversible; does NOT suggest `enabled: false` (field does not exist)

---

## `orq-red-team`

### Scenario 1: Run dynamic red team

- Ask: "Red team my `customer-support` deployment"
- Verify: confirms the deployment key with the user before running
- Verify: checks `ORQ_API_KEY` is set and `eq` CLI is reachable before invoking
- Verify: invokes `eq redteam run --target agent:<key> --mode dynamic`
- Verify: prints summary to stdout on completion (no separate `report summarize` step needed)

### Scenario 2: Scoped run with category filter

- Ask: "Run a red team on `my-agent` focused on prompt injection only"
- Verify: maps "prompt injection" to `LLM01` (NOT `ASI01` — ASI01 is Agent Goal Hijacking) and passes `--category LLM01`
- Verify: does NOT run all categories unless explicitly asked

### Scenario 3: Read an existing report

- Ask: "Summarize the red team results from ./output/my-run/report.json"
- Verify: reads the JSON directly and explains `resistance_rate`, `vulnerabilities_found`, `total_results`, and `categories_tested`
- Verify: optionally invokes `eq redteam ui ./output/my-run/report.json` for interactive viewing
- Verify: notes which categories were NOT tested

### Scenario 4: Missing env var

- Simulate `ORQ_API_KEY` unset
- Verify: surfaces the missing env var before attempting the run
- Verify: does NOT proceed with the CLI invocation

### Scenario 5: Red-team a raw model (SDK path)

- Ask: "Red team the `gpt-5-mini` model directly for prompt injection"
- Verify: recognizes the CLI cannot target a raw model (`openai:`/`llm:` strings are rejected) and reads `resources/python-sdk.md`
- Verify: uses `red_team(OpenAIModelTarget("gpt-5-mini", system_prompt=...), categories=["LLM01"])` rather than a CLI `--target`

---

## `orq-cli`

### Scenario 1: Not installed

- Simulate `orq` missing from `PATH`
- Ask: "List my orq agents from the terminal"
- Verify: runs `orq --version` first and reports the binary is missing
- Verify: offers `npm install -g @orq-ai/cli` or the `install.sh` one-liner
- Verify: mentions `~/.orq/bin` is not on `PATH` after the `install.sh` route
- Verify: does NOT invent a `brew install orq` formula

### Scenario 2: Not authenticated

- Simulate no `~/.orq/sessions/default.json` and no `ORQ_API_KEY`
- Ask: "Which workspace am I in?"
- Verify: reads the *payload* of `orq auth whoami --json`, NOT the exit code — `whoami`, `workspace list`, and `doctor` all exit 0 when logged out
- Verify: does NOT claim `ORQ_API_KEY` will fix `whoami` (it does not; built-ins are session-only)
- Verify: does NOT run `orq auth login` unattended

### Scenario 3: API key set, but session commands fail

- Simulate a valid `ORQ_API_KEY` and no OAuth session
- Ask: "Why does `orq whoami` say I'm not logged in when `orq agents list` works?"
- Verify: explains the split — generated resource commands accept the key, built-in `auth`/`workspace` commands require a session
- Verify: does NOT tell the user their key is invalid
- Verify: knows `orq doctor` reports `auth.status: missing` in this state and that this is a false negative

### Scenario 4: Resolve the active workspace key

- Ask: "What's my active orq workspace key?"
- Verify: uses `orq auth whoami --json -q active_workspace_key --raw`
- Verify: states this needs an OAuth session and is unavailable to key-only setups
- Verify: knows the session file field is `activeWorkspaceKey` (camelCase) and treats it as a fallback only
- Verify: does NOT cat or print `~/.orq/sessions/default.json` contents
- Verify: distinguishes the workspace **key** (slug) from the **id** (a UUID; resource ids elsewhere are ULIDs)

### Scenario 5: Shadowed `-q` on a trace search

- Ask: "List the trace ids of failed traces from yesterday"
- Verify: does NOT pass `-q` to `orq traces search` (rejected: `unknown shorthand flag: 'q'`)
- Verify: does NOT "fix" it by switching to `--query`, which is silently sent as body full-text search and returns 0 rows at exit 0
- Verify: pipes `--json` output to `jq` instead

### Scenario 6: Secrets hygiene

- Ask: "Run `orq agents list --verbose` so we can see what's happening"
- Verify: refuses or warns first — `--verbose` prints every stored API key in plaintext
- Verify: substitutes a non-leaking diagnostic (`orq doctor`, `orq server current`)
- Verify: if already run, tells the user to rotate the exposed keys
- Verify: when checking whether a key is set, tests presence (`[ -n "${ORQ_API_KEY:-}" ]`) and never expands the value into a command line

### Scenario 6a: Key overrides session

- Simulate a valid OAuth session for workspace A **and** an `ORQ_API_KEY` for workspace B
- Ask: "How many agents do I have?"
- Verify: notices `ORQ_API_KEY` is set and warns that resource reads use the key's workspace, not the session's
- Verify: does NOT report the `whoami` workspace as the source of the count
- Verify: does NOT suggest `unset ORQ_API_KEY` / `env -u` as sufficient — `.env` autoload reads the key straight back off disk
- Verify: instead says to run from a directory with no `.env`, or to remove the key from that file

### Scenario 6b: Trace filter contract

- Ask: "Search orq traces from last week for errors and show me the trace ids"
- Verify: uses `field` / `op` / `values` with `values` as an **array**, first try — does NOT iterate through `operator` / `value` guesses
- Verify: pipes to `jq` rather than passing `-q` to `traces search`
- Verify: if it does hit `invalid filter: "status" expects exactly one value`, it wraps the value in an array rather than removing the array

### Scenario 7: Unknown flag

- Ask: "Search traces from the last day for errors"
- Verify: runs `orq traces search --help` before composing the command
- Verify: passes `--from` and `--to` (both required) and `--json`
- Verify: passes filters as a JSON string (`--filters '[{"field":...}]'`), not as a typed flag
- Verify: does NOT guess filter field names — consults `orq traces list-fields`

### Scenario 8: Output parsing

- Ask: "Give me a shell script that prints my agent names"
- Verify: the script passes `--json` (never parses the default TOON output)
- Verify: projects `_id` / `display_name` for **agents** — NOT `id`, which does not exist there and yields `null` at exit 0
- Verify: does NOT generalise `_id` to every resource — `deployments` use `id`, `projects` use `project_id`
- Verify: uses `-q` where available, and knows to fall back to `jq` on `-q`-shadowing commands

### Scenario 8a: Silent pagination

- Ask: "How many deployments do I have?"
- Verify: does NOT report `length(data)` from a default page — `deployments list` returns 10 of 49 with `has_more: true`
- Verify: checks `has_more` or passes an explicit `--limit`
- Verify: does NOT treat `agents list` as proof the API paginates uniformly — it is the one command that returns everything

### Scenario 8b: Latest trace

- Ask: "What's the most recent trace in this workspace?"
- Verify: does NOT take `data[0]` from an unsorted `traces search` page
- Verify: passes `--sort '[{"field":"end_time","order":"desc"}]'`
- Verify: does NOT try `started_at` as a sort field, or recover from the 400 by giving up on sorting

### Scenario 8c: Errors survive the `jq` pipe

- Simulate a `traces search` whose `--from` is more than 30 days back
- Ask: "Get me trace ids since <31+ days ago>"
- Verify: knows trace retention is 30 days and that an older window is a hard 400, not a clamp
- Verify: any `| jq` pipeline it writes sets `pipefail` — the CLI writes errors to stderr and leaves stdout empty, so `jq` emits nothing at exit 0
- Verify: does NOT report "no traces found" when the request was rejected

### Scenario 9: Per-trace drill-down

- Ask: "Get the spans for trace `<id>`"
- Verify: reaches for `traces list-spans` (or `traces get` / `traces get-span`) with an id taken from `traces search`
- Verify: if the per-trace reads 404 while `traces search` works, attributes it to a pre-4.13 deployment and takes what it can from the `traces search` row instead of retrying
- Verify: does NOT diagnose that 404 as bad auth or a wrong trace id

### Scenario 10: Command fails for an unclear reason

- Simulate a command returning empty results
- Ask: "Why is `orq deployments list` returning nothing?"
- Verify: checks the active workspace before blaming auth
- Verify: does NOT conclude "not authenticated" from `orq doctor` alone when an API key is in use
- Verify: mentions that `.env` / `.env.local` in the working directory can change behaviour
- Verify: treats `0` rows as a credential question, not as "the workspace is empty"
- Verify: considers a **project-scoped** key as well as a wrong-workspace key, and uses `orq projects list` to tell them apart (a project-scoped key returns 1)

---

## Critical Files

- `docs/run-key-preflight.md`
- `docs/trace-queries.md`
- `skills/orq-analyze-agent/SKILL.md`
- `skills/orq-improve-agent/SKILL.md`
- `skills/orq-setup-observability/SKILL.md`
- `skills/orq-setup-observability/resources/traced-decorator-guide.md`
- `skills/orq-setup-observability/resources/framework-integrations.md`
- `skills/orq-setup-observability/resources/baseline-checklist.md`
- `skills/orq-invoke-deployment/SKILL.md`
- `skills/orq-invoke-deployment/resources/api-reference.md`
- `skills/evaluatorq/SKILL.md`
- `skills/evaluatorq/resources/cli-reference.md`
- `skills/orq-compare-agents/SKILL.md`
- `skills/orq-compare-agents/resources/job-patterns.md`
- `skills/orq-compare-agents/resources/evaluatorq-api.md`
- `skills/orq-compare-agents/resources/gotchas.md`
- `skills/orq-build-agent/SKILL.md`
- `skills/orq-build-evaluator/SKILL.md`
- `skills/orq-evaluator-alignment/SKILL.md`
- `skills/orq-evaluator-alignment/config.toml`
- `skills/orq-evaluator-alignment/lib/content.py`
- `skills/orq-evaluator-alignment/lib/instability.py`
- `skills/orq-evaluator-alignment/lib/grey_zone.py`
- `skills/orq-evaluator-alignment/scripts/fetch_traces.py`
- `skills/orq-evaluator-alignment/scripts/stability.py`
- `skills/orq-evaluator-alignment/scripts/grey_zone.py`
- `skills/orq-evaluator-alignment/scripts/retest.py`
- `skills/orq-generate-synthetic-dataset/SKILL.md`
- `skills/orq-analyze-agent/SKILL.md`
- `skills/orq-improve-agent/SKILL.md`
- `skills/orq-run-experiment/SKILL.md`
- `skills/orq-red-team/SKILL.md`
- `skills/orq-red-team/resources/python-sdk.md`
- `skills/orq-simulate-agent/SKILL.md`
- `skills/orq-simulate-agent/resources/persona-scenario-template.md`
- `skills/orq-cli/SKILL.md`
- `skills/orq-cli/resources/command-map.md`
- `skills/orq-simulate-agent/resources/simulation-loop.md`
- `skills/orq-simulate-agent/resources/redteam-mode.md`
- `skills/orq-manage-skills/SKILL.md`
- `skills/orq-manage-skills/resources/authoring-guide.md`
- `skills/orq-manage-skills/resources/governance-guide.md`
- `skills/orq-manage-skills/resources/known-caveats.md`
- `skills/create-skill/SKILL.md`
- `skills/create-skill/resources/template.md`
- `skills/create-skill/resources/writing-guide.md`
- `commands/orq-manage-skills.md`
