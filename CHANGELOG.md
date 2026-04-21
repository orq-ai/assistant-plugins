# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
