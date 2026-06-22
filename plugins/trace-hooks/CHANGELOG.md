# Changelog

All notable changes to the `orq-trace` plugin are documented here. Follows [Semantic Versioning](https://semver.org/).

## [0.3.2] - 2026-06-22

### Fixed
- Merge assistant content blocks sharing a `message.id` so token usage is counted once instead of per block (was inflating usage).
- Drop the `last_assistant_message` fallback that stamped the turn's final text onto tool/reasoning spans; tool spans now show their actual `tool_use` call.

### Changed
- Split assistant history into separate prose and per-tool-call messages instead of one mixed text+JSON string.
- `Stop`/`SessionEnd` hooks are synchronous (`async: false`) so final spans flush before process exit (async hooks were killed mid-send).

## [0.3.1] - 2026-06-17

### Fixed
- Config path: the hook read `~/.config/orq/config.json`, which does not exist — the orqi CLI stores profiles at `~/.orq/config.json`. The wrong path made `loadOrqConfig()` hit ENOENT and return `{}`, silently disabling the profile-resolution fallback chain (`ORQ_TRACE_PROFILE` / `ORQ_PROFILE` / CLI current profile). Only a raw `ORQ_API_KEY` env var worked. Repointed to `~/.orq/config.json`.

  Note: profile resolution now works where it never did before, so after upgrading, traces may start flowing to the workspace named by your active profile / `ORQ_TRACE_PROFILE`. Verify the destination is intended.

### Changed
- `ORQ_CONFIG_PATH` is now exported from `src/config.js` and overridable via the `ORQ_CONFIG_PATH` env var. Tests import the constant instead of re-hardcoding the path (single source of truth).
- Test profiles default to the CLI current profile + any other profile, so the suite runs against a real config without manual overrides.
