# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-14

### Added
- `manage-skills` skill — CRUD workflow for the orq.ai Skills entity (formerly Prompt Snippets), backed by `/v2/skills`. Covers list, get, create, update, soft-disable (`enabled: false`), and delete via the `*_skill` MCP tools. Includes authoring guidance (`display_name`, `description`, `tags`, `project_id`, `path`, `enabled`) and disambiguates the platform Skill entity from this repo's code-assistant Orq Skills and from the unrelated A2A `AgentCard.skills` array.
- `manage-skills`: documents the `{{snippet.<display_name>}}` template placeholder as the only mechanism for consuming Skills inside prompts and agent instructions (the `snippet.` prefix is a backwards-compat holdover from the rename — there is no `{{skill.<...>}}` syntax).
- `manage-skills`: reference-scan-before-delete workflow — paginates `search_entities`, fetches each candidate's body with `get_deployment` / `get_agent` / `get_skill`, and substring-matches `{{snippet.<display_name>}}` to surface consumers before any destructive operation. Defaults to `enabled: false` (soft disable) when references are found.
- `manage-skills`: rename-breaks-references warning on `display_name` updates — runs the same reference scan before any rename and offers to fan out updates in the same session.
- `manage-skills`: documents `GET /v2/skills` cursor pagination (`limit` / `starting_after` / `ending_before`) and the lack of server-side filters; pushes `project_id` / `tags` / `display_name` filtering to the client.
- `manage-skills`: anti-pattern guidance against `+NEVER+` / "you MUST refuse" prose constraints in `instructions` — recommends MCP tool gates for hard guardrails.
- `manage-skills`: error-handling guidance for `create_skill` `AlreadyExists` (offers either a renamed create or `update_skill` against the existing Skill).
- `/manage-skills` slash command — routes to list / get / create / update / disable / delete phases.

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
