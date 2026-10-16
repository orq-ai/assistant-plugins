# Claude Code Plugins

## Plugin runtime locations

Claude Code loads plugin hooks from **`~/.claude/plugins/marketplaces/<marketplace>/`**, NOT from `~/.claude/plugins/cache/`. The cache is used for MCP servers and skills only.

For development, symlink the marketplace to your local clone so edits take effect immediately:

```bash
rm -rf ~/.claude/plugins/marketplaces/orq-claude-plugin
ln -s ~/Documents/orq-skills ~/.claude/plugins/marketplaces/orq-claude-plugin
```

**Warning**: CC may overwrite the symlink on marketplace sync. If hooks stop working after a CC update, re-create the symlink.

## Checking traces with the orqi CLI

Always prefer the `orqi` CLI over MCP tools for inspecting traces and spans. Use `orqi profile set prod-claude-code` to switch to the production Claude Code workspace, then `orqi trace list`, `orqi trace span list <trace-id>`, `orqi trace span get <trace-id> <span-id>`. Use `--json` flag for full details.

## Trace plugin configuration

The trace hook reads credentials from the environment only. It never reads `~/.orq/config.json` or any orq CLI profile.

API key, first match wins:

1. **`CLAUDE_PLUGIN_OPTION_ORQ_API_KEY`** — the plugin's own `ORQ_API_KEY` userConfig option (`sensitive: true`, declared in the `orq-trace` entry of `.claude-plugin/marketplace.json`). Claude Code exports every userConfig option to hook processes as `CLAUDE_PLUGIN_OPTION_<KEY>`. Set it with `/plugin configure`.
2. **`ORQ_API_KEY` env var**
3. None: tracing stays off.

An empty value counts as unset. Other settings: `ORQ_BASE_URL` (default `https://my.orq.ai`) and `ORQ_OTLP_ENDPOINT` (explicit ingest URL for hosts where the `my.` to `api.` subdomain swap does not serve OTLP).

Migration: `ORQ_TRACE_PROFILE`, `ORQ_PROFILE`, `ORQ_CONFIG_PATH` and the orqi CLI current profile no longer have any effect. Users who relied on them set the plugin option or export `ORQ_API_KEY`.

Tracing is enabled whenever an API key can be resolved. Disable by disabling the plugin in CC settings.

The `env` field in `~/.claude/settings.local.json` only applies to MCP servers, not hooks. Environment variables must be set in your shell (e.g. `~/.zshrc`).

The OTLP endpoint can be overridden with `OTEL_EXPORTER_OTLP_ENDPOINT`, and extra headers with `OTEL_EXPORTER_OTLP_HEADERS`.

## Testing trace hooks

Hooks do NOT fire when running `claude` from inside an existing Claude Code session (CC sets `CLAUDECODE=1` which suppresses hooks in child processes). To test:

```bash
# Unset CLAUDECODE to allow hooks in child processes
env -u CLAUDECODE bash -c 'cd ~/Documents/orq-skills && claude -p "list files with ls" 2>&1'
```

### Testing methodology

1. **Simple trace (no tools)**:
   ```bash
   env -u CLAUDECODE bash -c 'cd <repo> && claude -p "say hello" 2>&1'
   ```
   Expected: session span + 1 LLM response span

2. **Trace with tool calls**:
   ```bash
   env -u CLAUDECODE bash -c 'cd <repo> && claude -p "list files with ls" 2>&1'
   ```
   Expected: session span + tool spans (Bash) + LLM response spans

3. **Trace with subagents**:
   ```bash
   env -u CLAUDECODE bash -c 'cd <repo> && claude -p "Use the Agent tool to search for files matching *.py" 2>&1'
   ```
   Expected: session span + Agent tool span + sub-tool spans + LLM responses

4. **Verify traces arrived**:
   ```bash
   orqi profile set prod-claude-code   # selects what orqi lists; the hook does not read it
   orqi trace list --limit 3
   orqi trace span list <trace-id>
   ```

5. **Check span hierarchy**: root `orq.claude_code.session` -> `claude.turn.N` -> tool/LLM child spans. Verify token counts, cost, and duration are populated.

6. **Run in parallel across repos** to test different git contexts:
   ```bash
   env -u CLAUDECODE bash -c 'cd ~/Documents/orq-skills && claude -p "say hi"' &
   env -u CLAUDECODE bash -c 'cd ~/Developer/orq/orq-smart-router && claude -p "say hi"' &
   wait
   ```
