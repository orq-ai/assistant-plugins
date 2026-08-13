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
allowed-tools: Bash(orq:*), Bash(jq:*), Bash(curl:*), Bash(npm:*), Read, Write, Edit, Grep, Glob, WebFetch, AskUserQuestion, mcp__orq-workspace__search_docs, mcp__orq-workspace__search_entities
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

- **NEVER** run any `orq` command with `--verbose` in output anyone else will
  see. It prints the whole profile config to stdout, including **every stored
  API key in plaintext**. If you already ran it, tell the user to rotate those
  keys.
- **NEVER** trust the exit code for auth. `orq auth whoami`, `orq workspace
  list`, and `orq doctor` all exit **0** when unauthenticated. Read the payload:
  `authenticated` from `whoami`, `auth.status` from `doctor`.
- **NEVER** guess a flag or subcommand. Run `orq <group> --help` first; the help
  text lists every flag with its exact name and type.
- **NEVER** parse default output. The default format is TOON, which is meant for
  humans. Pass `--json` (or `-o json`) on anything a script or you will parse.
- **NEVER** run `orq auth login` unattended. It is an interactive OAuth device
  flow that needs a browser. If nobody can complete it, stop and say so —
  `ORQ_API_KEY` is **not** a substitute for the commands that need a session
  (see the auth matrix below).
- **NEVER** assume which workspace is active. `ORQ_API_KEY` **overrides an active
  OAuth session**, and `.env` autoloads, so a stray key in a project file
  silently redirects every read to that key's workspace while `whoami` keeps
  reporting the one you logged into. Confirm with a count before trusting data
  (see "Which workspace am I really reading?").
- **NEVER** report a count or a "complete" list from a default page. Most list
  commands cap at 10 or 25 and set `has_more: true` with nothing in the output
  to signal it. Check `has_more` or pass `--limit` (see "Lists truncate
  silently").
- **NEVER** treat an empty or small result as an answer. `0` rows is the
  characteristic symptom of a project-scoped key or a wrong workspace, and it
  reads exactly like a legitimately empty workspace. Rule out credentials first.
- **NEVER** take `data[0]` from `traces search` as the latest trace. Results are
  unordered; only `[{"field":"end_time","order":"desc"}]` sorts, and nothing else
  is accepted.
- **NEVER** echo the contents of `~/.orq/sessions/*.json`,
  `~/.orq/credentials.json`, or `~/.orq/config.json`. They hold refresh tokens
  and API keys.
- **NEVER** print `$ORQ_API_KEY` to check whether it is set. Test presence
  without expanding the value, and beware that an unquoted expansion inside a
  larger command still lands in the transcript:

  ```sh
  [ -n "${ORQ_API_KEY:-}" ] && echo "ORQ_API_KEY: set" || echo "ORQ_API_KEY: unset"
  ```
- **ALWAYS** project with the global `-j/--jmespath`, never `--query`. On search
  commands `--query` is the body's full-text search field — it accepts a
  JMESPath expression and silently returns zero rows (see "`--query` is not the
  projection flag").
- **ALWAYS** prefer `-j` (JMESPath) over piping to `jq` **when it can express
  the projection**. It runs inside the CLI, so it needs no `jq` installed.
  Reach for `jq` when you need something JMESPath lacks (`@uri` encoding, text
  munging), or when reading a file rather than a command's output.

**Why these constraints:** the generated command surface is large and drifts
between versions, so guessed flags fail in ways that look like auth problems.
Auth and output failures here are quiet: wrong-workspace reads succeed, logged-out
commands exit 0, and one shadowed flag returns an empty list instead of an error.

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
| Piping results into other shell tools or a file | **CLI** | `--json` plus `-j` composes with the shell |
| Bulk export or pagination loops | **CLI** | cursors are easier to drive in a loop |
| Endpoint with no MCP tool — or no generated command either | **CLI** | the command surface is generated from the whole spec; `orq request <method> <path>` covers the rest |
| Checking why auth or routing is broken | **CLI** | `orq doctor` has no MCP equivalent |
| Acting as a specific profile or a self-hosted host | **CLI** | `--profile` and `--server` are CLI-only |
| Running experiments (create/run/export) | **MCP or evaluatorq SDK** | the CLI has no `experiments` group |
| Finding an entity by name, browsing docs | **MCP** | `search_entities` / `search_docs` have no CLI equivalent |
| Schedules, identities, projects, API keys, webhooks, KBs, memory stores, files | **CLI** | no MCP tools exist for these areas |

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

- Credentials confirmed by output, not exit code: `orq auth whoami --json` shows
  the expected user and `active_workspace_key` (session), or a resource command
  returns real data (API key)
- The command ran and returned data, not a usage dump
- Output is JSON (or a deliberately raw scalar), not TOON
- Any script produced is safe to re-run: no interactive login, no hardcoded key

---

## Phase 1 — Verify the install

```sh
orq --version          # e.g. orq version 4.13.0
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

There are two credential types and **they are not interchangeable**. This is the
single most confusing thing about the CLI, so check it before anything else.

| Command family | `ORQ_API_KEY` | OAuth session (`orq auth login`) |
|---|---|---|
| Generated resource commands (`agents`, `traces`, `projects`, `prompts`, `skills`, `datasets`, …) | works | works |
| Built-ins: `auth whoami`, `workspace list`, `workspace use` | **fails** | works |
| `doctor`'s `auth` block | **reports `missing`** | reports real state |

Verified live: with a valid `ORQ_API_KEY` exported, `orq agents list`
returns data while `orq auth whoami` prints `Error: you are not logged in` and
`orq doctor --json -j 'auth.status' --raw` prints `missing`.

**The practical consequences:**

- A key-only setup is fine for reading and writing resources, and cannot tell you
  who you are or which workspace is active.
- `doctor` saying `auth.status: missing` does **not** mean the CLI is broken.
  Confirm by running an actual resource command before chasing auth.
- Anything needing the workspace key requires an interactive login. There is no
  key-based path to it.
- **Sessions are short-lived.** `orq doctor` carries a `bootstrap_token` check
  whose `details.expires_at` is roughly an hour out from login. When it lapses on
  a machine that also has `ORQ_API_KEY` set, `whoami` and `workspace *` start
  reporting "not logged in" while resource commands keep working — the same
  split as a key-only setup, arriving mid-session. Suspect this before suspecting
  a wrong `--profile`:

  ```sh
  orq doctor --json -j "checks[?id=='bootstrap_token'].details.expires_at" --raw
  ```

```sh
orq auth login                              # interactive OAuth device flow
export ORQ_API_KEY=...                      # headless / CI, resource commands only
orq auth add-profile apikey ci <api-key>    # persist a key under a profile
orq auth list-profiles
orq --profile ci agents list
```

Checking state, given that all of these exit 0 either way:

```sh
orq auth whoami --json -j authenticated --raw    # true | (error text if no session)
orq doctor --json -j 'auth.status' --raw         # ok | missing | invalid
orq agents list --json -j 'length(data)' --raw   # the only real proof a key works
```

`orq whoami` is an alias for `orq auth whoami`.

Sessions live in `~/.orq/sessions/<profile>.json` and API keys in
`~/.orq/credentials.json` / `~/.orq/config.json`. After `auth login`, the host you
authenticated against is stored in the session and reused, so self-hosted users do
not need `--server` on every call.

## Phase 3 — Scope to a workspace

```sh
orq workspace list --json
orq workspace use <key>          # persists in the session
```

Workspace entries carry `id`, `key`, `name`, `total_members`, `active`. The
**key** is the human-readable slug (for example `orq-research`) that appears in
app URLs; the **id** is a UUID (`624ccbbd-a482-…`). Deep-links want the key — a
UUID in a Studio route gives an inaccessible page even when the API can read the
entity. Note resource ids elsewhere (agents, spans) are ULIDs; workspaces are the
exception.

**Both of these commands require an OAuth session.** With only `ORQ_API_KEY`
set they fail with `Error: you are not logged in`, at exit 0. So workspace
selection is not available to key-only setups at all, and neither is reading the
active key.

### Which workspace am I really reading?

`ORQ_API_KEY` **wins over an active session** for resource commands. Verified: a
deliberately invalid `ORQ_API_KEY` alongside a healthy session returns HTTP 401
rather than falling back to the session.

That produces the nastiest failure in this skill, because nothing errors:

- `orq auth whoami` reports the workspace you logged into — it only reads the
  session.
- `orq agents list` reads the **key's** workspace — a different one.
- `.env` and `.env.local` autoload from the working directory, so the key can
  arrive without anyone setting it in this shell.

A key can also be scoped to a single **project inside** a workspace, which is a
third case beyond session-versus-key. Observed on one machine: the session on
`orq-research` read **109** agents and **56** projects, while a project-scoped
key from a repo `.env` read **0** and **1** for the same commands.

(Both session figures need an explicit `--limit` to obtain — `projects list`
alone returns 25 of the 56. See "Lists truncate silently" below; the trap
applies to this diagnostic too.)

**The dangerous direction is too few rows, not too many.** `0` reads as "this
workspace is empty" and gets accepted and reported; an implausibly large count
at least invites a second look. Treat an empty or surprisingly small list as a
credential question until proven otherwise.

Use `orq projects list` as the canary, not `agents list`:

```sh
[ -n "${ORQ_API_KEY:-}" ] && echo "key present — resource reads use ITS workspace, not the session's"
orq auth whoami --json -j active_workspace_key --raw           # session's workspace
orq projects list --json --limit 200 -j 'length(data)' --raw   # scope of whatever authenticated
```

The `--limit` is not decoration: without it this command returns 25 regardless of
how many projects exist, which is the trap two sections down.

`agents list` is a poor canary here: it is the one list command that returns
everything without pagination, so it cannot expose the truncation trap in Phase 5
either. `projects list` separates the cases sharply — a project-scoped key
returns `1`.

**`unset ORQ_API_KEY` does not clear the key.** `.env` and `.env.local` autoload
from the working directory, so the CLI reads it straight back off disk. `unset`,
`env -u ORQ_API_KEY`, and `ORQ_API_KEY=` are each insufficient on their own:

```sh
cd repo-with-env && env -u ORQ_API_KEY orq projects list --json -j 'length(data)' --raw   # 1
cd /tmp          && env -u ORQ_API_KEY orq projects list --json -j 'length(data)' --raw   # 25
```

To actually read as the session, run from a directory with no `.env`, or remove
the key from that file. Nothing warns you which one applied.

Resolve the active key, when a session exists:

```sh
orq auth whoami --json -j active_workspace_key --raw
```

Fall back to the session file only when the CLI is unavailable. Same value, under
a camelCase name:

```sh
jq -r .activeWorkspaceKey ~/.orq/sessions/default.json
```

A snippet for scripts that need the key (for example to build
`https://my.orq.ai/<key>/traces?query=…` deep-links). It has to tolerate the
command succeeding while producing nothing, which is why the guard is not
optional:

```sh
# ORQ_WORKSPACE / ORQ_WORKSPACE_SLUG are an evaluatorq convention, not CLI
# variables. The CLI ignores them; this snippet honours them deliberately so a
# caller can target a workspace they are not switched to.
workspace_key="${ORQ_WORKSPACE:-${ORQ_WORKSPACE_SLUG:-}}"
if [ -z "$workspace_key" ]; then
  workspace_key="$(orq auth whoami --json -j active_workspace_key --raw 2>/dev/null)"
fi
case "$workspace_key" in
  ''|null) echo "no active orq workspace; run 'orq auth login'" >&2; exit 1 ;;
esac
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
orq agents list --json -j 'data[].{id: _id, name: display_name}'
orq agents list --json -j 'data[0]._id' --raw   # bare scalar, no quotes
```

**Identifier names are not consistent across resources.** There are three
conventions, and JMESPath returns `null` for a missing key at exit 0 — so
projecting the wrong one yields a silent column of `null` rather than an error:

| Resource | Identifier | Timestamps |
|---|---|---|
| `agents`, `prompts`, `datasets`, `knowledge-bases` | `_id` | `created` / `updated` |
| `deployments` | `id` (no `_id`) | `created` / `updated` |
| `projects` | `project_id` (neither `id` nor `_id`) | `created_at` / `updated_at` |

Confirm the field on the resource you are actually querying before projecting:

```sh
orq deployments list --json -j 'data[0]' | jq 'keys'
```

### Lists truncate silently

Most list commands cap by default and set `has_more: true`, which nothing in the
output makes obvious — `orq deployments list --json` returns 10 of 49 and looks
complete:

| Resource | Default | Max | Omitting `--limit` |
|---|---|---|---|
| `deployments` | 10 | 50 | truncates |
| `prompts` | 10 | 200 | truncates |
| `datasets` | 10 | 200 | truncates |
| `projects` | 25 | 200 | truncates |
| `knowledge-bases` | 25 | 300 | truncates |
| `agents` | — | 200 | returns all, no `has_more` |

**Always check `has_more` or pass an explicit `--limit`.** Never report a count
from a default page:

```sh
orq deployments list --json -j 'has_more' --raw      # true → the count below is wrong
orq deployments list --json --limit 50 -j 'length(data)' --raw
```

`agents` is the exception that returns everything, which is why it makes a
misleading canary — see Phase 3.

`-j` takes JMESPath and runs after the response is parsed. `--raw` unwraps the
result so a single string comes out unquoted — use it whenever the value feeds a
shell variable.

### `--query` is not the projection flag

The projection flag is the global `-j/--jmespath`. On commands whose request
body has a `query` field (`traces search`, `knowledge-bases search`,
`webhooks query`, …), `--query` is that **body field — a full-text search**,
and a stray `-q` (muscle memory from other CLIs or older orq builds) is an
unknown flag everywhere. The quiet failure is the one that costs turns:

```sh
orq traces search -q 'data[].trace_id' ...
# Error: unknown shorthand flag: 'q' in -q          <- loud, harmless

orq traces search --query 'data[].trace_id' ...
# {"data": [], "has_more": false, ...}              <- SILENT: sent as a body
#                                                      full-text search, 0 rows,
#                                                      exit 0
```

Never "fix" a rejected `-q` by reaching for `--query` — use `-j`. When you do
pipe to `jq` instead, **always set `set -o pipefail`**:

```sh
set -o pipefail
orq traces search --json --from ... --to ... | jq -r '.data[].trace_id'
```

`pipefail` is not optional here. On an API error the CLI writes the message to
**stderr and leaves stdout empty**, then exits 1. `jq` reading empty input emits
nothing and exits **0**, so without `pipefail` the pipeline reports success with
zero rows — indistinguishable from "no traces matched". Verified: a rejected
request produced 0 bytes on stdout, 164 on stderr, pipeline exit 0 without
`pipefail` and 1 with it.

### The trace filter contract

`--filters` is the other thing agents reliably get wrong on `traces search`. Do
not guess the shape. It is `field` / `op` / `values`, where **`values` is always
an array, even for `eq`**:

```sh
set -o pipefail
# -v-7d is BSD/macOS. On GNU/Linux: date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ
orq traces search --json \
  --from "$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ)" --to "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --limit 100 \
  --sort '[{"field":"end_time","order":"desc"}]' \
  --filters '[{"field":"status","op":"eq","values":["error"]}]' \
  | jq -r '.data[].trace_id'
```

The window is computed rather than hard-coded so it cannot age past the 30-day
retention boundary, and the sort is explicit because results are otherwise
unordered. Both are covered below.

The two near-miss spellings both fail, and their errors do not point at the real
problem:

```
"operator" instead of "op" ->
  validation error: filters[0].op: does not match regex pattern
  `^(eq|neq|in|not_in|gt|gte|lt|lte|between|contains|exists|not_exists)$`

"value" instead of "values" ->
  invalid filter: "status" expects exactly one value
```

That second message is actively misleading: it says "exactly one value" when the
fix is to wrap the one value in an array under the plural key.

Valid operators, from the API's own validation regex: `eq`, `neq`, `in`,
`not_in`, `gt`, `gte`, `lt`, `lte`, `between`, `contains`, `exists`,
`not_exists`.

### Results are unordered, and only one sort exists

`traces search` does **not** return rows in time order. A page often looks
descending for the first several rows and then breaks, so `data[0]` is not the
latest trace — it just frequently resembles it. Verified: 40 rows over a 7-day
window were not sorted descending, while the first 8 were.

Exactly one sort is accepted. `started_at` is rejected:

```sh
--sort '[{"field":"end_time","order":"desc"}]'      # the only supported sort
--sort '[{"field":"started_at","order":"desc"}]'
# HTTP 400: invalid sort: only end_time desc is supported
```

To answer "what is the latest trace", pass the sort explicitly. Never take
`data[0]` from an unsorted page.

### Traces expire after 30 days

A window starting more than 30 days back is a hard `400`, not a clamp:

```
HTTP 400: range outside retention: requested range starts before 30 day retention
```

So any script with a hard-coded `--from` works until it silently ages past the
boundary and then fails. Compute the window relative to now, and keep `--from`
inside 30 days.

Discover field names rather than guessing them:

```sh
orq traces list-fields --json     # queryable fields
orq traces list-facets --json     # facetable fields
```

### Request bodies

Commands that take a body accept it several ways, which compose:

```sh
orq traces search --from 2026-07-01T00:00:00Z --to 2026-07-31T00:00:00Z --limit 20 --json
echo '{"from":"...","to":"...","limit":20}' | orq traces search --json
orq traces search --from-file body.json --json
```

`--example` exists as a flag on body commands but is not populated for all of
them. `orq traces search --example` fails with `no generated body example is
available for this command`. Treat it as a convenience that may not be there, not
a documented starting point.

CLI shorthand applies on top of any base body, so you can override one field of a
file without editing it. Run `orq help-input` for the full shorthand grammar.

### Persisting a default format

```sh
orq default-format json
```

Per the CLI's own docs this writes to `~/.orq/config.json` and changes the default
output format for **every** `orq` invocation by that user, including their
interactive shell and other agents. *Documented, not observed — deliberately not
run during authoring, since testing it would have mutated the author's
environment.* Treat it as machine-wide until proven otherwise: pass `--json` per
command, and only persist a default when the user explicitly asks.

## Troubleshooting

`orq doctor` (or `orq doctor --json`) is the starting point, with two blind spots
worth knowing before you trust it:

- Its `auth` block only understands OAuth sessions. With a working
  `ORQ_API_KEY` it still reports `auth.status: "missing"`.
- It does not report `ORQ_SERVER` or the resolved server for generated commands
  at all. Use `orq server current` for that.

It does reliably report the binary and runtime, the active profile and session
path, the auth-side base URLs **with their source** (flag, session, env, default,
derived), and reachability probes.

| Symptom | Likely cause | Fix |
|---|---|---|
| `you are not logged in` on `whoami` / `workspace`, but resource commands work | key-only setup; these need a session | `orq auth login`, or accept the limitation |
| `doctor` says `auth.status: missing` but commands work | `doctor` ignores `ORQ_API_KEY` | confirm with `orq agents list --json -j 'length(data)' --raw` |
| Empty lists where data should be | wrong workspace, or a projection sent as `--query` full-text search | `orq workspace list`; re-run with `-j`, not `--query` |
| `unknown shorthand flag: 'q'` | there is no `-q` — the projection flag is `-j/--jmespath` | re-run with `-j`; do **not** switch to `--query` |
| `unknown command` | subcommand moved or renamed between releases | `orq <group> --help`; check `orq --version` |
| Output is unparseable | TOON default | add `--json` |
| Requests hit the wrong host | `ORQ_SERVER`, or a persisted server default | `orq server current` (not `doctor`) |
| `HTTP 404` on a documented command | endpoint in the spec but not served by this deployment | confirm with `orq request GET <path>`; if that also 404s it is server-side |
| Works locally, fails in CI | OAuth session is not portable | use `ORQ_API_KEY`, and avoid `whoami` / `workspace` in CI |

`.env` and `.env.local` in the working directory are loaded automatically, so a
stray `ORQ_SERVER`, `ORQ_API_KEY`, or `ORQ_OUTPUT_FORMAT` in a project file can
silently change behaviour. Note the env var the CLI reads for a key is
`ORQ_API_KEY` specifically; a project using a different name (`ORQ_KEY`, say)
will not authenticate the CLI even though the file loaded.

---

## orq.ai Documentation

**CLI:** [orq-cli repository](https://github.com/orq-ai/orq-cli) ·
[Releases](https://github.com/orq-ai/orq-cli/releases) ·
[`@orq-ai/cli` on npm](https://www.npmjs.com/package/@orq-ai/cli)

**API:** [API reference](https://docs.orq.ai/reference) ·
[Agents](https://docs.orq.ai/reference/agents)

**Shorthand syntax:** [bartolo shorthand](https://github.com/orq-ai/bartolo/tree/main/shorthand#readme)

### Key Concepts

- A **profile** is a named credential set with its own session file and API key.
  Everything is profile-scoped: auth, active workspace, and server host.
- A **workspace key** is the slug in app URLs; a workspace **id** is a UUID. The
  CLI accepts the key for `workspace use` and reports both in `workspace list`.
  Do not put the UUID in an app URL.
- **TOON** is the CLI's default human-facing output format. It is not JSON and
  should never be parsed.
- Generated commands mirror the OpenAPI spec one-to-one, so a command group maps
  to an API tag and a subcommand maps to an operation.
