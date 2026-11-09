---
name: orq-quickstart
description: >
  Onboarding for orq.ai in any coding agent — get an API key, register the
  orq.ai MCP server for the harness you are running in, prove the connection
  with a real workspace read, show a workspace snapshot, list the installed orq
  skills, and route the user to a first task. Use when the user is new to
  orq.ai, asks to set up or verify orq.ai, says "get started", "quickstart" or
  "onboard me", or another orq skill fails because the MCP server is missing or
  the credentials are rejected. Do NOT use to drive the orq CLI (use orq-cli)
  or to instrument an application with tracing (use orq-setup-observability).
allowed-tools: Bash, Read, AskUserQuestion, mcp__orq-workspace__search_entities
---

# orq.ai Quickstart

You are onboarding a user to orq.ai from inside their coding agent. Get them from
nothing to a verified connection and a first task, in whatever harness you are
running in (Claude Code, Codex, Cursor, Gemini CLI, Copilot, or another
MCP-capable agent).

Where this file says "ask the user", use your harness's question tool if it has
one, otherwise ask in plain text and wait for the answer.

## Rules

- **Never ask the user to paste an API key into the chat.** The key goes into
  their shell profile or their harness's credential prompt, never into the
  transcript.
- **Never print the key.** Check presence without expanding it:

  ```bash
  [ -n "${ORQ_API_KEY:-}" ] && echo "ORQ_API_KEY: set" || echo "ORQ_API_KEY: unset"
  ```

- **A successful read is the only proof the setup works.** An unset
  `ORQ_API_KEY` does not mean the setup is broken: the Claude Code plugin keeps
  the key in the system credential store, and some MCP connections authenticate
  another way. Go to step 4 and let the read decide.
- **Do not assume the MCP server's name.** The plugin and the install docs
  register it as `orq-workspace`, but users and other installers may name it
  differently. Look for the orq.ai server's tools (for example
  `search_entities`) under whatever server name your harness shows.

## 1. Find out where the user stands

Greet the user in one line: orq.ai skills cover building, evaluating and
improving LLM agents and pipelines. Then ask which applies:

- **New to orq.ai** → step 2
- **Has an API key, not set up in this agent** → step 3
- **Already set up** → step 4

## 2. Account and API key

Tell the user:

1. Go to [my.orq.ai](https://my.orq.ai) and create an account or sign in.
2. Open **Settings → API Keys** and create a key.
3. Keep it ready for step 3. Do not paste it here.

## 3. Register the orq.ai MCP server

The server is HTTP at `https://my.orq.ai/v2/mcp`, authenticated with
`Authorization: Bearer <ORQ_API_KEY>`. Most harnesses read the key from the
`ORQ_API_KEY` environment variable, so first have the user add it to their
shell profile themselves (zsh: `~/.zshrc`, bash: `~/.bashrc`):

```bash
export ORQ_API_KEY=your-key-here
```

Then register the server for the harness you are running in:

| Harness | How |
|---|---|
| Claude Code (plugin) | `/plugin marketplace add orq-ai/assistant-plugins`, then `/plugin install orq-mcp@orq-claude-plugin`. The plugin asks for the key at install; change it later with `/plugin configure`. |
| Claude Code (no plugin) | `claude mcp add --transport http orq-workspace https://my.orq.ai/v2/mcp --header "Authorization: Bearer ${ORQ_API_KEY}"` |
| Codex | `codex mcp add orq-workspace --url https://my.orq.ai/v2/mcp --bearer-token-env-var ORQ_API_KEY` |
| Cursor | Add an `orq-workspace` entry with that `url` and `headers` to `~/.cursor/mcp.json` |
| Anything else | Add the same `url` and `Authorization` header to the client's MCP config |

Full per-harness instructions:
[MANUAL_INSTALL.md](https://github.com/orq-ai/assistant-plugins/blob/main/MANUAL_INSTALL.md).

Running a registration command yourself is fine when the key is already in the
environment. Editing the shell profile is the user's job, because the edit would
contain the key.

A new environment variable or MCP server usually needs a restart of the agent
session. If it does, tell the user to restart and then ask you to "run the orq
quickstart" again. Stop here until they come back.

## 4. Verify with a real read

Call the orq.ai MCP tool `search_entities` with `type: "agent"`.

- **Returns a list (even an empty one)** → connected. Go to step 5.
- **Auth error (401/403)** → the key is wrong, expired or project-scoped. Send
  the user to [my.orq.ai](https://my.orq.ai) → **Settings → API Keys** for a new
  workspace key, then step 3.
- **No orq.ai MCP tools available** → the server is not registered in this
  session. Go to step 3.

If MCP is impossible in this harness but a shell is available, the `orq-cli`
skill covers verifying the connection through the `orq` CLI instead.

## 5. Workspace snapshot

Run `search_entities` for `agent`, `deployment`, `prompt`, `dataset` and
`experiment`, in parallel if your harness allows. Show one short table with up
to five names per type.

Each call returns one page. When a response has `has_more: true`, write "first
page shown, more exist" for that type. Never present a page size as a total.

An empty workspace is normal for new users. Say so and move on.

## 6. Installed orq skills

List the orq skills installed in this session as a table with two columns:
**Skill** and **When to use**, the second taken from each skill's own
description. Read them from what is installed. Do not recite a list from
memory, because installs differ.

Then explain the usual loop in one or two lines: `orq-analyze-traces` finds
failure modes, `orq-improve-agent` fixes them, `orq-run-experiment` measures the
fix, and `orq-build-evaluator` adds a judge when a failure mode needs one.
Skills trigger from plain-language requests, so the user does not need to
remember their names.

## 7. Models

Calls fail until the workspace has a model provider enabled. If the user plans
to call models and is new, point them to [my.orq.ai](https://my.orq.ai) →
**Model Garden → Providers**: add a provider key, then toggle the models on.
The MCP model list shows the catalogue, not what this workspace has enabled, so
do not tell the user models are enabled based on it.

## 8. First task

Ask what they want to do, and hand off:

| Goal | Skill |
|---|---|
| Build an agent | `orq-build-agent` |
| Evaluate an existing agent | `orq-analyze-traces`, then `orq-build-evaluator`, then `orq-run-experiment` |
| Debug production traces | `orq-analyze-traces` |
| Improve an agent's instructions | `orq-improve-agent` |
| Generate test data | `orq-generate-synthetic-dataset` |
| Add tracing to an app | `orq-setup-observability` |
| Work from the terminal | `orq-cli` |

Close with the docs link: [docs.orq.ai](https://docs.orq.ai/docs/quick-start).
