# Wiring coding agents to orq

Companion to [`../SKILL.md`](../SKILL.md). Verified against orq CLI 5.1.0 on
2026-08-31, from `--help` and the upstream CHANGELOG.


`connect`, `disconnect`, `setup`, `launch` and `update` are hand-written, not
generated: they **write to local config files** rather than calling the
platform. Treat them as changes to the user's machine, not as reads.

| Command | What it does |
|---|---|
| `orq setup` | Authenticate, mint a gateway key, and wire the detected agents |
| `orq connect [agent…] [capability…]` | Wire specific agents/capabilities |
| `orq connect --status` | Report what is wired; changes nothing |
| `orq connect --dry-run` | Show the files that would change; writes nothing |
| `orq disconnect` | Undo what `connect` wrote (names every file first) |
| `orq launch <agent>` | Run one agent through the orq AI Router, no persistent change |

Agents: `claude`, `codex`, `kilo`, `kimi`, `opencode`, `pi`. Capabilities:
`gateway` (routes model calls, needs a credential), `skills` (installs the orq
skills, offline, no credential), `mcp` (writes the MCP server URL only — the
agent does its own OAuth), and `tracing`, which parses but **is not implemented
yet**.

**Always start with `--status` or `--dry-run`.** These commands edit files the
user's other tools depend on, so confirm before writing.

Two traps worth knowing:

- **`orq skills` is a different noun.** It manages Skill entities on the orq
  platform. The `skills` *capability* here installs SKILL.md files into agents'
  skills directories. See `orq-manage-skills` for the platform entity.
- **`--local` only scopes `mcp`.** Everything else is machine-wide, and
  `--local` from `$HOME` is refused, since the `~/.mcp.json` it would write
  would follow the user into every session started from home.

`orq launch` propagates the launched agent's exit status verbatim, so it is the
one command whose exit code is not the CLI's own contract (`0` success, `1` any
failure, `130` SIGINT, `143` SIGTERM). Any value from `2` to `255` can come
back: `127` when the agent binary does not start, `128+signum` when it is killed
by a signal, and otherwise whatever the agent itself returned. Treat any
non-zero as failure rather than matching specific values.

## Credentials these commands mint

`orq setup` and `orq connect` mint an API key and write it into agent config
files. Two things follow from that:

- **The minted key is gateway-scoped**, not workspace-wide — the gateway domains
  plus read-only model listing. It is stored as `gateway_key` in
  `credentials.json`, separate from a key you brought yourself (`api_key`), so
  it does not shadow your login for `orq prompts list` and friends.
- **Keys minted by v4.13.10 carry every permission in the workspace**, in
  cleartext, in agent config files — including `member`, `billing`, `sso` and
  `workspace`. Re-running `orq setup` does **not** replace one; it reuses what
  is saved. To move off it: `orq auth logout`, log in again, `orq setup`, then
  revoke the old key in the dashboard.

Minted keys now expire after 90 days. `orq disconnect` and `orq auth logout`
remove the local wiring but do **not** revoke the key server-side — it stays
live until its own expiry, and anywhere else it was copied keeps working. Both
commands print the `orq api-keys delete <id>` line that actually revokes it.

