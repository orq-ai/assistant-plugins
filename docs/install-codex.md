# Codex install

Installs skills and MCP config. Repo ships a Codex plugin at [plugins/orq](../plugins/orq) (`plugins/orq/.codex-plugin/plugin.json`) and a repo-level marketplace at [.agents/plugins/marketplace.json](../.agents/plugins/marketplace.json).

Codex reads marketplace manifests from two locations:
- `$REPO_ROOT/.agents/plugins/marketplace.json` (repo-level)
- `~/.agents/plugins/marketplace.json` (personal)

## Repo install — test the bundled marketplace in place

```bash
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills
export ORQ_API_KEY=your-key-here

# Launch Codex from the repo root so it picks up .agents/plugins/marketplace.json
codex
```

Restart Codex. Verify the `orq` plugin appears in the plugin directory — the manifest registers the skills folder and the `orq-workspace` MCP server automatically.

## Personal install — use the plugin outside this repo

```bash
# Copy the plugin bundle into Codex's personal plugins dir
mkdir -p ~/.codex/plugins
cp -r plugins/orq ~/.codex/plugins/orq

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

export ORQ_API_KEY=your-key-here
```

Restart Codex. See the [Codex plugin docs](https://developers.openai.com/codex/plugins/build) for the full plugin spec.

## Verify

Restart Codex from the repo root, confirm the `orq` plugin appears in the plugin directory, and ask *"List my orq.ai agents."*
