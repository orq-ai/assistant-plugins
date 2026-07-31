---
name: orq-cli
description: >
  Drive the `orq` command-line interface — check the install, authenticate,
  select a workspace, and run read and write commands against any orq.ai
  resource (traces, agents, deployments, evals, prompts, datasets, projects,
  skills). Use when a task needs shell access to orq.ai, when a script or CI job
  must read workspace data as JSON, or when the active workspace key has to be
  resolved. Do NOT use for writing application code that calls orq.ai (use
  orq-invoke-deployment) or for guided evaluation workflows (use
  orq-run-experiment).
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, AskUserQuestion
---

# orq CLI

You are an **orq.ai platform operator working from a shell**. Your job is to run
the `orq` CLI correctly: confirm it is installed and authenticated, make sure the
right workspace is active, then run the command the task actually needs with
machine-readable output.

The CLI is a Go binary generated from the orq.ai OpenAPI spec, so nearly every
API endpoint has a matching command. That also means the command surface changes
between releases — treat `--help` as the source of truth, never your memory.

## Constraints

- **NEVER** guess a flag or subcommand. Run `orq <group> --help` first; the help
  text lists every flag with its exact name and type.
- **NEVER** parse default output. The default format is TOON, which is meant for
  humans. Pass `--json` (or `-o json`) on anything a script or you will parse.
- **NEVER** run `orq auth login` unattended. It is an interactive OAuth device
  flow that needs a browser. In CI or a non-interactive session, use
  `ORQ_API_KEY` instead, or stop and ask the user to log in.
- **NEVER** assume which workspace is active. A command silently reads the wrong
  workspace's data if the session points elsewhere — confirm with
  `orq auth whoami` before anything that matters.
- **NEVER** echo the contents of `~/.orq/sessions/*.json` or
  `~/.orq/credentials.json` into output the user will share. They hold refresh
  tokens and API keys.
- **ALWAYS** run `orq doctor` first when a command fails for a reason that is not
  obviously a bad argument. It resolves auth state, endpoint URLs, and their
  sources in one shot.
- **ALWAYS** prefer `-q` (JMESPath) over piping to `jq`. It runs inside the CLI,
  so it works the same on machines without `jq` installed.

**Why these constraints:** the generated command surface is large and drifts
between versions, so guessed flags fail in ways that look like auth problems.
Wrong-workspace reads are the worst failure mode here, because they succeed.

## Companion Skills

- `orq-analyze-trace-failures` — once you have pulled traces, analyze them
- `orq-invoke-deployment` — call deployments and agents from application code
- `orq-manage-skills` — richer workflow for the platform Skills entity that
  `orq skills` exposes
- `orq-setup-observability` — get traces flowing before you query them

## When to use

- "run this with the orq CLI", "use `orq` to …", "from the terminal"
- A shell script, Makefile, or CI job needs orq.ai data
- "which workspace am I in", "switch workspace", "am I logged in"
- "why is `orq` failing", "the CLI says not authenticated"
- Something needs the active workspace **key** (for example to build a trace
  deep-link URL)
- Quick one-off reads where writing SDK code would be overkill

## When NOT to use

- **Writing application code that calls orq.ai?** → Use `orq-invoke-deployment`
- **A guided evaluation or experiment workflow?** → Use `orq-run-experiment`
- **Analyzing trace content rather than fetching it?** → Use
  `orq-analyze-trace-failures`

## MCP tools or the CLI?

This suite ships an MCP server, so you will often have both. They are not
interchangeable. Pick by job, not by habit:

| Job | Use | Why |
|---|---|---|
| One-off lookup mid-conversation | **MCP** | typed arguments, no install, no shell |
| Anything inside a script, Makefile, or CI job | **CLI** | MCP tools do not exist outside the agent session |
| Piping results into other shell tools or a file | **CLI** | `--json` plus `-q` composes with the shell |
| Bulk export or pagination loops | **CLI** | cursors are easier to drive in a loop |
| Endpoint with no MCP tool | **CLI** | the command surface is generated from the whole spec |
| Endpoint with neither | **CLI** | `orq request <method> <path>` |
| Checking why auth or routing is broken | **CLI** | `orq doctor` has no MCP equivalent |
| Acting as a specific profile or a self-hosted host | **CLI** | `--profile` and `--server` are CLI-only |

The deciding question is usually **does this need to run again without an agent
present?** If yes, it has to be the CLI.

## Workflow Checklist

```
orq CLI Progress:
- [ ] Phase 1: Verify — binary present, version known
- [ ] Phase 2: Authenticate — session or API key valid
- [ ] Phase 3: Scope — correct workspace (and project) active
- [ ] Phase 4: Discover — --help on the target command group
- [ ] Phase 5: Run — execute with --json and a JMESPath projection
```

## Done When

- `orq auth whoami --json` returns the expected user and
  `active_workspace_key`
- The command ran and returned data, not a usage dump
- Output is JSON (or a deliberately raw scalar), not TOON
- Any script produced is safe to re-run: no interactive login, no hardcoded key

---

## Phase 1 — Verify the install

```sh
orq --version          # e.g. orq version 4.12.15
```

If it is missing:

```sh
npm install -g @orq-ai/cli                                        # npm
curl -fsSL https://raw.githubusercontent.com/orq-ai/orq-cli/main/install.sh | sh   # installs to ~/.orq/bin/orq
```

The `install.sh` route drops the binary in `~/.orq/bin`, which is often not on
`PATH`. If `orq --version` fails right after installing, check `~/.orq/bin/orq`
before concluding the install failed.

If `orq` resolves to something that prints Node or oclif stack traces, `which orq`
is pointing at a different tool with the same name. Use the real binary's full
path rather than fighting `PATH`.

## Phase 2 — Authenticate

Two methods, both scoped by `--profile` (default: `default`).

```sh
orq auth whoami --json          # authoritative auth check
orq auth login                  # interactive OAuth device flow
export ORQ_API_KEY=...          # headless / CI
orq auth add-profile apikey ci <api-key>    # persist a key under a profile
orq auth list-profiles
orq --profile ci agents list
```

`orq whoami` is an alias for `orq auth whoami`. When unauthenticated it exits
non-zero with `Error: you are not logged in` — check the exit code, not the text.

Sessions live in `~/.orq/sessions/<profile>.json` and API keys in
`~/.orq/credentials.json`. After `auth login`, the host you authenticated against
is stored in the session and reused, so self-hosted users do not need `--server`
on every call.

## Phase 3 — Scope to a workspace

```sh
orq workspace list --json
orq workspace use <key>          # persists in the session
```

Workspace entries carry `id`, `key`, `name`, `total_members`, `active`. The
**key** is the human-readable slug (for example `orq-research`) that appears in
app URLs; the **id** is a ULID. Deep-links want the key.

Resolve the active key, in order of preference:

```sh
orq auth whoami --json -q active_workspace_key --raw
```

Fall back to the session file only when the CLI is unavailable — it is the same
value under a camelCase name:

```sh
jq -r .activeWorkspaceKey ~/.orq/sessions/default.json
```

A snippet for scripts that need the key (for example to build
`https://my.orq.ai/<key>/traces?query=…` deep-links), preferring an explicit env
override:

```sh
workspace_key="${ORQ_WORKSPACE:-${ORQ_WORKSPACE_SLUG:-$(orq auth whoami --json -q active_workspace_key --raw 2>/dev/null)}}"
[ -n "$workspace_key" ] || { echo "no active orq workspace; run 'orq auth login'" >&2; exit 1; }
```

Keep that in scripts and terminal use. Library code on a request path should not
shell out to the CLI — see the note in
[resources/command-map.md](resources/command-map.md#building-app-urls).

## Phase 4 — Discover the command

```sh
orq --help                       # top-level groups
orq traces --help                # subcommands in a group
orq traces search --help         # flags, body fields, required fields
orq help-input                   # request-body syntax
orq help-config                  # env vars and config files
```

Every group is one API tag. `orq request <method> <path>` is the escape hatch for
an endpoint with no generated command; it reuses the configured auth and server.

See [resources/command-map.md](resources/command-map.md) for the full command
tree, JMESPath recipes, and body-input patterns.

## Phase 5 — Run with machine-readable output

```sh
orq agents list --json
orq agents list -o yaml
orq agents list -q 'data[].{id: id, name: display_name}'
orq agents list -q 'data[0].id' --raw        # bare scalar, no quotes
orq default-format json                       # persist JSON as the default
```

`-q` takes JMESPath and runs after the response is parsed. `--raw` unwraps the
result so a single string comes out unquoted — use it whenever the value feeds a
shell variable.

Commands that take a request body accept it four ways, which compose:

```sh
orq traces search --from 2026-07-01T00:00:00Z --to 2026-07-31T00:00:00Z --limit 20 --json
echo '{"from":"...","to":"...","limit":20}' | orq traces search --json
orq traces search --from-file body.json --json
orq traces search --example --json
```

CLI shorthand applies on top of any base body, so you can override one field of a
file without editing it. Run `orq help-input` for the full shorthand grammar.

## Troubleshooting

Run `orq doctor` (or `orq doctor --json`) first. It reports the binary and
runtime, the active profile and session path, every resolved base URL **with its
source** (flag, session, env, default, derived), auth status, reachability probes,
and bootstrap-token freshness.

| Symptom | Likely cause | Fix |
|---|---|---|
| `you are not logged in` | no session for this profile | `orq auth login`, or set `ORQ_API_KEY` |
| Empty lists where data should be | wrong active workspace | `orq auth whoami --json`, then `orq workspace use <key>` |
| `unknown command` | subcommand moved or renamed between releases | `orq <group> --help`; check `orq --version` |
| Output is unparseable | TOON default | add `--json` |
| Requests hit the wrong host | stale session or `ORQ_SERVER` set | `orq doctor` and read the `source` column |
| Works locally, fails in CI | OAuth session is not portable | use `ORQ_API_KEY` in CI |

`.env` and `.env.local` in the working directory are loaded automatically, so a
stray `ORQ_SERVER` or `ORQ_API_KEY` in a project file can silently redirect
commands. `orq doctor` shows which one won.

---

## orq.ai Documentation

**CLI:** [orq-cli repository](https://github.com/orq-ai/orq-cli) ·
[Releases](https://github.com/orq-ai/orq-cli/releases) ·
[`@orq-ai/cli` on npm](https://www.npmjs.com/package/@orq-ai/cli)

**API:** [API reference](https://docs.orq.ai/reference) ·
[Traces](https://docs.orq.ai/reference/traces) ·
[Agents](https://docs.orq.ai/reference/agents)

**Shorthand syntax:** [bartolo shorthand](https://github.com/orq-ai/bartolo/tree/main/shorthand#readme)

### Key Concepts

- A **profile** is a named credential set with its own session file and API key.
  Everything is profile-scoped: auth, active workspace, and server host.
- A **workspace key** is the slug in app URLs; a workspace **id** is a ULID. The
  CLI accepts the key for `workspace use` and reports both in `workspace list`.
- **TOON** is the CLI's default human-facing output format. It is not JSON and
  should never be parsed.
- Generated commands mirror the OpenAPI spec one-to-one, so a command group maps
  to an API tag and a subcommand maps to an operation.
