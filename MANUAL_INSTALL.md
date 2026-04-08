# Manual Install

This page is for users who want to install orq-skills **without** the `npx skills` CLI, or who need to register the orq.ai MCP server on a tool other than Claude Code.

If you're on Claude Code, you should use the [plugin install](README.md#option-a--claude-code-plugin-recommended) instead — it's one command.
If you're on another MCP-capable agent, the easiest path is still `npx skills add orq-ai/orq-skills` followed by Part 2 below (MCP registration).

Use this document when:

- The `skills` CLI doesn't support your tool yet
- You want to symlink skills from a checkout for local development
- You're debugging an install and want to see exactly where files should land

---

## Prerequisites

- An [orq.ai](https://orq.ai) account and an API key from [Settings → API Keys](https://my.orq.ai)
- `ORQ_API_KEY` exported in your shell:

  ```bash
  export ORQ_API_KEY=your-key-here
  ```

---

## Part 1 — Install the skills

Skills are folders containing a `SKILL.md` file. Manual installation = copy (or symlink) those folders into whatever directory your agent scans for skills.

Start by cloning the repo somewhere persistent:

```bash
git clone https://github.com/orq-ai/orq-skills.git
cd orq-skills
```

### Claude Code

Skills live in `.claude/skills/` (project) or `~/.claude/skills/` (global).

```bash
# Global install — all skills
mkdir -p ~/.claude/skills
cp -r skills/* ~/.claude/skills/
```

Symlink a single skill for development (edits reflect immediately):

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/build-agent" ~/.claude/skills/build-agent
```

### GitHub Copilot

Copilot loads skills from `.github/skills/` (project) or `~/.copilot/skills/` (global), and also reads `.claude/skills/` for compatibility.

```bash
mkdir -p ~/.copilot/skills
cp -r skills/* ~/.copilot/skills/
```

### Cline

Enable the experimental **Skills** feature in Cline settings first. Cline then loads from `.cline/skills/` (project) or `~/.cline/skills/` (global).

```bash
mkdir -p ~/.cline/skills
cp -r skills/* ~/.cline/skills/
```

### OpenAI Codex

Codex loads skills from `.codex/skills/` (project) or `~/.codex/skills/` (global).

```bash
mkdir -p ~/.codex/skills
cp -r skills/* ~/.codex/skills/
```

### Windsurf

Windsurf uses `.windsurf/rules/` for workspace-scoped rules rather than a skills directory. Copy each skill's `SKILL.md` in as a rule file:

```bash
mkdir -p .windsurf/rules
for d in skills/*/; do
  name=$(basename "$d")
  cp "$d/SKILL.md" ".windsurf/rules/${name}.md"
done
```

### Other agents

Any agent that supports the [Agent Skills standard](https://agentskills.io) can use these skills — place the folders in whichever directory your agent scans. Browse compatible agents at [skills.sh](https://skills.sh).

---

## Part 2 — Register the orq.ai MCP server

Most of the skills call the `orq-workspace` MCP server to read and write resources in your workspace. It's an HTTP MCP server:

- **URL:** `https://my.orq.ai/v2/mcp`
- **Auth header:** `Authorization: Bearer ${ORQ_API_KEY}`

The exact config file varies per tool.

### Claude Code

```bash
claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp \
  --header "Authorization: Bearer ${ORQ_API_KEY}"
```

### Cursor

Edit `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project):

```json
{
  "mcpServers": {
    "orq-workspace": {
      "url": "https://my.orq.ai/v2/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_ORQ_API_KEY"
      }
    }
  }
}
```

### Generic HTTP MCP config

Most MCP-capable tools (Gemini CLI, Codex, Cline, Copilot CLI, Windsurf, …) accept a JSON definition along these lines. Drop it into your tool's MCP config file:

```json
{
  "mcpServers": {
    "orq-workspace": {
      "url": "https://my.orq.ai/v2/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_ORQ_API_KEY"
      }
    }
  }
}
```

Some tools use a slightly different key name for HTTP servers (e.g. `httpUrl` instead of `url`) or load the config from a TOML file rather than JSON. Check your tool's MCP documentation for the exact path and schema — the URL and header are the same everywhere.

> **Tip:** Don't hard-code the API key. If your tool supports env expansion in the config, reference `${ORQ_API_KEY}` instead of pasting the secret.

---

## Verifying

Once skills and MCP are both in place, ask your agent something like:

> List my orq.ai agents.

If the MCP server is registered and your key is valid, you'll see a live response from your workspace. If not:

- Confirm `ORQ_API_KEY` is set in the shell your agent inherited
- Re-check the `Authorization` header in your MCP config
- Make sure the skill folders actually landed where your agent scans (run `ls` on the directory)

For Claude Code users, `/orq:quickstart` runs all of these checks automatically.
