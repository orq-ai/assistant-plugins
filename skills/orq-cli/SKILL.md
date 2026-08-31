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

**Verified against `orq` 5.1.0 (built against orq API 4.14.3) on 2026-08-31.**
The CLI's version is its own since 5.0.0 and no longer tracks the API line, so a
`5.x` number tells you nothing about the API — `orq version --json` reports both.

## Constraints

- **NEVER** run any `orq` command with `--verbose` in output anyone else will
  see. It prints the whole profile config to **stderr**, including **every
  stored API key in plaintext**. Since 5.0.0 `auth list-profiles` masks keys in
  its own output (`sk-o********08f7`) — `--verbose` bypasses that masking
  entirely, so the safe-looking command is still unsafe with the flag. Verified
  on 5.1.0: `orq --verbose auth list-profiles --json` printed 19 profiles' full
  keys on stderr while stdout showed only masked ones. If you already ran it,
  tell the user to rotate those keys.
- **NEVER** run a generated `delete` command, or `orq request DELETE`, without
  `--force` in any non-interactive context. Since 5.0.0 they prompt for
  confirmation and **refuse to run when there is no terminal** — which is every
  agent and CI invocation. 40 commands are affected. This one fails *loudly*
  (exit 1, message on stderr, no request sent), so it costs a retry rather than
  data; `--force` skips the prompt, so think before adding it — it is the
  confirmation you are removing.
- **NEVER** trust the exit code for auth. `orq auth whoami`, `orq workspace
  list`, and `orq doctor` all exit **0** when unauthenticated. Read the payload:
  `authenticated` from `whoami`, `auth.status` from `doctor`. Verified on 5.1.0:
  `orq --profile <key-only> auth whoami --json` prints `Error: you are not
  logged in` and exits **0**.
- **NEVER** trust the exit code for a typo'd subcommand either. An unknown
  subcommand prints the help text to **stdout** and exits **0** — a script
  wrapping it sees success and empty data. Verified: `orq traces bogus` →
  exit 0, 3160 bytes on stdout, 0 on stderr. Errors that reach the API do the
  opposite (empty stdout, message on stderr, exit 1), so the two failures need
  different guards.
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
- **NEVER** report a count or a "complete" list from a default page. Every list
  command below caps by default and sets `has_more: true` with nothing in the
  output to signal it. Check `has_more` or pass `--limit` (see "Lists truncate
  silently"). Do **not** carry over the old advice that `agents list` returns
  everything — as of 5.1.0 it paginates like the rest, and omitting `--limit`
  is actively worse than truncating (see below).
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

- `orq-analyze-traces` — once you have pulled traces, analyze them
- `orq-improve-agent` — act on what that analysis found
- `orq-shared` — the layered trace-read recipe (`aggregate` → `search` →
  `get-span`) built on top of this skill's query contract. It defers to this
  file for the CLI's general rules, so **correct a query rule here first**
- `orq-invoke-deployment` — call deployments and agents from application code
- `orq-manage-skills` — richer workflow for the platform Skills entity that
  `orq skills` exposes (not the `skills` capability of `orq connect`)
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
  `orq-analyze-traces`

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
| Alerts, notifiers, budgets, annotation queues, smart routers, MCP gateways, workspace security/settings, reporting | **CLI** | likewise — and several have no UI equivalent either |
| Wiring a coding agent to orq, or launching one through the AI Router | **CLI** | `connect` / `setup` / `launch` write local config; there is no remote equivalent |

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
orq version --json     # {"api_version":"4.14.3","cli":"5.1.0","install_method":"npm"}
orq --version          # "orq version 5.1.0" then a second line with the API version
```

**Read `orq version`, not `orq --version`, in a script.** The CLI's semver was
decoupled from the orq API's at 5.0.0, so the two numbers move independently and
only `orq version --json` reports both plus how the binary was installed
(`npm`, `installer`, `unknown`). `orq --version` keeps `orq version <semver>` as
its *first* line for compatibility but now prints a second line under it, which
breaks anything reading the whole output.

If it is missing:

```sh
npm install -g @orq-ai/cli                                        # npm
curl -fsSL https://raw.githubusercontent.com/orq-ai/orq-cli/main/install.sh | sh   # installs to ~/.orq/bin/orq
```

To upgrade an existing install, use `orq update` — it resolves the latest
release, reuses the install method the binary arrived through, verifies the
published `.sha256`, and swaps atomically. `orq update --check --json` reports
and changes nothing; verified output:

```json
{"current": "5.1.0", "install_method": "npm", "latest": "5.1.3", "update_available": true}
```

**A machine still on `4.x` will not upgrade itself.** `npm update -g` treats a
global install as pinned to a caret range of the installed version, so a `4.x`
box reports itself up to date forever and never crosses into `5.x`. That one
hop needs an explicit `npm install -g @orq-ai/cli@latest`. `orq update` and
`install.sh` are unaffected — both install an exact resolved version.

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
| `doctor`'s `auth` block | reports no session | reports `authenticated` |

Verified live on 5.1.0: `orq --profile <api-key-only> auth whoami --json` prints
`Error: you are not logged in` at exit 0, while resource commands on that same
profile return data. With a session present, `orq doctor --json -j 'auth.status'
--raw` prints **`authenticated`** and `auth.source` names where the credential
came from (`session-file`).

**`auth.status` values changed.** Earlier releases reported `ok` / `missing` /
`invalid`; do not match on those strings. Read the whole `auth` block —
`status`, `source`, `active_workspace_key`, `user_email`, `workspace_count` —
rather than comparing `status` against a literal you remember.

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
orq auth add-profile apikey ci --api-key-file key.txt   # or `-` to read stdin
orq auth list-profiles                      # keys are masked since 5.0.0
orq --profile ci agents list
```

Prefer `--api-key-file` (or `-` for stdin) over passing the key as a positional
argument: an argument is visible to every process on the machine through `ps`.

**An explicit `--profile` outranks an exported `ORQ_API_KEY`, and says so.**
This is the escape hatch for the stray-key problem below — you can name a
credential rather than fighting the environment. Verified: with
`ORQ_API_KEY=sk-bogus` set, `orq --profile babcock auth whoami` printed
`warning: using the API key from profile "babcock"; ORQ_API_KEY set but an
explicit --profile takes precedence`. `ORQ_PROFILE` does **not** win that tie —
env against env has no statement of intent to break it. *[unverified: doc-only,
from the upstream CHANGELOG; constructing a clean two-credential case would have
meant mutating the author's saved profiles.]*

A caveat that costs a turn when it bites: **a stray `ORQ_API_KEY` masks an
unknown-profile error.** Verified — `orq --profile research projects list`
alone fails with `unknown profile "research": no session … and no credentials
entry`, but the same command with a key exported skips that check and returns
`HTTP 401: Authorization token is invalid` instead. The 401 sends you hunting a
bad key when the real problem is a profile that does not exist.

Credential files are `0600` from 5.0.0 onward, but **earlier versions could
leave `~/.orq/credentials.json` world-readable permanently** — `orq auth
add-profile` never chmodded at all. Nothing repairs an existing file
automatically:

```sh
orq doctor          # flags any credential path with loose permissions
orq doctor --fix    # chmods them (0600 files, 0700 dirs); exits 1 if a repair fails
```

If a file was `0644`, treat the key in it as exposed to every account on that
machine: rotate it, do not just chmod it. Unix only; the check has no Windows
equivalent.

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
orq workspace use <key>                          # persists in the session
orq --workspace <key> projects list --json       # this invocation only
ORQ_WORKSPACE=<key> orq projects list --json     # same, via the environment
```

**`--workspace` is a global flag as of 5.x, and it is usually what you want.**
It overrides the session's active workspace for one invocation without
persisting anything, so a script can read another workspace without disturbing
the user's shell. Verified: with a session on `orquesta-demos`, `orq projects
list --limit 200 -j 'length(data)'` returned **76**, `orq --workspace
orq-research projects list --limit 200 …` returned **60**, and `orq auth whoami
-j active_workspace_key` still reported `orquesta-demos` afterwards. It works
before the group, after the subcommand, and as `ORQ_WORKSPACE` in the
environment.

This reverses earlier guidance. `ORQ_WORKSPACE` used to be an evaluatorq-only
convention that the CLI ignored; it is now a documented CLI variable
(`[env: ORQ_WORKSPACE]` in `orq --help`). Reserve `orq workspace use` for
genuinely changing the user's default, and use `--workspace` for everything
scoped to one command or one script.

**An API key silently wins over `--workspace` — but now warns.** Verified:

```
warning: --workspace has no effect because an explicit API key (ORQ_API_KEY or a
credentials profile) is configured and takes precedence
```

The command then runs against the key's workspace, and usually fails loudly
(`HTTP 401: API key is not valid for this workspace`) rather than returning the
wrong data. Watch stderr for that warning before concluding `--workspace` is
broken.

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
`orq-research` read **60** projects, while a project-scoped key from a repo
`.env` read **1** for the same command.

(The session figure needs an explicit `--limit` to obtain — `projects list`
alone returns 25 of the 60. See "Lists truncate silently" below; the trap
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

`projects list` separates the cases sharply — a project-scoped key returns `1`.
`agents list` is a worse canary: it truncates at 10 by default like everything
else, so a small number there is ambiguous between a narrow credential and a
default page.

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
# ORQ_WORKSPACE is read by the CLI itself as of 5.x, so honouring it here agrees
# with what the commands will do. ORQ_WORKSPACE_SLUG is an evaluatorq-only
# convention the CLI ignores; it is honoured second so a caller using that name
# can still target a workspace they are not switched to.
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

Every generated group is one API tag. `orq request <method> <path>` is the
escape hatch for an endpoint with no generated command; it reuses the configured
auth and server.

`orq --help` sorts the groups into six sections. Knowing the sections is the
fastest way to guess where a capability lives before running `--help` on it:

| Section | Groups |
|---|---|
| Get started | `auth`, `connect`, `disconnect`, `doctor`, `launch`, `setup`, `update` |
| AI Gateway | `budgets`, `chat`, `chunking`, `completions`, `embeddings`, `images`, `mcp-gateways`, `mcp-servers`, `model-catalog`, `models`, `moderations`, `ocr`, `pii`, `rerank`, `responses`, `smart-routers`, `speech`, `transcriptions`, `translations` |
| Observability | `alerts`, `feedback`, `identities`, `logs`, `notifiers`, `traces` |
| Managed agents | `agents`, `agents-responses`, `deployments`, `knowledge-bases`, `memory-stores`, `prompts`, `schedules`, `skills`, `tools` |
| Optimization | `annotation-queues`, `datasets`, `evals` |
| Administration | `api-keys`, `files`, `management-keys`, `projects`, `reporting`, `webhooks`, `workspace`, `workspace-security`, `workspace-settings` |

There is still no `experiments` group — use the MCP tools or the evaluatorq SDK
for those.

The **Get started** group is not generated from the API and behaves differently
from everything else: those commands write to local config files rather than
calling the platform. See "Wiring coding agents" below before running any of
them.

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
| `agents` | 10 | 200 | truncates — **and see below** |

**Always check `has_more` or pass an explicit `--limit`.** Never report a count
from a default page:

```sh
orq deployments list --json -j 'has_more' --raw      # true → the count below is wrong
orq deployments list --json --limit 50 -j 'length(data)' --raw
```

**On `agents list`, always pass `--limit`.** Earlier releases returned every
agent in one unpaginated response, and the old version of this skill said so.
On 5.1.0 a bare `orq agents list` blocked for **4m16s** and then failed with
`HTTP 503: upstream connect error or disconnect/reset before headers`, while
`--limit 10` answered in 0.17s (`has_more: true`) and `--limit 200` in 0.16s.

**Do not read that 503 as "the unpaginated read timed out" — it is broader than
pagination.** On the same workspace, `orq agents retrieve <valid-key>` — one
entity, no pagination anywhere — also hung 4m14s and returned the same 503,
while `orq agents retrieve <nonexistent-key>` came back `HTTP 404` in 0.19s.
Requests that would carry a full agent config hang; requests that return nothing
are fast. That points at a server-side condition on the agents detail path, not
a CLI rule, and it may well not reproduce on your workspace or next week.

Practical consequence: `--limit` is verified-good advice for `agents list`, but
if a **single** `agents retrieve` hangs for minutes and 503s, adding flags will
not help. Treat a multi-minute hang followed by `upstream connect error` as an
upstream fault to report, not as something to tune.

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

The registry **grows and renames between releases** — it went 56 → 57 fields in
a single afternoon when `attr.*` became `attributes.*`, and read 66 on 5.1.0. A
name that no longer resolves returns **zero rows without erroring**, which looks
exactly like "no matching traces". Resolve names at call time; never hard-code
one from this document.

### OQL: a second query language, with its own rules

`orq traces query-oql` and `orq logs query` take a pipeline expression instead
of the filter/sort structure above. They are not drop-in alternatives — three
things differ, and each fails in its own way.

**The source is fixed per command.** `traces query-oql` accepts only `fetch
traces`; `fetch spans` and `fetch logs` both give `HTTP 400: invalid oql: query
must start with fetch traces`. `logs query` takes `fetch logs`, with the grammar
its help states: `fetch logs | filter <expr> | sort timestamp desc | limit N`,
where `timestamp desc` is the only sort.

**Equality is not `=`.** This is the one that costs turns, because every
spelling fails with the same unhelpful message:

```sh
--oql 'fetch traces | filter status = "error" | limit 2'    # invalid oql: invalid filter
--oql "fetch traces | filter status = 'error' | limit 2"    # invalid oql: invalid filter
--oql 'fetch traces | filter status eq "error" | limit 2'   # invalid oql: invalid filter
--oql 'fetch traces | filter status:"error" | limit 2'      # invalid oql: invalid filter

--oql 'fetch traces | filter status in ("error") | limit 2' # works
```

Use list membership — `in (…)` / `not_in (…)` — for equality. Combining it with
a comparison is rejected too: `filter status in ("error") and total_cost > 0`
gives `invalid oql: malformed list`. When a query needs mixed operators, drop
back to `traces search --filters`, which supports the full operator set.

**The response is shaped differently.** OQL results are nested under `search`,
not at the top level:

```json
{"object": "query",
 "search": {"data": [], "has_more": false, "next_page_token": "",
            "meta": {"from": "…", "to": "…", "request_id": "…", "row_count": 0},
            "object": "list", "total_count": "0"}}
```

So `-j 'length(data)'` is wrong here; project `search.data` or
`search.meta.row_count`. And projecting a key that does not exist **crashes**
rather than yielding `null` — `orq logs query … -j '[length(data),has_more]'`
gives `FATAL logs_commands.go:637 formatting failed Invalid type for: <nil>`.
Confirm the envelope before projecting:

```sh
orq traces query-oql --json --from "$F" --to "$T" --oql 'fetch traces | limit 1' -j 'keys(@)'
# ["object", "search"]
```

Both commands paginate with `--page-token` against `next_page_token`, not with
the `has_more` + offset pattern used elsewhere.

### Request bodies

Commands that take a body accept it several ways, which compose:

```sh
orq traces search --from 2026-07-01T00:00:00Z --to 2026-07-31T00:00:00Z --limit 20 --json
echo '{"from":"...","to":"...","limit":20}' | orq traces search --json
orq traces search --from-file body.json --json
```

`--example` prints a generated body and exits without sending a request. It now
works on `traces search` — verified, `{"from":"2024-01-01T00:00:00Z","to":
"2024-01-01T00:00:00Z"}` — where it previously failed with `no generated body
example is available for this command`. It is still not populated for every body
command, and what it prints is the **required scalars only**: the example above
omits `filters`, `sort` and `limit` entirely. Use it to confirm field names and
the required set, not as a working query.

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

### Deleting requires `--force` off a terminal

Since 5.0.0 every generated `delete` command prompts for confirmation and
**refuses to run when stdin is not a terminal** — which is every agent, script
and CI invocation. 40 commands are affected, plus `orq request DELETE`:

```sh
orq agents delete <id> --force        # required non-interactively
orq request DELETE /v2/agents/<id> --force
```

**What happens without it**, verified on 5.1.0 — exit **1**, nothing on stdout,
one line on stderr, and **no request is sent**:

```
Error: refusing to run "orq agents delete <id>" without --force in a non-interactive shell
```

`orq request DELETE` gives the identical message with its own command line in
it. That the request is never sent is not an inference: the same nonexistent id
*with* `--force` reached the API and came back `HTTP 404: Agent not found`,
while without it there was no HTTP response at all.

So this is a **loud** failure, unlike most of the traps in this skill — it costs
a retry, never data. If a script that used to work now exits 1 with that
message, the fix is to add `--force` after confirming the id, not to debug auth.

Only DELETE is gated. Reads and writes on the same resource are unaffected:
`orq agents retrieve <id>` and `orq agents update …` need no flag.

Two related 5.0.0 changes make delete safer rather than just noisier: path
parameters are URL-escaped (`orq datasets retrieve '../../etc/passwd'` now 404s
instead of traversing), and an **empty id is rejected before any request** —
`orq agents delete ""` used to build a collection URL and hit `/v2/agents`, and
now fails with `path parameter agent_key cannot be empty` (verified).

Confirm the id resolves to what you think before adding `--force`. The flag
removes the only prompt standing between a wrong id and a deleted entity.

## Wiring coding agents

`connect`, `disconnect`, `setup`, `launch` and `update` are hand-written rather
than generated: they **write to local config files** instead of calling the
platform, wiring a coding agent (`claude`, `codex`, `kilo`, `kimi`, `opencode`,
`pi`) to the orq AI Gateway, MCP server, or skills directory.

**Always start with `orq connect --status` or `--dry-run`** — these edit files
the user's other tools depend on. `orq launch <agent>` is the non-persistent
option, and it propagates the launched agent's exit status verbatim, so it is
the one command whose exit code is not the contract below.

See [resources/coding-agents.md](resources/coding-agents.md) for the capability
matrix, the `--local` scoping rule, and the `orq skills` / `skills`-capability
name collision.

**Exit codes** everywhere else: `0` success, `1` any failure, `130` SIGINT,
`143` SIGTERM. Remember that a typo'd subcommand is a `0` (see Constraints).

## Troubleshooting

`orq doctor` (or `orq doctor --json`) is the starting point, with two blind spots
worth knowing before you trust it:

- Its `auth` block only understands OAuth sessions. With a working
  `ORQ_API_KEY` and no session it does not report a usable login, even though
  resource commands work.
- It reports where the host came from (`flag`, `env`, `config`, `session`,
  `default`), but `orq server current` is still the direct answer for the
  resolved server.

It does reliably report the binary and its `api_version`, the active profile and
session path, the base URLs with their source, credential-file permissions (with
`--fix` to repair them), coding-agent wiring status, and reachability probes.
`orq doctor --report` prints a pre-filled GitHub issue URL for filing a bug.

| Symptom | Likely cause | Fix |
|---|---|---|
| `you are not logged in` on `whoami` / `workspace`, but resource commands work | key-only setup; these need a session | `orq auth login`, or accept the limitation |
| `doctor` reports no login but commands work | `doctor`'s auth block ignores `ORQ_API_KEY` | confirm with `orq projects list --json --limit 200 -j 'length(data)' --raw` |
| `refusing to run "…" without --force in a non-interactive shell` | 5.0.0 gates DELETE on confirmation; no TTY means refuse. Nothing was sent | add `--force` after confirming the id resolves |
| A typo'd subcommand "succeeds" with no data | unknown subcommands print help to stdout at exit **0** | compare the output against `--help`; do not trust `$?` alone |
| An `agents` read hangs for minutes, then `HTTP 503 upstream connect error` | server-side on the agents detail path — reproduced on `retrieve` of a single valid key, so not a pagination fault | pass `--limit` on `list` regardless; for `retrieve`, report it rather than tuning flags |
| `--workspace` appears ignored | an API key outranks it | read stderr for the `--workspace has no effect` warning; unset the key or use `--profile` |
| `unknown profile "x"` becomes an unexplained `HTTP 401` | a stray `ORQ_API_KEY` masks the unknown-profile check | re-run with the key unset to see the real error |
| `invalid oql: invalid filter` on an obviously valid filter | OQL has no `=`; equality is `in (…)` | rewrite as `filter f in ("v")` |
| `-j` gives `formatting failed Invalid type for: <nil>` | projecting a key the envelope lacks (common on OQL) | check `-j 'keys(@)'`; OQL nests results under `search` |
| `npm update -g` says the CLI is current, but it is on `4.x` | a global install is pinned to a caret range | `npm install -g @orq-ai/cli@latest`, or use `orq update` |
| Empty lists where data should be | wrong workspace, or a projection sent as `--query` full-text search | `orq workspace list`; re-run with `-j`, not `--query` |
| `unknown shorthand flag: 'q'` | there is no `-q` — the projection flag is `-j/--jmespath` | re-run with `-j`; do **not** switch to `--query` |
| `unknown command` | subcommand moved or renamed between releases | `orq <group> --help`; check `orq --version` |
| Output is unparseable | TOON default | add `--json` |
| Requests hit the wrong host | `ORQ_SERVER`, a profile-bound host, or a persisted default | `orq server current`; set hosts with `--server` only |
| `warning: --api-base-url is deprecated` | the pre-5.0.0 flag for the auth host | replace it with `--server` — same value, one name |
| `HTTP 404` on a documented command | endpoint in the spec but not served by this deployment | confirm with `orq request GET <path>`; if that also 404s it is server-side |
| Works locally, fails in CI | OAuth session is not portable | use `ORQ_API_KEY`, and avoid `whoami` / `workspace` in CI |

`.env` and `.env.local` in the working directory are loaded automatically, so a
stray `ORQ_SERVER`, `ORQ_API_KEY`, `ORQ_WORKSPACE`, or `ORQ_OUTPUT_FORMAT` in a
project file can silently change behaviour. Note the env var the CLI reads for a
key is `ORQ_API_KEY` specifically; a project using a different name (`ORQ_KEY`,
say) will not authenticate the CLI even though the file loaded.

### Setting the host: `--server`, and nothing else

**`--server <url>` / `ORQ_SERVER` is the only way to point the CLI at a host.**
It works on every command, built-in and generated, including
`orq auth login --server https://orq.acme.internal`. Use it and stop there.

`--api-base-url` and `ORQ_API_BASE_URL` are the **deprecated** old spellings.
Do not write them into anything new, and replace them when you find them — the
value is identical, so it is a rename, not a migration:

```sh
orq doctor --api-base-url https://api.orq.ai      # old — deprecated
orq doctor --server       https://api.orq.ai      # new — do this
```

Verified on 5.1.0: the old flag still runs and prints `warning: --api-base-url
is deprecated and will be removed in a future release; use --server instead`.
Upstream states it will be removed in a following minor, so treat it as already
gone. It was also never accepted on generated commands — `orq projects list
--api-base-url …` gives `Error: unknown flag` — which is exactly the split
`--server` exists to end.

Why it matters beyond tidiness: until 5.0.0 these were **two different hosts**,
not two names for one. Six built-in commands took `--api-base-url` and rejected
`--server`, every generated command did the reverse, and a single run could talk
to two hosts at once. Anything you find that sets both is working around that
old split and should collapse to one `--server`.

The default host also moved from `https://api.orq.ai` to `https://my.orq.ai` —
both answer the same routes, but a self-hosted deployment that only allow-listed
one name will notice.

(The `config.api_base_url` field in `orq doctor --json` output is unrelated —
that is a response field name, not the flag, and it keeps its spelling.)

A profile now carries its own host as well as its own credentials, and both beat
the wider setting: `orq auth login --server <url> --profile acme` binds that
host, so `orq --profile acme …` routes there with no flag.

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
  should never be parsed — upstream states explicitly that TOON is
  presentation-only and its rendering may change between releases without
  notice. `--json` on stdout is the machine contract.
- Generated commands mirror the OpenAPI spec one-to-one, so a command group maps
  to an API tag and a subcommand maps to an operation. The **Get started** group
  (`connect`, `disconnect`, `launch`, `setup`, `update`) is hand-written and
  writes local config instead.
- The **CLI version is not the API version.** They were decoupled at 5.0.0;
  `orq version --json` reports `cli`, `api_version` and `install_method`
  separately. A `5.x` CLI built against API `4.14.3` is normal.
- The command surface is tracked upstream in `surface.json` and CI fails any
  uncommitted change to it, so a command or flag cannot vanish silently between
  releases — but it *can* be removed deliberately after one release's notice.
  `--help` remains the source of truth.
