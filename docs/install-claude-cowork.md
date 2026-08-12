# Claude Cowork (Desktop) install

> **Status:** The orq plugin is pending OAuth approval for the Claude Cowork marketplace. Once published, install via **Settings → Plugins → Browse** inside the Desktop app and search for "orq".

This document explains how the plugin works with Claude Cowork and how it differs from the Claude Code (CLI) install.

---

## How paths are resolved

The plugin manifest lives at `.claude-plugin/plugin.json` and declares:

```json
"skills": "./skills/",
"mcpServers": "./.mcp.json"
```

| Client | Path resolution base | `./skills/` resolves to |
|--------|----------------------|------------------------|
| Claude Code (CLI) | Repo root | `<repo-root>/skills/` ✓ |
| Claude Cowork (Desktop) | `plugin.json` location | `.claude-plugin/skills/` |

Claude Cowork validates that paths exist at load time; Claude Code does not. To make both work with the same `plugin.json`, the repo includes symlinks inside `.claude-plugin/`:

```
.claude-plugin/skills     → ../skills      (symlink)
.claude-plugin/.mcp.json  → ../.mcp.json   (symlink)
```

These symlinks allow Desktop to resolve `./skills/` and `./.mcp.json` correctly while keeping Claude Code behaviour unchanged.

> **Windows:** Symlinks require `core.symlinks=true` and either Developer Mode or Admin privileges. Run `git config --global core.symlinks true` before cloning and enable [Windows Developer Mode](https://learn.microsoft.com/en-us/windows/apps/get-started/enable-your-device-for-development).

---

## What the plugin provides (Claude Cowork)

| Component | Delivered via |
|-----------|--------------|
| Skills | `./skills/` → orq agent skills loaded automatically |
| MCP server | `./.mcp.json` → `orq-workspace` HTTP MCP server registered |

The MCP server requires `ORQ_API_KEY` to be set in your environment. Export it before launching the Desktop app:

```bash
export ORQ_API_KEY=your-key-here
```

---

## Verify

Once the plugin is installed and `ORQ_API_KEY` is set, ask in chat:

> List my orq.ai agents.

If the MCP server is reachable you'll see a live response from your workspace.
