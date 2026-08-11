# Codex install

Two paths:

- **Bundled plugin** — skills + MCP via the Codex marketplace.
- **Separate** — `npx skills` for skills, `~/.codex/config.toml` for MCP.

Make sure `ORQ_API_KEY` is exported (see [Prerequisites](../README.md#prerequisites)).

## Option A: Bundled plugin (skills + MCP)

Repo ships a Codex plugin at the repo root (`.codex-plugin/plugin.json`) and a repo-level marketplace at [.agents/plugins/marketplace.json](../.agents/plugins/marketplace.json) whose `source.path` points at the root.

Codex reads marketplace manifests from:
- `$REPO_ROOT/.agents/plugins/marketplace.json` (repo-level)
- `~/.agents/plugins/marketplace.json` (personal)

### Repo install — test the bundled marketplace in place

> **Windows:** Git symlinks require `core.symlinks=true` and either Developer Mode or Admin privileges. Without this, symlinked files are checked out as plain text and plugin resolution silently breaks. Run `git config --global core.symlinks true` before cloning, and enable [Windows Developer Mode](https://learn.microsoft.com/en-us/windows/apps/get-started/enable-your-device-for-development).

```bash
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills

# Launch Codex from the repo root so it picks up .agents/plugins/marketplace.json
codex
```

The manifest registers the skills folder and the `orq-workspace` MCP server automatically.

### Personal install — use the plugin outside this repo

```bash
# Clone the repo into Codex's personal plugins dir — the repo root is the
# plugin root (.codex-plugin/plugin.json, skills/, .mcp.json)
mkdir -p ~/.codex/plugins
git clone https://github.com/orq-ai/orq-skills.git ~/.codex/plugins/orq

# Reference it in your personal marketplace (use an absolute path —
# tilde expansion is not guaranteed inside JSON string values)
mkdir -p ~/.agents/plugins
cat > ~/.agents/plugins/marketplace.json <<JSON
{
  "name": "personal",
  "plugins": [
    {
      "name": "orq",
      "source": { "source": "local", "path": "$HOME/.codex/plugins/orq" }
    }
  ]
}
JSON
```

Restart Codex. See the [Codex plugin docs](https://developers.openai.com/codex/plugins/build) for the full plugin spec.

## Option B: Skills only (npx)

```bash
npx skills add orq-ai/orq-skills
```

Then register the MCP server separately (see below).

## MCP server (standalone)

Codex reads MCP servers from `~/.codex/config.toml`. Add:

```toml
[mcp_servers.orq-workspace]
type = "http"
url = "https://my.orq.ai/v2/mcp"
headers = { Authorization = "Bearer ${ORQ_API_KEY}" }
```

Restart Codex.

## Verify

Restart Codex, confirm the `orq` plugin/MCP appears, and ask *"List my orq.ai agents."*
