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

Paths resolve from the plugin root, which is the repository root, so `./skills/` is `<repo-root>/skills/` and `./.mcp.json` is `<repo-root>/.mcp.json`. No symlinks inside `.claude-plugin/` are needed (Anthropic's plugin directory validator rejects them).

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
