# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
