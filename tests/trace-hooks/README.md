# trace-hooks tests

Maintainer smoke tests for the orq-trace plugin. They live at the repository
root so the plugin package does not include its own tests. Run them from the
repository root after editing `plugins/trace-hooks/src/`, `plugins/trace-hooks/hooks/`, or the
marketplace entry in `.claude-plugin/marketplace.json`.

## Quick start

```bash
./tests/trace-hooks/run-all.sh
TEST_ORQ_API_KEY=<key> ./tests/trace-hooks/run-all.sh   # also runs the subprocess trace-flow test
```

## Individual tests

| Script | What it checks | Speed | External deps |
|--------|---------------|-------|--------------|
| `test-resolution.mjs` | `getApiKey()` / `getBaseUrl()` / `getOtlpEndpoint()`: plugin option (`CLAUDE_PLUGIN_OPTION_ORQ_API_KEY`) first, then `ORQ_API_KEY`, else null; empty values count as unset; a decoy `~/.orq/config.json` is ignored. 11 cases. | <1s | none |
| `test-trace-flow.sh` | Spawns `claude -p` subprocess with the key as `ORQ_API_KEY`, as the plugin option, and as both; verifies hook fires + POSTs OTLP 200. SKIPs without `TEST_ORQ_API_KEY`. | ~2 min | `claude` CLI, network, `TEST_ORQ_API_KEY` |
| `test-workspace-visibility.sh` | Same as flow test, then queries `orqi trace list` to confirm trace appears in workspace UI. | ~1 min + ingestion lag | `orqi`, `TEST_ORQ_API_KEY`, backend |

## Conventions

- The hook reads no config file. Tests supply keys through
  `CLAUDE_PLUGIN_OPTION_ORQ_API_KEY` / `ORQ_API_KEY` only, and use short
  placeholders such as `test-key`; generate anything longer at runtime.
- All tests write to `/tmp/orq-trace-debug.log` (truncated each run). Inspect
  this file for hook output post-failure.
- `claude -p` only loads plugins registered in
  `~/.claude/plugins/installed_plugins.json`. If flow tests fail with empty
  log, register `orq-claude-plugin` marketplace + install `orq-trace`.

## Adding a test

1. Drop a `test-*.{sh,mjs}` script in `tests/trace-hooks/`.
2. Add it to `run-all.sh` in the appropriate section.
3. Update the table above.
