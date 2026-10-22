# Claude Cowork (Desktop) install

> **Status:** The orq plugin is pending OAuth approval for the Claude Cowork marketplace. Once published, install via **Settings → Plugins → Browse** inside the Desktop app and search for "orq".

This document explains how the plugin works with Claude Cowork and how it differs from the Claude Code (CLI) install.

---

## How paths are resolved

The plugin manifest lives at `.claude-plugin/plugin.json` and declares:

```json
"skills": "./skills/",
"mcpServers": { "orq-workspace": { "type": "http", "url": "https://my.orq.ai/v2/mcp", "headers": { "Authorization": "Bearer ${user_config.orq_api_key}" } } }
```

Paths resolve from the plugin root, which is the repository root, so `./skills/` is `<repo-root>/skills/`. The MCP server is declared inline so the API key comes from the sensitive `orq_api_key` option instead of your shell; it replaces the same-named server in `<repo-root>/.mcp.json`, which Codex and Cursor still use with `ORQ_API_KEY`. No symlinks inside `.claude-plugin/` are needed (Anthropic's plugin directory validator rejects them).

---

## What the plugin provides (Claude Cowork)

| Component | Delivered via |
|-----------|--------------|
| Skills | `./skills/` → orq agent skills loaded automatically |
| MCP server | inline in `plugin.json` → `orq-workspace` HTTP MCP server registered |

Claude Code prompts for your orq.ai API key when you install or enable the plugin (or later via `/plugin configure`) and stores it in the credential store. If your Desktop build does not show that prompt, set `ORQ_API_KEY` in your environment before launching the app:

```bash
export ORQ_API_KEY=your-key-here
```

---

## Verify

Once the plugin is installed and `ORQ_API_KEY` is set, ask in chat:

> List my orq.ai agents.

If the MCP server is reachable you'll see a live response from your workspace.
