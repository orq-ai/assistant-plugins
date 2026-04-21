# Cursor install

Installs skills and MCP config. Repo root doubles as Cursor plugin (`.cursor-plugin/plugin.json` declares `./skills/` and `./.mcp.json`). Cursor loads local plugins from `~/.cursor/plugins/local/<name>`.

## Install

```bash
# 1. Clone the repo
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills

# 2. Symlink into Cursor's local plugins directory
mkdir -p ~/.cursor/plugins/local
ln -s "$(pwd)" ~/.cursor/plugins/local/orq

# 3. Export your API key (the MCP config references ${ORQ_API_KEY})
export ORQ_API_KEY=your-key-here
```

Restart Cursor (or run **Developer: Reload Window**).

## Verify

- **Settings → Rules** lists the `orq` skills under *Agent Decides*.
- **Settings → Features → Model Context Protocol** lets you enable `orq-workspace`.
- Ask in chat: *"List my orq.ai agents."*

## Testing as a Cursor marketplace

To smoke-test the repo as a Cursor team marketplace before submitting to [cursor.com/marketplace/publish](https://cursor.com/marketplace/publish):

1. Add a `.cursor-plugin/marketplace.json` at the repo root.
2. Import the repo via **Dashboard → Settings → Plugins → Team Marketplaces → Import** with your GitHub URL.

Team marketplaces require a Teams or Enterprise plan.
