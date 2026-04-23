# Cursor install

Two paths:

- **Bundled plugin** — skills + MCP in one step (clone + symlink).
- **Separate** — `npx skills` for skills, manual JSON for MCP.

Make sure `ORQ_API_KEY` is exported (see [Prerequisites](../README.md#prerequisites)) — the MCP config references `${ORQ_API_KEY}`.

## Option A: Bundled plugin (skills + MCP)

Repo root doubles as Cursor plugin (`.cursor-plugin/plugin.json` declares `./skills/` and `./.mcp.json`). Cursor loads local plugins from `~/.cursor/plugins/local/<name>`.

> **Windows:** Git symlinks require `core.symlinks=true` and either Developer Mode or Admin privileges. Without this, symlinked files are checked out as plain text and plugin resolution silently breaks. Run `git config --global core.symlinks true` before cloning, and enable [Windows Developer Mode](https://learn.microsoft.com/en-us/windows/apps/get-started/enable-your-device-for-development).

```bash
# 1. Clone the repo
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills

# 2. Symlink into Cursor's local plugins directory
mkdir -p ~/.cursor/plugins/local
ln -s "$(pwd)" ~/.cursor/plugins/local/orq
```

Restart Cursor (or run **Developer: Reload Window**).

## Option B: Skills only (npx)

```bash
npx skills add orq-ai/orq-skills
```

Writes skills to `.cursor/rules/`. Then add the MCP server separately (see below).

## MCP server (standalone)

Cursor reads MCP servers from `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` in your project. Add:

```json
{
  "mcpServers": {
    "orq-workspace": {
      "type": "http",
      "url": "https://my.orq.ai/v2/mcp",
      "headers": { "Authorization": "Bearer ${ORQ_API_KEY}" }
    }
  }
}
```

Restart Cursor, then enable `orq-workspace` under **Settings → Features → Model Context Protocol**.

## Verify

- **Settings → Rules** lists the `orq` skills under *Agent Decides*.
- **Settings → Features → Model Context Protocol** lets you enable `orq-workspace`.
- Ask in chat: *"List my orq.ai agents."*

## Testing as a Cursor marketplace

To smoke-test the repo as a Cursor team marketplace before submitting to [cursor.com/marketplace/publish](https://cursor.com/marketplace/publish):

1. Add a `.cursor-plugin/marketplace.json` at the repo root.
2. Import the repo via **Dashboard → Settings → Plugins → Team Marketplaces → Import** with your GitHub URL.

Team marketplaces require a Teams or Enterprise plan.
