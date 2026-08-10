# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.4] - 2026-08-10

### Changed
- `orq-evaluator-alignment`: evaluator CRUD (`evals get` / `evals create`) now shells out to the `orq` CLI instead of httpx against `/v2/evaluators`, using the CLI's own TLS and ambient auth (avoids the Windows OpenSSL Applink crash). Trace/model routes stay on httpx. Create is project-aware — an aligned copy is co-located with its source evaluator's project.

### Fixed
- `orq-evaluator-alignment`: trace extraction now reads the orq Responses API span shape (`span.responses`, with text under `messages[].parts[].content`) and no longer treats a reference-only root span as content. Previously every datapoint from a Responses-API evaluator came back hollow and the scan aborted with a misleading auth/rate-limit guess.
- `orq-evaluator-alignment`: the hollow-datapoint guard now distinguishes a span-detail fetch failure (auth/rate-limit) from an unrecognised span shape, and writes `hollow_debug.json` with the offending span shape on a shape-gap abort instead of guessing.
- `orq-evaluator-alignment`: Windows robustness — the cosmetic run-dir rename is guarded against OneDrive/WinError 32 locks, and human-edited artifacts are read as `utf-8-sig` so an editor-written BOM no longer crashes reads or corrupts the created prompt's first line.
- `orq-evaluator-alignment`: the stability step now skips degraded/hollow datapoints by default (they waste judge calls and flatten the flip metrics); `--include_degraded` keeps them.

## [2.2.3] - 2026-08-07

### Added
- Curated `allowed-tools` on five read-dominant skills (`orq-analyze-trace-failures`, `orq-optimize-prompt`, `orq-run-experiment`, `orq-build-evaluator`, `orq-build-agent`) — the `orq*` wildcard (which matched no tool and pre-approved nothing) replaced with explicit read/search orq tools for prompt-free lookups. Writes are not pre-approved: `create_*`/`update_*`/`invoke_*` and shell commands still prompt on these five skills. Each skill states this rationale under its title.
- `disallowed-tools` on the same five skills disables every `delete_*` orq tool outright while the skill is active — these skills never delete, so the tools are removed from the pool, not merely left prompting.
- `allowed-tools` on `orq-evaluator-alignment`, the last skill in the suite that declared none. It drives the orq API through its own bundled `uv run scripts/*.py` toolkit rather than orq tools, so its allowlist is narrow (`Bash(uv run:*)` plus file and question tools); the `delete_*` set is disallowed for consistency.
- Finished the `orq*` sweep across the remaining nine skills: the dead wildcard is replaced with the orq tools each skill's body actually calls, and `disallowed-tools` now covers every skill except `orq-manage-skills` (which legitimately deletes) and `orq-generate-synthetic-dataset` (which legitimately deletes datapoints, so only that one delete stays available — and only behind a prompt).
- Scoped `Bash` to the commands each skill actually runs (`Bash(orq:*)`, `Bash(eq:*)`, `Bash(curl:*)`, …), and dropped it entirely from the four skills with no shell examples. Unscoped `Bash` in `allowed-tools` pre-approved every shell command without a prompt, which was a wider grant than the `delete_*` tools this release set out to close.

### Changed
- `orq-manage-skills`: `create_skill`, `update_skill`, and `delete_skill` removed from `allowed-tools` (reads stay pre-approved) so every write and delete prompts; its delete workflow now uses the repo's `## Destructive Actions` + `AskUserQuestion` convention with per-entity confirmation and no bulk deletes without listing every item first.
## [2.2.2] - 2026-08-06

### Added

- Evaluatorq v1.10 features now referenced: LLM-jury and pairwise judging (`llm_jury`, `llm_jury_pairwise`, `PairwiseComparator`), `eq dashboard` run browser, `eq sim ui`, CrewAI and Pydantic AI target wrappers, redteam `--strategy`/`--delivery-method`/`--executive-summary` flags, and the `ORQ_WORKSPACE`/`ORQ_UI_BASE_URL` dashboard deep-link variables.

### Fixed

- Evaluatorq-family skills re-verified against the **current evaluatorq release (1.11.x)** (RES-1220):
  - CLI flags aligned to 1.11: `eq sim generate` writes via `--datapoints/-d`; `eq sim simulate` reads via `--input/-i` and writes via `--results/-r`; redteam report flags are `--report`/`--artifacts-dir`/`--report-md`/`--report-html` (the pre-1.11 `--save-report`/`--output-dir`/`--export-md`/`--export-html` no longer exist); Python `red_team(artifacts_dir=)` likewise.
  - `target_callback=` removed everywhere — it does not exist in the library (`target=` is the only name; passing it raises `TypeError`).
  - `simulate()`/`generate_and_simulate()` report kwargs documented (`report=`, `executive_summary=`, `save=`, `orq_results_path=`); `eq sim` subcommand list completed (`from-traces`, `upload-dataset`, `validate` with `validate-dataset` as deprecated alias); OWASP table carries `ASI10` Rogue Agents; async scorer example uses `await orq.evals.invoke_async(...)` with env-derived credentials; `orq-ai-sdk` added to the install table.
  - `orq-red-team`: credential-priority rule corrected — `ORQ_API_KEY` (gateway) wins when both keys are set, not `OPENAI_API_KEY`; the "uv run + .env credential trap" section, conflict guidance, and troubleshooting rows rewritten around the verified routing order. Removed the nonexistent `[ui]` extra (`eq redteam ui` ships inside `[redteam]`; `openai`/`typer` are core deps).
  - `evaluatorq`: `eq sim` quick references corrected — the target flag is `--target agent:<key>` (no `--agent-key`), datapoint-driven runs use `eq sim simulate` (not `run`), and the generate→export example now includes the required simulate step. Dashboard stage-file name fixed (`03_summary_report.json`).
  - `orq-simulate-agent`: `simulate()`/`generate_and_simulate()` examples no longer pass `agent_key=` (a TypeError — the parameter is `target=`); `red_team` import path corrected to `evaluatorq.redteam`; raw-model targets use `OpenAIModelTarget` (the `llm:` prefix is rejected); run store documented as CWD-relative `.evaluatorq/` with `$EVALUATORQ_DIR` override.
  - `orq-compare-agents`: the `my.`→`api.` staging URL rewrite no longer exists (removed in evaluatorq v1.10.0) — `ORQ_BASE_URL` is used verbatim with default `https://my.orq.ai`; the obsolete workaround was replaced with a historical note.

## [2.2.1] - 2026-08-06

### Changed

- `orq-cli`: command map re-verified live against the **4.13** CLI and rewritten to describe the 4.13 surface only — no version deltas or cross-version caveats. Covers the `annotation-queues` group (eval corrections / unified annotations), per-trace drill-down (`traces get` / `list-spans` / `get-span`), and notes there is no `experiments` group. The map is scoped to the supported surface; some CLI groups are intentionally not covered. (RES-1210)
- `orq-cli`: caveats re-tested live on 4.13 — plain strings accepted by generated flags (double-JSON-quoting workaround deleted); no client-side enum validation (documented as such: read the server error).
- `orq-cli`: the global projection flag is `-j/--jmespath` — 4.13 removed `-q/--query`, and body fields no longer shadow globals (colliding body fields get a `body-` prefix; a body `query` field is plain `--query`, full-text search). Every example updated; the shadowing section replaced with the `--query`-is-not-a-projection trap and the `-q`-muscle-memory error.
- MCP tool-name rot fixed across resources: `evaluator_get` → `get_llm_eval`/`get_python_eval` (4 files); phantom `list_registry_keys` row removed from `orq-invoke-deployment`; `## Companion Skills` heading case normalized.
- `orq-cli`: "MCP tools or the CLI?" decision table extended with the coverage split — MCP-exclusive (experiments, docs/entity search) vs CLI-only (schedules, identities, projects, API keys, webhooks, knowledge bases, memory stores, files).
- All 12 MCP-primary skills now cross-reference `orq-cli` for anything that must run again without an agent present (CI, cron, scripts, bulk), pointing at the canonical decision table. (RES-1163)

### Fixed

- `orq-red-team`: stale MCP references `mcp__orq-mcp-global__agent_get`/`agent_list` corrected to `mcp__orq-workspace__get_agent`/`search_entities` — the old server and tool names no longer exist.

## [2.2.0] - 2026-07-31

### Added
- **`orq-cli` skill** — drive the `orq` command-line interface end to end: verify the install (`npm i -g @orq-ai/cli` or `install.sh`, which lands in the often-not-on-`PATH` `~/.orq/bin`), authenticate via OAuth session or `ORQ_API_KEY`, scope to the right workspace, discover commands with `--help`, and run them with `--json` plus `-q` JMESPath projections. Covers the profile model, the `auth whoami` / `workspace list` / `doctor` JSON shapes, the four request-body input paths (typed flags, stdin, `--from-file`, `--example`) and bartolo shorthand, the `orq request` escape hatch, and a symptom→cause troubleshooting table. Companion `resources/command-map.md` carries the full v4.12.15 command tree, global flags and their env-var twins, verified response field names for traces/spans/agents, and JMESPath recipes — including resolving the active workspace key (`orq auth whoami --json -q active_workspace_key --raw`) for app deep-links, with a warning not to shell out to the CLI from library request paths. Registered in `agents/AGENTS.md`, `README.md`, `tests/skills.md`, and `skills-lock.json`. (RES-1140)

## [2.1.1] - 2026-07-22

### Fixed
- **`orq-evaluator-alignment`** — corrected the `judge_model` router slug format documented in `SKILL.md`. It previously stated the router requires `<provider>/openai/<model>` with a literal `openai/` segment "always required, whatever the provider". That form 404s on the router (`anthropic/openai/claude-haiku-4-5` → 404) and every user following it with a non-OpenAI provider hit a bare 404 mid-run. The correct form is the plain `<provider>/<model>` single-prefix slug (e.g. `anthropic/claude-haiku-4-5`, `google/gemini-2.5-flash`) — verified live against the Orq router (chat-completions and responses endpoints, openai/anthropic/google all route) and consistent with the agent config and the MCP `create_llm_eval` tool. Added a regression note to `tests/skills.md`. (RES-1145)

## [2.1.0] - 2026-07-02

### Added
- **`orq-evaluator-alignment` skill** — align, calibrate, or improve an existing binary Pass/Fail LLM-as-a-judge (orq evaluator) so its verdicts match human judgment. Measures judge self-consistency (flip-rate) via repeated runs, surfaces the most ambiguous datapoints for human annotation, rewrites the judge prompt from the labels, and creates the new evaluator only after human approval. Complements `orq-build-evaluator` (build from scratch) and `orq-optimize-prompt` (fix via prompt tweaks). Every step script is self-contained via PEP 723 inline dependencies, so `uv run scripts/<name>.py` builds its own environment with no repo checkout or `uv sync`. Registered in `agents/AGENTS.md`, `README.md`, `tests/skills.md`, and `skills-lock.json`.

## [2.0.0] - 2026-06-24

### Changed
- **BREAKING — renamed the Claude Code marketplace from `assistant-plugins` to `orq-claude-plugin`** (`.claude-plugin/marketplace.json` `name`). Install commands change from `claude plugin install <plugin>@assistant-plugins` to `@orq-claude-plugin`. The GitHub repo path (`orq-ai/assistant-plugins`) used by `claude plugin marketplace add` and `npx skills add` is **unchanged**, so Codex/Cursor/Warp/Gemini installs are unaffected — only Claude Code's `@<marketplace>` handle changed. Existing `@assistant-plugins` installs must re-add the marketplace. Updated install docs in `README.md`, `plugins/trace-hooks/README.md`, `plugins/trace-hooks/CLAUDE.md` (dev symlink path), and `plugins/trace-hooks/tests/README.md`; the orquesta-web docs (`claude-code.mdx`) were aligned to the already-published `@orq-claude-plugin` instructions in the changelog/tutorial/skills pages.

## [1.0.0] - 2026-06-11

### Changed
- **BREAKING — namespace every skill under the `orq-` prefix** to prevent collisions with similarly-named skills from other plugin marketplaces and make the suite discoverable as a set. Renamed 11 skill directories and their frontmatter `name`: `analyze-trace-failures`, `build-agent`, `build-evaluator`, `compare-agents`, `generate-synthetic-dataset`, `invoke-deployment`, `manage-skills`, `optimize-prompt`, `run-experiment`, `setup-observability`, `simulate-agent` → `orq-*`. `orq-red-team` already carried the prefix; `evaluatorq` is left as-is (its name already reads "orq"). Updated all cross-references in `agents/AGENTS.md` (path list + `<available_skills>`), `README.md`, `tests/`, `skills-lock.json`, and inter-skill companion references.
- **BREAKING** — renamed the `/manage-skills` slash command to `/orq-manage-skills` (`commands/manage-skills.md` → `commands/orq-manage-skills.md`, frontmatter `name`) to match its 1:1 skill.

## [0.5.2] - 2026-06-11

### Fixed
- `orq-red-team`: the "Verify the target exists" preflight now `export`s the resolved `ORQ_API_KEY` on success, so the verify call and the subsequent `eq redteam run` use the same key. Previously a key present only in `./.env` would pass the verify curl (which sourced it explicitly) but the run — `eq` reads `ORQ_API_KEY` from the environment and does not auto-read `./.env` when run directly — would get an empty key and fail deep with a cryptic 401/404.

## [0.5.1] - 2026-06-10

### Changed
- `orq-red-team`: the "Verify the target exists" preflight now checks via REST/SDK with the key the run actually uses (`ORQ_API_KEY` from the env, else the project `.env`) instead of the MCP, whose separately-configured key is often in a different project — an MCP miss isn't proof the target is absent. Covers both `agent:` (`GET /v2/agents/{key}`) and `deployment:` (`POST /v2/deployments/get_config`) targets, and falls back to asking the user when no key resolves.

## [0.5.0] - 2026-06-08

### Added
- `evaluatorq`: new skill for writing evaluatorq evaluation scripts (Python + TypeScript) and operating the `eq` CLI. Covers single-agent evaluation, custom scorers, built-in evaluators. Routes to `red-team` skill for `eq redteam` and `simulate-agent` skill for `eq sim`.
- `evaluatorq/resources/cli-reference.md`: full flag reference for `eq redteam` and `eq sim`, output file schemas, and common usage patterns.

## [0.4.0] - 2026-06-06

### Added
- `orq-red-team`: `eq` discovery ladder before installing — probe local/cheap options first (PATH → project `.venv/bin/eq` → `uv run --package evaluatorq` orqkit workspace → `python3 -m evaluatorq`) and use the first hit; install only as a last resort, preferring a project-local venv (`uv pip install` into `.venv`) over a global `uv tool install`, and avoiding global `pip` (PEP 668 / `--break-system-packages` breakage). Every resolved invocation is quote-clean and reusable as a `$EQ` prefix; the preflight CLI check honors it via `${EQ:-eq}`.
- `orq-red-team`: document the `uv run` + `.env` credential trap — `uv` injects an env-file only when opted in (`UV_ENV_FILE` set or `--env-file` passed); it does **not** auto-read `./.env` from a bare `uv run` (verified on uv 0.11.19). When such an env-file holds `OPENAI_API_KEY`, uv re-loads it *after* a shell `unset`, flipping routing to direct OpenAI and breaking gateway model strings (`openai/gpt-5-mini` → `401 Incorrect API key`). Includes a default-vs-`--no-env-file` detector that also surfaces `UV_ENV_FILE` (presence only, never prints the key), a decide-don't-auto-strip guide that surfaces the conflict and lets the user choose (a key in the env usually means they want it), Fix A (`env -u OPENAI_API_KEY uv run --no-env-file …` — strip the key and block uv from re-adding it, no temp file), Fix B (run `eq` off PATH), and a note that plain `env -u` without `uv run` is unaffected.
- `orq-red-team`: pre-run "Verify the target agent exists" step — check the `--target` key via orq MCP `agent_get`/`agent_list`, REST `GET /v2/agents/{agent_key}` (curl), or SDK `agents.retrieve`, so a wrong key fails fast instead of deep in the run with `Agent not found`. Documents the project-scoping caveat (MCP key may differ from the CLI key; a hit confirms existence, a miss is conclusive only when the checking credential shares the agent's project — verified live).
- `orq-red-team`: troubleshooting rows for the `uv run`/`.env` 401, the mid-run `Agent not found`, and the discovery-first `eq: command not found` fix.
- `orq-red-team` (`resources/python-sdk.md`): document `OrqResponsesTarget` — the hosted-agent target wrapping orq's Responses v3 API. Covers the import path (`evaluatorq.openresponses.target`), constructor signature, a `red_team()` example, and `require_orq=True` gateway routing. Notes it's usually built for you by the `openresponses` backend when you pass `"agent:<key>"`; hand-build only for a custom client/instructions/timeout/retry.
- `orq-red-team`: "Plan the run — decide parameters with the user" guided flow before the first invocation — step through mode (`dynamic`/`static`/`hybrid`, with an upside/downside table), datapoint budget (`--max-dynamic-datapoints` / `--max-static-datapoints` as the main cost lever, with smoke-test vs assessment guidance and the per-category multiplier), vulnerability scope (an agent-surface → OWASP-ASI/LLM category map, noting the framework auto-prunes non-applicable categories so over-picking is cheap), and delivery (one-off CLI vs a reusable script for the fix → re-run loop, CI, and baking in the `.env`/`uv` credential handling), then confirm the choices and state the coverage gap before assembling the command.

## [0.3.0] - 2026-06-06

### Changed
- Rename the `red-team` skill to `orq-red-team` for clearer invocation and to namespace it under orq. Skill directory `skills/red-team/` → `skills/orq-red-team/`, frontmatter `name`, and all references in `README.md`, `agents/AGENTS.md`, and `tests/skills.md`. (Treated as MINOR rather than MAJOR: `0.2.0` was never released/tagged, so the old name has no external consumers.)

## [0.2.0] - 2026-06-05

### Added
- `red-team`: `resources/python-sdk.md` progressive-disclosure reference for the `evaluatorq.redteam` Python API — covers `red_team()`, `OpenAIModelTarget` / the `agent:<key>` string target / custom `AgentTarget`, a raw-model worked example (the case the CLI cannot do), and programmatic `RedTeamReport` handling.
- `red-team`: document external-framework targets in `resources/python-sdk.md` — `LangGraphTarget` (`[langgraph]`), `OpenAIAgentTarget` (`[openai-agents]`), and `CallableTarget` (bundled, the escape hatch for any `async def(prompt) -> str`), plus LangChain/Vercel AI SDK pointers. Covers red-teaming a non-orq agent, which the CLI cannot do.
- `red-team`: document `generate_recommendations=True` and `report.focus_area_recommendations` (SDK-only LLM remediation) in both `SKILL.md` and `resources/python-sdk.md`.
- `red-team`: add "Acting on results — next steps" guidance for coding assistants — how to mine `report.results[]` (filter `vulnerable`, read `attack.category`/`attack_technique`, the transcript, and `evaluation.explanation`), prioritize by `summary.by_technique`/`by_category`, map failure patterns to concrete fixes, and close the re-run feedback loop. `jq` recipes in `SKILL.md`; the Python equivalent plus `focus_area_recommendations` handling in `resources/python-sdk.md`.
- `simulate-agent` skill: run multi-turn agent simulations using evaluatorq's first-class primitives (`simulate()`, `generate_and_simulate()`, `wrap_simulation_agent()`). Covers the real `Persona` schema (`patience` / `assertiveness` / `politeness` / `technical_level` scalars, `communication_style`, `background`, optional `emotional_arc` and `cultural_context`), `Scenario` schema (goal, criteria-driven judge termination, starting emotion, conversation strategy, edge-case flag), three target shapes (`agent_key`, `target_callback` via `from_orq_deployment` / `from_chat_completions`, custom `AgentTarget`), and where outputs land (OTel spans auto-emitted to orq.ai, `SimulationResult` in memory, auto-uploaded Experiments via `evaluatorq()` routing, JSONL export). Resources: `persona-scenario-template.md`, `simulation-loop.md`, `redteam-mode.md`. RES-732.

### Fixed
- `red-team`: correct `ASI01` label — it is **Agent Goal Hijacking**, not prompt injection (prompt injection is `LLM01`). Reframed the worked example and category guidance, and added the full OWASP-ASI (ASI01–10) / OWASP-LLM (LLM01–09) name mapping.
- `red-team`: correct the credential model — routing is decided by which env var is set (`OPENAI_API_KEY` → direct OpenAI with bare model names; else `ORQ_API_KEY` → orq gateway with provider-prefixed names like `openai/gpt-5-mini`), not by the model string. `OPENAI_API_KEY` wins if both set; `ORQ_API_KEY` always required for `agent:`/`deployment:` targets.
- `red-team`: use `openai/gpt-5-mini` in examples and drop the backwards "switch to `gpt-4o`" troubleshooting advice (the default `gpt-5-mini` is newer).
- `red-team`: remove invented framework labels ("OWASP Agentic 2026" / "OWASP LLM 2025"); use the real `OWASP-ASI` / `OWASP-LLM` identifiers.
- `red-team`: fix install instructions to `pip install 'evaluatorq[redteam]'` (and note the `[ui]` extra for the dashboard).

### Changed
- `red-team`: invocation preflight checks credentials before any `eq redteam run` — hard-fail if no LLM credential at all (`OPENAI_API_KEY` or `ORQ_API_KEY`), and check-and-warn for `ORQ_API_KEY`. Document that `ORQ_API_KEY` is not strictly required (raw-model runs work with `OPENAI_API_KEY` alone) but is needed for orq `agent:`/`deployment:` targets, gateway LLM routing, and uploading results to orq (`experiment_url`). The agent halts only when an orq-agent target is requested without the key.
- `red-team`: trim the flag table to first-run essentials and defer the full set to `eq redteam run --help`; document the `deployment:<key>` target form, the `eq redteam validate-dataset` pre-flight, and the `--system-prompt` flag.
- `red-team`: add a Constraints note (and `--no-cleanup-memory` flag row) that dynamic runs against a **memory-backed** agent write entities into its memory store (cleaned up unless `--no-cleanup-memory`); no-op for memory-less agents, raw models, and static mode.

## [0.1.0] - 2026-06-04

### Added
- `red-team`: new skill for invoking the orq red teaming library — adaptive attacks, dataset runs, hybrid mode, OWASP Agentic/LLM coverage, and ASR reporting.
- `manage-skills` skill — CRUD workflow for the orq.ai Skills entity (formerly Prompt Snippets), backed by `/v2/skills`. Covers list, get, create, update, soft-retire (tag as `retired`), and delete via the `*_skill` MCP tools. Includes authoring guidance (`display_name`, `description`, `tags`, `project_id`, `path`) and disambiguates the platform Skill entity from this repo's code-assistant Orq Skills and from the unrelated A2A `AgentCard.skills` array.
- `manage-skills`: documents both `{{skill.<display_name>}}` (canonical) and `{{snippet.<display_name>}}` (backward-compatible alias, falls back to the Skill whose `display_name` matches) as the template placeholders for consuming Skills inside prompts and agent instructions.
- `manage-skills`: reference-scan-before-delete workflow — paginates `search_entities`, fetches each candidate's body with `get_deployment` / `get_agent` / `get_skill`, and substring-matches both `{{skill.<display_name>}}` and `{{snippet.<display_name>}}` to surface consumers before any destructive operation. Defaults to tagging with `retired` (soft-retire) when references are found.
- `manage-skills`: rename-breaks-references warning on `display_name` updates — runs the same reference scan before any rename and offers to fan out updates in the same session.
- `manage-skills`: documents `GET /v2/skills` cursor pagination (`limit` / `starting_after` / `ending_before`) and the lack of server-side filters; pushes `project_id` / `tags` / `display_name` filtering to the client.
- `manage-skills`: anti-pattern guidance against `+NEVER+` / "you MUST refuse" prose constraints in `instructions` — recommends MCP tool gates for hard guardrails.
- `manage-skills`: error-handling guidance for `create_skill` `AlreadyExists` (offers either a renamed create or `update_skill` against the existing Skill).
- `/manage-skills` slash command — routes to list / get / create / update / retire / delete phases.

### Fixed
- `red-team`: rewrite skill to target the real `evaluatorq` package (`orqkit/packages/evaluatorq-py`) and `eq redteam` CLI instead of the legacy `research/projects/red-teaming` path.
- `red-team`: replace non-existent `redteam run adaptive/dataset/hybrid` subcommands with the actual `eq redteam run --mode dynamic|static|hybrid` interface.
- `red-team`: fix all CLI flags — `--category` repeatable (not `--categories` comma-separated), `--max-dynamic-datapoints`/`--max-static-datapoints` (not `--max-attacks`), `--generated-strategy-count` (not `--generated-count`), `--parallelism` default 10 (not 5), `--output-dir` (not `--out`).
- `red-team`: remove non-existent `redteam report summarize` command; replace with `eq redteam runs` / `eq redteam ui <path>`.
- `red-team`: fix default model to `gpt-5-mini`; add OpenAI `gpt-4o` as worked example model.
- `red-team`: fix env var section — document auto-detection order (`OPENAI_API_KEY` → direct OpenAI; `ORQ_API_KEY` → orq gateway); remove incorrect Azure credential guidance.
- `red-team`: fix output file naming — auto-named `redteam-report-<target>-<ts>.json` in `.evaluatorq/runs/`; use `--save-report <path>` for explicit path.
- `red-team`: add authorization guardrail — require explicit user confirmation before attacking any deployment.
- `red-team`: fix `tests/skills.md` scenarios to use correct `eq redteam run --mode dynamic` invocations.
- `agents/AGENTS.md`: remove trailing blank line after red-team `<available_skills>` entry.

## [0.0.2] - 2026-04-21

### Added
- `invoke-deployment`: document three deployment invocation patterns — variable substitution (`inputs`), message appending (`messages`), and mixed — with Python and curl templates for each.
- `invoke-deployment`: Phase 1 Step 3 now fetches `GET /v2/deployments/<key>/config` to discover `{{variable}}` placeholders before invoking.
- `invoke-deployment`: anti-pattern entry for passing `inputs` to a deployment with no matching `{{variable}}` placeholders (silently ignored).

### Changed
- `invoke-deployment`: Phase 1 marked as one-time setup — discovery steps do not belong in production invocation flows.
- `invoke-deployment`: clarify `inputs` only substitute when matching `{{variable}}` placeholder exists in the prompt template.

### Fixed
- `invoke-deployment`: replace insecure `curl -sk` with `curl -s` in deployment config fetch example (no TLS bypass).

## [0.0.1] - 2026-04-21

### Added
- `invoke-deployment` skill — invoke orq.ai deployments, agents, and models via Python SDK, Node.js SDK, or curl. Covers prompt variable substitution, multi-turn agent conversations via `task_id`, AI Router calls with `provider/model` format, and streaming.
- `setup-observability` skill — instrument LLM applications with orq.ai tracing. AI Router mode, OpenTelemetry/OpenInference mode, and the `@traced` decorator for custom spans.
- `compare-agents` skill — cross-framework agent comparisons using `evaluatorq` from orqkit. Compare orq.ai, LangGraph, CrewAI, OpenAI Agents SDK, and Vercel AI SDK head-to-head.
- Codex and Cursor plugin manifests (`.codex-plugin/`, `.cursor-plugin/`) plus Codex marketplace entry.
- `tests/scripts/validate-plugin-manifests.sh` — validates plugin JSON, field values, and symlink integrity.
- Smoke test scenarios in `tests/skills.md` for every skill.

### Changed
- README install instructions expanded to cover 5 tools: Claude Code, Cursor, Codex, npx skills CLI, and manual clone.
- Python code templates now use `os.environ["ORQ_API_KEY"]` instead of `os.environ.get()` / `os.getenv()` to fail fast on missing key.
- Renamed `instrument-app` skill to `setup-observability`.
- AI Router base URL standardized to `https://api.orq.ai/v2/router` across all skills.
