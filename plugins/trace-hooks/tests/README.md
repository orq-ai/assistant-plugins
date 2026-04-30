# trace-hooks tests

Local smoke tests for the orq-trace plugin. Run after editing `src/`,
`hooks/`, or `.claude-plugin/plugin.json`.

## Quick start

```bash
cd plugins/trace-hooks
./tests/run-all.sh                  # uses default TRACE_PROFILE=prod-claude-code
./tests/run-all.sh staging          # use a different profile
```

## Individual tests

| Script | What it checks | Speed | External deps |
|--------|---------------|-------|--------------|
| `test-resolution.mjs` | `getApiKey()` / `getBaseUrl()` priority across 5 env-var permutations (T1–T5). Decodes JWT, asserts workspace_id match. | <1s | `~/.config/orq/config.json` with `research` + `prod-claude-code` profiles |
| `test-trace-flow.sh` | Spawns `claude -p` subprocess, verifies hook fires + POSTs OTLP 200. | ~2 min | `claude` CLI, network |
| `test-workspace-visibility.sh` | Same as flow test, then queries `orqi trace list` to confirm trace appears in workspace UI. | ~1 min + ingestion lag | `orqi`, valid API key, backend |

## Conventions

- Profile names default to `research` and `prod-claude-code`. Override via
  `TEST_RESEARCH_PROFILE` / `TEST_TRACE_PROFILE` env vars (resolution test) or
  positional arg (shell tests).
- All tests write to `/tmp/orq-trace-debug.log` (truncated each run). Inspect
  this file for hook output post-failure.
- `claude -p` only loads plugins registered in
  `~/.claude/plugins/installed_plugins.json`. If flow tests fail with empty
  log, register `assistant-plugins` marketplace + install `orq-trace`.

## Adding a test

1. Drop a `test-*.{sh,mjs}` script in this directory.
2. Add it to `run-all.sh` in the appropriate section.
3. Update the table above.
