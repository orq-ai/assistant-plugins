# Claude Code Plugins

## Plugin runtime locations

Claude Code loads plugin hooks from **`~/.claude/plugins/marketplaces/<marketplace>/`**, NOT from `~/.claude/plugins/cache/`. The cache is used for MCP servers and skills only.

For development, symlink the marketplace to your local clone so edits take effect immediately:

```bash
rm -rf ~/.claude/plugins/marketplaces/assistant-plugins
ln -s ~/Documents/orq-skills ~/.claude/plugins/marketplaces/assistant-plugins
```

**Warning**: CC may overwrite the symlink on marketplace sync. If hooks stop working after a CC update, re-create the symlink.

## Checking traces with the orq CLI

Always prefer the `orq` CLI over MCP tools for inspecting traces and spans. Use `orq profile set prod-claude-code` to switch to the production Claude Code workspace, then `orq trace list`, `orq trace span list <trace-id>`, `orq trace span get <trace-id> <span-id>`. Use `--json` flag for full details.

## Trace plugin configuration

The trace hook resolves API key and base URL using this priority chain:

1. **`ORQ_TRACE_PROFILE` env var** — trace-specific profile override (highest priority for API key, so traces can be pinned to a workspace independent of `ORQ_API_KEY`)
2. **`ORQ_API_KEY` / `ORQ_BASE_URL` env vars** — explicit overrides
3. **`ORQ_PROFILE` env var** — names a profile in `~/.config/orq/config.json`
4. **orq CLI current profile** — whatever `orq profile set <name>` is set to

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
   Expected: session span + turn span + 1 LLM response span

2. **Trace with tool calls**:
   ```bash
   env -u CLAUDECODE bash -c 'cd <repo> && claude -p "list files with ls" 2>&1'
   ```
   Expected: session span + turn span + tool spans (Bash) + LLM response spans

3. **Trace with subagents**:
   ```bash
   env -u CLAUDECODE bash -c 'cd <repo> && claude -p "Use the Agent tool to search for files matching *.py" 2>&1'
   ```
   Expected: session span + turn span + Agent tool span + sub-tool spans + LLM responses

4. **Verify traces arrived**:
   ```bash
   orq profile set prod-claude-code
   orq trace list --limit 3
   orq trace span list <trace-id>
   ```

5. **Check span hierarchy**: root `orq.claude_code.session` -> `claude.turn.N` -> tool/LLM child spans. Verify token counts, cost, and duration are populated.

6. **Run in parallel across repos** to test different git contexts:
   ```bash
   env -u CLAUDECODE bash -c 'cd ~/Documents/orq-skills && claude -p "say hi"' &
   env -u CLAUDECODE bash -c 'cd ~/Developer/orq/orq-smart-router && claude -p "say hi"' &
   wait
   ```
