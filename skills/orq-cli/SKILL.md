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

You are an **orq.ai platform operator working from a shell**. Your job is to run the `orq` CLI correctly: confirm it is installed and authenticated, make sure the right workspace is active, then run the command the task actually needs with machine-readable output.

The CLI is a Go binary generated from the orq.ai OpenAPI spec, so nearly every API endpoint has a matching command. That also means the command surface changes between releases — treat `--help` as the source of truth, never your memory.

**Every section below opens with a `Probed against` line naming the `orq` version it was last checked on, so the oldest of those lines is the ceiling on how far to trust the file: `grep -n 'Probed against' skills/orq-cli/SKILL.md`. A section with no such line makes no version claim. Treat `--help` as the source of truth either way.** The CLI's version is its own since 5.0.0 and no longer tracks the API line, so the number tells you nothing about the API — `orq version -o json` reports both.

## Constraints

- **NEVER** run any `orq` command with `--verbose` in output anyone else will see. It writes the whole profile config to **stderr** before any request, so every command dumps it. **On 8.6.9 the keys in that dump are masked** (`"api_key":"**HIDDEN**"`) and the HTTP `Authorization` header is `[REDACTED]`, so it no longer leaks credentials — but it still lists every profile name and host, which is not for a shared transcript. **On 5.1.0 it printed every stored key in plaintext**, and that version was verified to do so; the fix landed somewhere between 5.1.0 and 8.6.9 and was not bisected. On any CLI you have not checked, assume the 5.1.0 behaviour, and if a key did print, tell the user to rotate it.
- **NEVER** run a generated `delete` command, or `orq request DELETE`, without `--force` in any non-interactive context. Since 5.0.0 they prompt for confirmation and **refuse to run when there is no terminal** — which is every agent and CI invocation. Re-verified on 8.6.9. This one fails *loudly* (exit 1, message on stderr, no request sent), so it costs a retry rather than data; `--force` skips the prompt, so think before adding it — it is the confirmation you are removing.
- **NEVER** trust `orq doctor`'s exit code, or its `auth.status`, as proof a credential works. `doctor` exits **0** whatever it finds, and its `auth.status` says `authenticated` from the mere presence of a session file: on 8.6.9 a session whose refresh token was dead reported `authenticated` (`source: session-file`) while `orq auth whoami` failed with `Error: Invalid refresh token!`. `whoami` and `workspace list` now fail loudly — exit **1** with `Error: you are not logged in` (5.1.0 exited 0 with the same message, so an old script that read the payload still works, and one that read the exit code now gets a real signal). The only real proof a credential works is a resource command returning data.
- **NEVER** trust the exit code for a typo'd subcommand either. An unknown subcommand prints the help text to **stdout** and exits **0** — a script wrapping it sees success and empty data. Re-verified on 8.6.9: `orq traces bogus` → exit 0, help on stdout, 0 bytes on stderr. Errors that reach the API do the opposite (empty stdout, message on stderr, exit 1), so the two failures need different guards.
- **NEVER** guess a flag or subcommand. Run `orq <group> --help` first; the help text lists every flag with its exact name and type.
- **NEVER** parse default output. `--help` names `table` as the default format, but piped (not a terminal) it renders TOON, which is meant for humans; a real terminal was not probed. Pass **`-o json`** on anything a script or you will parse. There is no separate JSON flag (`--json` is `unknown flag`). `ORQ_OUTPUT_FORMAT=json` did produce JSON on 8.6.9, but an explicit `-o json` does not depend on the environment.
- **NEVER** run `orq auth login` unattended without an API key. Its default is an interactive OAuth device flow that needs a browser; `--api-key` signs in with a key instead. If nobody can complete the browser flow, stop and say so — `ORQ_API_KEY` is **not** a substitute for the commands that need a session (see the auth matrix below).
- **NEVER** assume which workspace is active. `ORQ_API_KEY` **overrides an active OAuth session** (5.1.0; not re-probed on 8.6.9, which needed a live session), so a stray exported key silently redirects every read to that key's workspace while `whoami` keeps reporting the one you logged into. Confirm with a count before trusting data (see "Which workspace am I really reading?").
- **NEVER** report a count or a "complete" list from a default page. Every list command below except `agents` caps by default and sets `has_more: true` with nothing in the output to signal it. Check `has_more` or pass `--limit` (see "Lists truncate silently"). `agents list` is the exception on 8.6.9: it returns every agent when `--limit` is omitted.
- **NEVER** treat an empty or small result as an answer. `0` rows is the characteristic symptom of a project-scoped key or a wrong workspace, and it reads exactly like a legitimately empty workspace. Rule out credentials first.
- **NEVER** take `data[0]` from `traces search` as the latest trace unless you passed the sort. Only `[{"field":"end_time","order":"desc"}]` sorts and nothing else is accepted, so ordering is a contract only when you ask for it.
- **NEVER** echo the contents of `~/.orq/sessions/*.json` (one per host, for example `my.orq.ai.json`), `~/.orq/credentials.json`, or `~/.orq/config.json`. They hold refresh tokens and API keys.
- **NEVER** print `$ORQ_API_KEY` to check whether it is set. Test presence without expanding the value, and beware that an unquoted expansion inside a larger command still lands in the transcript:

  ```sh
  [ -n "${ORQ_API_KEY:-}" ] && echo "ORQ_API_KEY: set" || echo "ORQ_API_KEY: unset"
  ```
- **ALWAYS** project with the global `-j/--jmespath`, never `--query`. On search commands `--query` is the body's full-text search field — it accepts a JMESPath expression and silently returns zero rows (see "`--query` is not the projection flag").
- **ALWAYS** prefer `-j` (JMESPath) over piping to `jq` **when it can express the projection**. It runs inside the CLI, so it needs no `jq` installed. Reach for `jq` when you need something JMESPath lacks (`@uri` encoding, text munging), or when reading a file rather than a command's output.

**Why these constraints:** the generated command surface is large and drifts between versions, so guessed flags fail in ways that look like auth problems. Auth and output failures here are quiet: wrong-workspace reads succeed, logged-out commands exit 0, and one shadowed flag returns an empty list instead of an error.

## Companion Skills

- `orq-analyze-traces` — once you have pulled traces, analyze them
- `orq-improve-agent` — act on what that analysis found
- `orq-shared` — the layered trace-read recipe (`aggregate` → `search` → `get-span`) built on top of this skill's query contract. It defers to this file for the CLI's general rules, so **correct a query rule here first**
- `orq-invoke-deployment` — call deployments and agents from application code
- `orq-manage-skills` — richer workflow for the platform Skills entity that `orq skills` exposes (not the `skills` capability of `orq connect`)
- `orq-setup-observability` — get traces flowing before you query them

## When to use

- "run this with the orq CLI", "use `orq` to …", "from the terminal"
- A shell script, Makefile, or CI job needs orq.ai data
- "which workspace am I in", "switch workspace", "am I logged in"
- "why is `orq` failing", "the CLI says not authenticated"
- Something needs the active workspace **key** (for example to build a trace deep-link URL)
- Quick one-off reads where writing SDK code would be overkill

## When NOT to use

- **Writing application code that calls orq.ai?** → Use `orq-invoke-deployment`
- **A guided evaluation or experiment workflow?** → Use `orq-run-experiment`
- **Analyzing trace content rather than fetching it?** → Use `orq-analyze-traces`

## MCP tools or the CLI?

This suite ships an MCP server, so you will often have both. They are not interchangeable. Pick by job, not by habit:

| Job | Use | Why |
|---|---|---|
| One-off lookup mid-conversation | **MCP** | typed arguments, no install, no shell |
| Anything inside a script, Makefile, or CI job | **CLI** | MCP tools do not exist outside the agent session |
| Piping results into other shell tools or a file | **CLI** | `-o json` plus `-j` composes with the shell |
| Bulk export or pagination loops | **CLI** | cursors are easier to drive in a loop |
| Endpoint with no MCP tool — or no generated command either | **CLI** | the command surface is generated from the whole spec; `orq request <method> <path>` covers the rest |
| Checking why auth or routing is broken | **CLI** | `orq doctor` has no MCP equivalent |
| Acting as a specific profile or a self-hosted host | **CLI** | `--profile` and `--server` are CLI-only |
| Running experiments (create/run/export) | **MCP or evaluatorq SDK** | the CLI has no `experiments` group |
| Finding an entity by name, browsing docs | **MCP** | `search_entities` / `search_docs` have no CLI equivalent |
| Schedules, identities, projects, API keys, webhooks, KBs, memory stores, files | **CLI** | no MCP tools exist for these areas |
| Alerts, notifiers, budgets, annotation queues, smart routers, MCP gateways, workspace security/settings, reporting | **CLI** | likewise — and several have no UI equivalent either |
| Wiring a coding agent to orq, or launching one through the AI Router | **CLI** | `connect` / `setup` / `launch` write local config; there is no remote equivalent |

The deciding question is usually **does this need to run again without an agent present?** If yes, it has to be the CLI.

## Workflow Checklist

```
orq CLI Progress:
- [ ] Phase 1: Verify — binary present, version known
- [ ] Phase 2: Authenticate — session or API key valid
- [ ] Phase 3: Scope — correct workspace (and project) active
- [ ] Phase 4: Discover — --help on the target command group
- [ ] Phase 5: Run — execute with -o json and a JMESPath projection
```

## Done When

- Credentials confirmed by output, not exit code: `orq auth whoami -o json` shows the expected user and `active_workspace_key` (session), or a resource command returns real data (API key)
- The command ran and returned data, not a usage dump
- Output is JSON (or a deliberately raw scalar), not TOON
- Any script produced is safe to re-run: no interactive login, no hardcoded key

---

## Phase 1 — Install and verify

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.**

Check first — it is usually already there:

```sh
orq version -o json     # {"api_version":"4.14.20","cli":"8.6.9","install_method":"npm"}
orq --version          # "orq version 8.6.9" then a second line: "built against orq API 4.14.20"
```

**Check the major before you trust anything below.** A globally installed `orq` is often not the newest: while this section was probed, the global binary was 8.4.1 and npm's latest was 9.0.0. To probe a specific release without touching the global install, put it in a scratch directory and call it by path: `npm i @orq-ai/cli@8.6.9` there, then `./node_modules/.bin/orq`.

**Read `orq version`, not `orq --version`, in a script.** The CLI's semver was decoupled from the orq API's at 5.0.0, so the two numbers move independently and only `orq version -o json` reports both plus how the binary was installed (`npm`, `installer`, `unknown`). `orq --version` keeps `orq version <semver>` as its *first* line for compatibility but now prints a second line under it, which breaks anything reading the whole output.

**The `--version` flag takes no format.** Re-verified on 8.6.9: `orq --version -o json` fails with `Error: unknown command "json" for "orq"` at exit `1` with nothing on stdout, because `-o`'s value lands in subcommand position. Only the `version` **subcommand** serializes — `orq version -o json`. A script that pipes the flag's output into `jq` gets a parse error, not JSON.

### Installing

**npm is the recommended route, and the only one for Windows** (needs Node ≥ 14):

```sh
npm install -g @orq-ai/cli
```

Or the raw-binary installer, which writes `~/.orq/bin/orq` and checksum-verifies it. **Its two defaults matter off a terminal:** left alone it offers to edit your shell profile and then runs `orq setup`, an interactive OAuth login that also rewires this machine's coding agents. Unattended, decline both:

```sh
curl -fsSL https://cli.orq.ai/install.sh | sh                              # interactive
curl -fsSL https://cli.orq.ai/install.sh | sh -s -- --no-modify-path --no-setup   # unattended
```

Then confirm, and read `install_method` to learn which upgrade path applies:

```sh
orq version -o json          # or ~/.orq/bin/orq version -o json if PATH is not set up
```

See [resources/install.md](resources/install.md) for the installer's full flag and env-var set, the pinning and `rc`-channel options, checksum behaviour, pre-built binaries and building from source, and the `PATH` markers to undo.

### Upgrading

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** `orq update --check` reports and changes nothing; with `-o json` it returns `{current, install_method, latest, update_available}` (`install_method` was `npm`). Plain `orq update` replaces the binary and was not run. Every command also prints an `Update available: 8.6.9 -> 9.0.0` notice on stderr when one exists.

To upgrade an existing install, use `orq update` — it resolves the latest release, reuses the install method the binary arrived through, verifies the published `.sha256`, and swaps atomically. `orq update --check -o json` reports and changes nothing. On 5.1.0 it printed `current`, `install_method`, `latest` and `update_available`; read the keys off a real run rather than trusting that list.

**A machine still on `4.x` will not upgrade itself.** `npm update -g` treats a global install as pinned to a caret range of the installed version, so a `4.x` box reports itself up to date forever and never crosses into `5.x`. That one hop needs an explicit `npm install -g @orq-ai/cli@latest`. `orq update` and `install.sh` are unaffected — both install an exact resolved version.

### If `orq` is not found, or is the wrong `orq`

A "failed" `install.sh` is usually `~/.orq/bin` not being on `PATH` — run `~/.orq/bin/orq version -o json` before concluding anything. If `orq` resolves to something printing Node or oclif stack traces, `which orq` is finding a different tool of the same name; use the real binary's full path rather than fighting `PATH`. Details and the profile markers to undo are in [resources/install.md](resources/install.md).

## Phase 2 — Authenticate

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** Two items in this section could not be re-probed because the machine's OAuth session had expired, and they say so where they appear.

There are two credential types and **they are not interchangeable**. This is the single most confusing thing about the CLI, so check it before anything else.

| Command family | `ORQ_API_KEY` | OAuth session (`orq auth login`) |
|---|---|---|
| Generated resource commands (`agents`, `traces`, `projects`, `prompts`, `skills`, `datasets`, …) | works | works |
| Built-ins: `auth whoami`, `workspace list`, `workspace use` | **fails** | works |
| `doctor`'s `auth` block | reports `authenticated`, `source: env:ORQ_API_KEY` | reports `authenticated`, `source: session-file` |

Verified on 8.6.9 with only `ORQ_API_KEY` set and no session file: `orq auth whoami -o json` and `orq workspace list` both print `Error: you are not logged in` and exit **1**, while resource commands return data. `doctor` exits 0 and reports `auth.status: authenticated` with `source: env:ORQ_API_KEY`, `user_email: ""` and `workspace_count: 0` — so it now *does* see the key, but it cannot tell you who or where. With neither a key nor a session it reports `status: missing`, `source: none`.

**`auth.status` values changed.** Earlier releases reported `ok` / `missing` / `invalid`; 8.6.9 was observed to emit `authenticated` and `missing`. Do not match on a remembered literal, and remember that `authenticated` describes what is *configured*, not what works. Read the whole `auth` block — `status`, `source`, `active_workspace_key`, `user_email`, `workspace_count` — and then run a resource command.

**The practical consequences:**

- A key-only setup is fine for reading and writing resources, and cannot tell you who you are or which workspace is active.
- `doctor` saying `auth.status: missing` means neither a key nor a session is configured. Confirm by running an actual resource command before chasing auth.
- Anything needing the workspace key requires an interactive login. There is no key-based path to it.
- **Sessions are short-lived.** On 8.6.9 `orq doctor` has a `bootstrap_token` check (`pass`, with `details.expires_at`); right after login it was about an hour out. *(5.1.0, not re-probed: when it lapsed on a machine that also had `ORQ_API_KEY` set, `whoami` and `workspace *` started reporting "not logged in" while resource commands kept working.)* On 8.6.9 a session with a dead refresh token gave `Error: Invalid refresh token!` from `whoami`. Either way, `orq auth login` again.

```sh
orq auth login                              # interactive OAuth device flow; --api-key <key> signs in with a key instead
export ORQ_API_KEY=...                      # headless / CI, resource commands only
orq auth profile add ci --api-key-file key.txt   # or `-` to read stdin
orq auth profile list                       # also: current, use, clear
orq --profile ci agents list
```

**Renamed since 5.1.0.** `orq auth add-profile apikey <name>` and `orq auth list-profiles` no longer exist: on 8.6.9 both print the `auth` group help instead of running. The commands are `orq auth profile add <name> [<api-key>]` and `orq auth profile list`. `orq auth sessions` lists saved logins, one per host. `orq status` (alias `orq whoami`) shows the active user, workspace, project and credential; `orq auth whoami` still exists separately.

Prefer `--api-key-file` (or `-` for stdin) over passing the key as a positional argument: an argument is visible to every process on the machine through `ps`.

**An explicit `--profile` outranks an exported `ORQ_API_KEY`, and says so.** *(Documented in the upstream CHANGELOG, not observed: a clean two-credential case would have meant mutating saved profiles.)* `ORQ_PROFILE` does not win that tie — env against env has no statement of intent to break it.

**A stray `ORQ_API_KEY` no longer masks an unknown-profile error.** On 5.1.0 an exported key turned `unknown profile "research"` into an unexplained `HTTP 401`. On 8.6.9 the same command fails with the real error whether or not a key is exported: `unknown profile "research" (selected by --profile): credentials.json has no entry of that name. Add it with `orq auth profile add research --api-key-file <file>` …`.

Credential files are `0600` from 5.0.0 onward, but **earlier versions could leave `~/.orq/credentials.json` world-readable permanently** — `orq auth add-profile` never chmodded at all. Nothing repairs an existing file automatically:

```sh
orq doctor          # flags any credential path with loose permissions
orq doctor --fix    # chmods them (0600 files, 0700 dirs); exits 1 if a repair fails
```

If a file was `0644`, treat the key in it as exposed to every account on that machine: rotate it, do not just chmod it. Unix only; the check has no Windows equivalent.

Checking state (`doctor` exits 0 either way; `whoami` exits 1 without a session):

```sh
orq auth whoami -o json -j authenticated --raw    # true | (error text, exit 1, if no session)
orq doctor -o json -j 'auth.status' --raw         # configured, not proven: read the whole auth block
orq agents list -o json -j 'length(data)' --raw   # the only real proof a key works
```

`orq whoami` is an alias for `orq auth whoami`.

Sessions live in `~/.orq/sessions/<host>.json` (on 8.6.9, `my.orq.ai.json`; earlier releases keyed them by profile, `default.json`) and API keys in `~/.orq/credentials.json` / `~/.orq/config.json`. `orq doctor -o json -j 'config.session_file'` names the one in use. After `auth login`, the host you authenticated against is stored in the session and reused, so self-hosted users do not need `--server` on every call *(5.1.0, not re-probed)*.

## Phase 3 — Scope to a workspace

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21,** except where a paragraph says otherwise. The session-dependent claims (`workspace use`, the session-versus-key precedence, the project-scoped-key counts) date from 5.1.0 and are marked.

```sh
orq workspace list -o json
orq workspace use <key>                          # persists in the session; `orq switch [workspace] [project]` is the newer spelling
orq --workspace <key> projects list -o json       # this invocation only
orq --project <id|key|name> ...                   # same idea, one project; also ORQ_PROJECT
ORQ_WORKSPACE=<key> orq projects list -o json     # same, via the environment
```

**`--workspace` is a global flag as of 5.x, and it is usually what you want.** It overrides the session's active workspace for one invocation without persisting anything, so a script can read another workspace without disturbing the user's shell. Verified on 8.6.9 with a session and `ORQ_API_KEY` unset: `orq --workspace capgemini projects list` returned 13 projects where the default workspace (`orq-research`, via the key) returned 25, so the flag does switch what is read. *(5.1.0: it also left `auth whoami`'s `active_workspace_key` unchanged; not re-run on 8.6.9.)* On 8.6.9 `orq --help` lists `--workspace` as a global flag with `[env: ORQ_WORKSPACE]`, and the API-key warning below was reproduced.

This reverses earlier guidance. `ORQ_WORKSPACE` used to be an evaluatorq-only convention that the CLI ignored; it is now a documented CLI variable (`[env: ORQ_WORKSPACE]` in `orq --help`). Reserve `orq workspace use` for genuinely changing the user's default, and use `--workspace` for everything scoped to one command or one script.

**An API key silently wins over `--workspace` — but now warns.** Re-verified on 8.6.9:

```
warning: --workspace has no effect because an explicit API key (ORQ_API_KEY or a
credentials profile) is configured and takes precedence
```

The command then runs against the key's workspace, and usually fails loudly (`HTTP 401: API key is not valid for this workspace`) rather than returning the wrong data. Watch stderr for that warning before concluding `--workspace` is broken.

Workspace entries carry `id`, `key`, `name`, `total_members`, `active`. The **key** is the human-readable slug (for example `orq-research`) that appears in app URLs; the **id** is a UUID (`624ccbbd-a482-…`). Deep-links want the key — a UUID in a Studio route gives an inaccessible page even when the API can read the entity. Note resource ids elsewhere (agents, spans) are ULIDs; workspaces are the exception.

**Both of these commands require an OAuth session.** With only `ORQ_API_KEY` set they fail with `Error: you are not logged in`, at exit 1 (exit 0 on 5.1.0). With a session both exit 0 (verified on 8.6.9); `orq workspace use <key>` prints `✓ Active workspace: <name> (<key>)` and, when `ORQ_API_KEY` is set, a warning that the key takes precedence so the switch will not affect API calls. So workspace selection is not available to key-only setups at all, and neither is reading the active key.

### Which workspace am I really reading?

`ORQ_API_KEY` **wins over an active session** for resource commands. On 8.6.9 the CLI says so itself: `auth login` and `workspace use` both warn `an explicit API key takes precedence` when the variable is set, `auth whoami` reports `credential.source: "ORQ_API_KEY"` (and `"session"` once it is unset), and every resource command prints `Using ORQ_API_KEY from environment` on stderr. *(5.1.0: a deliberately invalid `ORQ_API_KEY` alongside a healthy session returned HTTP 401 rather than falling back to the session; not re-run.)*

That produces the nastiest failure in this skill, because nothing errors:

- `orq auth whoami` reports the workspace you logged into — it only reads the session.
- `orq agents list` reads the **key's** workspace — a different one.
- On 5.1.0, `.env` and `.env.local` autoloaded from the working directory, so the key could arrive without anyone setting it in this shell. **8.6.9 did not autoload either file** (a `.env` and a `.env.local` holding a valid `ORQ_API_KEY`, `ORQ_API_KEY` unset in the shell: `missing API key`), so on a current CLI a key comes from the shell environment or a profile.

A key can also be scoped to a single **project inside** a workspace, which is a third case beyond session-versus-key. *(5.1.0: a session on a 60-project workspace read all 60, while a project-scoped key read 1 for the same command.)* On 8.6.9 the `ORQ_API_KEY` on the probing machine read 69 with `--limit 200` (which workspace it belongs to was not established).

(The full figure needs an explicit `--limit` — `projects list` alone returns 25. See "Lists truncate silently" below; the trap applies to this diagnostic too.)

**The dangerous direction is too few rows, not too many.** `0` reads as "this workspace is empty" and gets accepted and reported; an implausibly large count at least invites a second look. Treat an empty or surprisingly small list as a credential question until proven otherwise.

Use `orq projects list` as the canary, not `agents list`:

```sh
[ -n "${ORQ_API_KEY:-}" ] && echo "key present — resource reads use ITS workspace, not the session's"
orq auth whoami -o json -j active_workspace_key --raw           # session's workspace
orq projects list -o json --limit 200 -j 'length(data)' --raw   # scope of whatever authenticated
```

The `--limit` is not decoration: without it this command returns 25 regardless of how many projects exist, which is the trap two sections down.

`projects list` separates the cases sharply — a project-scoped key returns `1`. `agents list` is a worse canary: it truncates at 10 by default like everything else, so a small number there is ambiguous between a narrow credential and a default page.

**On 5.1.0, `unset ORQ_API_KEY` did not clear the key**, because `.env` and `.env.local` autoloaded from the working directory and the CLI read it straight back off disk. On 8.6.9 `env -u ORQ_API_KEY` is enough: the shell environment is the only place the variable is read from. If you are on an older CLI, run from a directory with no `.env`, or remove the key from that file — nothing warns you which one applied.

Resolve the active key, when a session exists:

```sh
orq auth whoami -o json -j active_workspace_key --raw
```

Fall back to the session file only when the CLI is unavailable. Same value, under a camelCase name:

```sh
jq -r .activeWorkspaceKey ~/.orq/sessions/default.json
```

A snippet for scripts that need the key (for example to build `https://my.orq.ai/<key>/traces?query=…` deep-links). It has to tolerate the command succeeding while producing nothing, which is why the guard is not optional:

```sh
# ORQ_WORKSPACE is read by the CLI itself as of 5.x (still listed on 8.6.9), so honouring it here agrees
# with what the commands will do. ORQ_WORKSPACE_SLUG is an evaluatorq-only
# convention the CLI ignores; it is honoured second so a caller using that name
# can still target a workspace they are not switched to.
workspace_key="${ORQ_WORKSPACE:-${ORQ_WORKSPACE_SLUG:-}}"
if [ -z "$workspace_key" ]; then
  workspace_key="$(orq auth whoami -o json -j active_workspace_key --raw 2>/dev/null)"
fi
case "$workspace_key" in
  ''|null) echo "no active orq workspace; run 'orq auth login'" >&2; exit 1 ;;
esac
```

Keep that in scripts and terminal use. Library code on a request path should not shell out to the CLI — see the note in [resources/command-map.md](resources/command-map.md#building-app-urls).

## Phase 4 — Discover the command

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21,** except where a paragraph says otherwise.

```sh
orq --help                       # top-level groups
orq traces --help                # subcommands in a group
orq traces search --help         # flags, body fields, required fields
orq help-input                   # request-body syntax
orq help-config                  # env vars and config files
```

Every generated group is one API tag. `orq request <method> <path>` is the escape hatch for an endpoint with no generated command; it reuses the configured auth and server.

`orq --help` sorts the groups into seven sections. Knowing the sections is the fastest way to guess where a capability lives before running `--help` on it:

| Section | Groups |
|---|---|
| Get started | `auth`, `connect`, `disconnect`, `doctor`, `launch`, `orqi`, `setup`, `status`, `switch`, `update` |
| AI Gateway | `budgets`, `chat`, `chunking`, `completions`, `embeddings`, `images`, `mcp-gateways`, `mcp-servers`, `model-catalog`, `models`, `moderations`, `ocr`, `pii`, `rerank`, `responses`, `smart-routers`, `speech`, `transcriptions`, `translations` |
| Observability | `alerts`, `feedback`, `identities`, `logs`, `notifiers`, `traces` |
| Managed agents | `agents`, `agents-responses`, `deployments`, `knowledge-bases`, `memory-stores`, `prompts`, `schedules`, `skills`, `tools` |
| Optimization | `annotation-queues`, `datasets`, `evals` |
| Administration | `api-keys`, `files`, `management-keys`, `projects`, `reporting`, `webhooks`, `workspace`, `workspace-security`, `workspace-settings` |
| Utilities | `completion`, `default-format`, `help`, `help-config`, `help-input`, `request`, `server`, `version` |

There is still no `experiments` group — use the MCP tools or the evaluatorq SDK for those.

The **Get started** group is not generated from the API and behaves differently from everything else: those commands write to local config files rather than calling the platform. `orqi` (the orq.ai assistant, installed on first use) and `switch` are new since 5.1.0 and were only read from `--help`, not run. See "Wiring coding agents" below before running any of them.

See [resources/command-map.md](resources/command-map.md) for the full command tree, JMESPath recipes, and body-input patterns.

## Phase 5 — Run with machine-readable output

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.**

```sh
orq agents list -o json
orq agents list -o yaml
orq agents list -o json -j 'data[].{id: _id, name: display_name}'
orq agents list -o json -j 'data[0]._id' --raw   # bare scalar, no quotes
```

**Identifier names are not consistent across resources.** There are three conventions, and JMESPath returns `null` for a missing key at exit 0 — so projecting the wrong one yields a silent column of `null` rather than an error:

| Resource | Identifier | Timestamps |
|---|---|---|
| `agents`, `prompts`, `datasets`, `knowledge-bases` | `_id` | `created` / `updated` |
| `deployments` | `id` (no `_id`) | `created` / `updated` |
| `projects` | `project_id` (neither `id` nor `_id`) | `created_at` / `updated_at` |

Confirm the field on the resource you are actually querying before projecting:

```sh
orq deployments list -o json -j 'data[0]' | jq 'keys'
```

### Lists truncate silently

Most list commands cap by default and set `has_more: true`, which nothing in the output makes obvious — a default `orq deployments list -o json` returns 10 rows and looks complete. Defaults and ceilings, each measured on 8.6.9 by asking for one over the ceiling and reading the `HTTP 400`:

| Resource | Default | Max | Omitting `--limit` |
|---|---|---|---|
| `deployments` | 10 | 50 | truncates |
| `prompts` | 10 | 200 | truncates |
| `datasets` | 10 | 200 | truncates |
| `projects` | 25 | 200 | truncates |
| `knowledge-bases` | 25 | 300 | truncates |
| `agents` | none | 200 | **returns everything** |

**Always check `has_more` or pass an explicit `--limit`.** Never report a count from a default page:

```sh
orq deployments list -o json -j 'has_more' --raw      # true → the count below is wrong
orq deployments list -o json --limit 50 -j 'length(data)' --raw
```

**`agents list` returns every agent when `--limit` is omitted,** and `orq agents list --help` says so ("When not provided, returns all agents without pagination"). On 8.6.9 a bare call returned all 142 agents with `has_more: false` in about 0.8s, and `--limit 200` returned the same 142. Passing `--limit` is still harmless and keeps the call shape uniform with the other lists.

*(5.1.0 differed.)* There a bare `orq agents list` blocked for **4m16s** and failed with `HTTP 503: upstream connect error or disconnect/reset before headers`, and a single `agents retrieve <valid-key>` did the same, so it was a server-side fault on the agents detail path rather than a CLI rule. Neither reproduced on 8.6.9 (`agents retrieve` of a valid key took 0.2s). If a read hangs for minutes and ends in `upstream connect error`, treat it as an upstream fault to report, not something to tune with flags.

`-j` takes JMESPath and runs after the response is parsed. `--raw` unwraps the result so a single string comes out unquoted — use it whenever the value feeds a shell variable.

### `--query` is not the projection flag

The projection flag is the global `-j/--jmespath`. On commands whose request body has a `query` field (`traces search`, `knowledge-bases search`, `webhooks query`, …), `--query` is that **body field — a full-text search**, and a stray `-q` (muscle memory from other CLIs or older orq builds) is an unknown flag everywhere. The quiet failure is the one that costs turns:

```sh
orq traces search -q 'data[].trace_id' ...
# Error: unknown shorthand flag: 'q' in -q          <- loud, harmless

orq traces search --query 'data[].trace_id' ...
# {"data": [], "has_more": false, ...}              <- SILENT: sent as a body
#                                                      full-text search, 0 rows,
#                                                      exit 0
```

Never "fix" a rejected `-q` by reaching for `--query` — use `-j`. When you do pipe to `jq` instead, **always set `set -o pipefail`**:

```sh
set -o pipefail
orq traces search -o json --from ... --to ... | jq -r '.data[].trace_id'
```

`pipefail` is not optional here. On an API error the CLI writes the message to **stderr and leaves stdout empty**, then exits 1. `jq` reading empty input emits nothing and exits **0**, so without `pipefail` the pipeline reports success with zero rows — indistinguishable from "no traces matched". Re-verified on 8.6.9: a rejected request produced 0 bytes on stdout and a message on stderr; the `| jq` pipeline exited 0 without `pipefail` and 1 with it.

### The trace filter contract

`--filters` is the other thing agents reliably get wrong on `traces search`. Do not guess the shape. It is `field` / `op` / `values`, where **`values` is always an array, even for `eq`**:

```sh
set -o pipefail
# -v-7d is BSD/macOS. On GNU/Linux: date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ
orq traces search -o json \
  --from "$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ)" --to "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --limit 100 \
  --sort '[{"field":"end_time","order":"desc"}]' \
  --filters '[{"field":"status","op":"eq","values":["error"]}]' \
  | jq -r '.data[].trace_id'
```

The window is computed rather than hard-coded so it cannot age past the 30-day retention boundary, and the sort is explicit because results are otherwise unordered. Both are covered below.

The two near-miss spellings both fail, and on 8.6.9 the errors at least name the offending key (5.1.0 gave misleading messages for both — a regex mismatch for `operator` and `expects exactly one value` for `value`):

```
"operator" instead of "op" ->
  HTTP 400: proto: (line 1:31): unknown field "operator"

"value" instead of "values" ->
  HTTP 400: proto: (line 1:41): unknown field "value"
```

An `op` outside the set is rejected with the API's own validation regex: `filters[0].op: does not match regex pattern ^(eq|neq|in|not_in|gt|gte|lt|lte|between|contains|exists|not_exists)$`. Which of those a given field accepts is in its `list-fields` entry (`operators`); for example `trace_id` accepts `eq`, `neq`, `in`, `not_in` and `contains`.

### Only one sort exists; ordering is not promised without it

Exactly one sort is accepted, and `list-fields` agrees: `end_time` is the only field with `sortable: true`. `started_at` is rejected:

```sh
--sort '[{"field":"end_time","order":"desc"}]'      # the only supported sort
--sort '[{"field":"started_at","order":"desc"}]'
# HTTP 400: invalid sort: only end_time desc is supported   (re-verified on 8.6.9)
```

On 5.1.0 an unsorted `traces search` came back only partly in time order (40 rows over 7 days, descending for the first 8 and then not). On 8.6.9 an unsorted 40-row page over 7 days was fully descending by `end_time`. That is an observation, not a contract: to answer "what is the latest trace", pass the sort explicitly rather than relying on `data[0]`.

### The retention window

On 5.1.0 a window starting more than 30 days back was a hard `400` (`range outside retention: requested range starts before 30 day retention`). **That did not reproduce on 8.6.9:** `--from` 31, 45, 90 and 400 days back were all accepted and returned rows. Whether older traces are actually retained, or the range is silently clamped, was not established, so keep computing the window relative to now rather than hard-coding `--from`, and do not read an old window's result as complete.

`--from` and `--to` are optional on 8.6.9: `orq traces search --help` says that with neither, the window is the last 7 days.

Discover field names rather than guessing them:

```sh
orq traces list-fields -o json     # queryable fields, under `data`
orq traces list-facets -o json     # facetable fields
```

The registry **grows and renames between releases**: 56 → 57 fields in one afternoon when `attr.*` became `attributes.*`, 66 on 5.1.0, **168 on 8.6.9** (and 50 facets). Each entry now carries `name`, `type`, `operators`, `sortable`, `facet`, `groupable`, `scope` and an `aliases` list, and 108 of the 168 have aliases (for example `operation` is also `attributes.gen_ai.operation.name`), so a renamed field can stay reachable under its old spelling. A name that resolves to nothing now **fails loudly**: `HTTP 400: invalid filter: unknown field "attr.nonexistent_zzz"`. On 5.1.0 it returned zero rows without erroring, which looked exactly like "no matching traces", so a script written for 5.1.0 may carry a workaround it no longer needs. Resolve names at call time; never hard-code one from this document.

### Reading a conversation: `traces conversation`

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21,** on a live trace with a 3-message conversation (first probed on 8.5.2, RES-1507): `--spans` (marks the chosen span, `NOTE` says why others were skipped), the default `xml` and `markdown` renders, `--slice`, `--max-chars`, `-o json` (keys `messages`, `source`), and the exits below all behaved as documented. `--include` and `--reasoning` were not re-run. **On 8.6.9 the command is `traces thread` and it has no `conv` alias** (`orq traces conv` prints the `traces` group help at exit 0), so the `conv` spellings below apply to the release that renamed it.

`orq traces conversation <trace-id> [span-id]` renders a trace's conversation instead of its span JSON. `conv` is the short spelling. Added in 7.4.0 (RES-1507) as `traces thread`, substantially extended by 8.5.2, and renamed to `conversation` in the next major after 8.6.9 — the old name was dropped outright, so on an 8.x CLI it is still `traces thread`. `orq traces --help` says which one you have. **Do not reconstruct a conversation out of `get-span` attributes** — the payload shapes differ per dialect (Chat Completions, OpenAI Responses, the flattened OpenTelemetry GenAI shape orq collectors emit) and `conversation` normalizes all three into one model. It is also far cheaper to read: on one live Responses span the default render was roughly an order of magnitude smaller than the raw `get-span -o json`, and `-o json` about a quarter of it.

```sh
orq traces conv <trace-id>                     # picks the span, names it in the output
orq traces conv <trace-id> <span-id>           # that span, nothing else
orq traces conv <trace-id> --spans             # which span it picks, and the alternatives
orq traces conv <trace-id> -o markdown         # to paste into a ticket or chat
orq traces conv <trace-id> -o json             # the canonical conversation, not the raw span
orq traces conv <trace-id> --slice -1          # last message; also 2, 2:, :-1, 1:3
orq traces conv <trace-id> --match get_weather # only turns matching a regexp
orq traces conv <trace-id> -i user,assistant   # only these parts
```

**Its `-o` is its own, not the CLI-wide one.** The enum is `[xml, markdown, json, yaml, toon]`, default `xml`; `ORQ_OUTPUT_FORMAT` and the config file are not read, so the workspace default never reaches this command. `-o table` is refused outright (exit 1, before any request):

```
Error: --output-format: "table" is the CLI-wide default layout, and a conversation has no columns to lay out.
This command takes [xml, markdown, json, yaml, toon]; xml is what it renders when you ask for nothing
```

Two human renders, and the difference is a security property, not taste. **`xml`** (default) frames turns as `<message index=… role=…>` elements and escapes its own tag names where they appear in recorded content, so a span body cannot forge a turn or desync the indices `--slice` refers to. **`markdown`** uses `## USER [1]` headings and fenced JSON for tool arguments — readable, pasteable, and forgeable by content. Default to `xml` for anything you will act on; reach for `markdown` when a human is reading it.

In both, the `index` is the `--slice` index and the system message is `0`, so `--slice 0` is the system prompt. Missing content is named rather than invented: `[content unavailable]`, `[content unavailable: N items]`, `[truncated: N more characters]`, `[unsupported content: <type>]`, `[redacted thinking]`.

Flags beyond `-o`, all verified live on 8.5.2:

- `--spans` prints the span table instead of a conversation: `TRY` (read order, not a ranking), `SPAN`, `TYPE`, `STARTED`, `TURNS` (messages found, blank when not read), `NAME`, `NOTE` (why a span was passed over), with `*` on the one selected. Run it when you doubt the selection — `TURNS` is what tells you the right span was picked.
- `--match <regexp>` keeps messages whose recorded text matches, searching message text, reasoning, JSON values, and tool calls by name, id and arguments. Case-insensitive; `(?-i)` inline to respect case. Surviving messages **keep their original indices**, so a filtered render can read `0, 2, 4`.
- `-i/--include` renders only the named parts: `system` (covers developer), `user`, `assistant`, `tool`, `reasoning`. Naming no role keeps every role, so `-i reasoning` is the thinking from all of them and `-i user,assistant` is the turns without it.
- `--max-chars` (default **4000**) cuts each rendered block and appends `[truncated: N more characters]`; `--max-chars 0` lifts the cap. Applied last, so `--match` still searches the full text. Only text inside elements is cut, so the XML stays well-formed.
- `--reasoning=false` drops reasoning everywhere, including `-o json`.

Filters compose in a fixed order: `--slice`, then `--match`, then `--include`, then `--max-chars`.

Three exit-code facts, each from a recorded call on 8.5.2:

- **An empty selection is exit 0, not an error.** `--slice 99:` on a 5-message conversation, and `-i reasoning` on one with none, both printed the `<conversation …>` header with no messages and exited **0**. Bounds clamp like Python; check for messages rather than trusting the exit code.
- **A bad slice expression exits 1** and names the grammar: `invalid slice expression "nonsense": expected an index or a range, for example 2, 2:, :-1 or 1:3`.
- **A trace with no conversational span exits 1**: `no supported conversation found in trace "<id>"`. That is a real answer (evaluator-only traces do this), not a bug to work around — and it is distinct from a bad id, which is `HTTP 404: trace not found`.

`-o json` / `-o yaml` / `-o toon` all serialize the same canonical structure — `messages[]` with `index`, `role`, `content[]`, optional `tool_calls[]` / `name` / `tool_call_id`, plus a `source` object carrying `trace_id`, `span_id`, `representation`, `model`, `tokens`, `duration_ms`. There is no per-message `finish_reason`. The global `-j` projects it (`-j 'source.model'` → `"gpt-4o-mini"`).

### OQL: a second query language, with its own rules

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** Every claim below reproduced except the crash text, which changed.

`orq traces query-oql` and `orq logs query` take a pipeline expression instead of the filter/sort structure above. They are not drop-in alternatives — three things differ, and each fails in its own way.

**The source is fixed per command.** `traces query-oql` accepts only `fetch traces`; `fetch spans` and `fetch logs` both give `HTTP 400: invalid oql: query must start with fetch traces`. `logs query` takes `fetch logs`, with the grammar its help states: `fetch logs | filter <expr> | sort timestamp desc | limit N`, where `timestamp desc` is the only sort.

**Equality is not `=`.** This is the one that costs turns, because every spelling fails with the same unhelpful message:

```sh
--oql 'fetch traces | filter status = "error" | limit 2'    # invalid oql: invalid filter
--oql "fetch traces | filter status = 'error' | limit 2"    # invalid oql: invalid filter
--oql 'fetch traces | filter status eq "error" | limit 2'   # invalid oql: invalid filter
--oql 'fetch traces | filter status:"error" | limit 2'      # invalid oql: invalid filter

--oql 'fetch traces | filter status in ("error") | limit 2' # works
```

Use list membership — `in (…)` / `not_in (…)` — for equality. Combining it with a comparison is rejected too: `filter status in ("error") and total_cost > 0` gives `invalid oql: malformed list`. When a query needs mixed operators, drop back to `traces search --filters`, which supports the full operator set.

**The response is shaped differently.** OQL results are nested under `search`, not at the top level:

```json
{"object": "query",
 "search": {"data": [], "has_more": false, "next_page_token": "",
            "meta": {"from": "…", "to": "…", "request_id": "…", "row_count": 0},
            "object": "list", "total_count": "0"}}
```

So `-j 'length(data)'` is wrong here; project `search.data` or `search.meta.row_count`. And projecting a key that does not exist **fails** rather than yielding `null` — `orq logs query … -j '[length(data),has_more]'` gives `Error: formatting failed: Invalid type for: <nil>, expected: []jmespath.jpType{"string", "array", "object"}` (5.1.0 printed it as a `FATAL logs_commands.go:637` line). Confirm the envelope before projecting:

```sh
orq traces query-oql -o json --from "$F" --to "$T" --oql 'fetch traces | limit 1' -j 'keys(@)'
# ["object", "search"]
```

Both commands paginate with `--page-token` against `next_page_token`, not with the `has_more` + offset pattern used elsewhere.

### Request bodies

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.**

Commands that take a body accept it several ways, which compose:

```sh
orq traces search --from 2026-07-01T00:00:00Z --to 2026-07-31T00:00:00Z --limit 20 -o json
echo '{"from":"...","to":"...","limit":20}' | orq traces search -o json
orq traces search --from-file body.json -o json
```

`--example` prints a generated body and exits without sending a request. It works on `traces search` — re-verified, it prints `{"from": "2024-01-01T00:00:00Z", "to": "2024-01-01T00:00:00Z"}` — where it once failed with `no generated body example is available for this command`. It is not populated for every body command, and what it prints is the **required scalars only**: the example above omits `filters`, `sort` and `limit` entirely. Use it to confirm field names and the required set, not as a working query. Note that `--from` and `--to` are no longer required on `traces search` (a bare call searches the last 7 days), but `query-oql` still lists `from`, `oql` and `to` as required. `--stdin` requires piped input and `--from-file` reads a path.

CLI shorthand applies on top of any base body, so you can override one field of a file without editing it. Run `orq help-input` for the full shorthand grammar.

### Persisting a default format

```sh
orq default-format json      # accepts json, yaml, toon or table
```

*Not probed on any version: `orq default-format --help` was read on 8.6.9, and the command was never run.* Per the CLI's own docs this writes to `~/.orq/config.json` and changes the default output format for **every** `orq` invocation by that user, including their interactive shell and other agents. Treat it as machine-wide until proven otherwise: pass `-o json` per command, and only persist a default when the user explicitly asks.

### Deleting requires `--force` off a terminal

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.**

Since 5.0.0 every generated `delete` command prompts for confirmation and **refuses to run when stdin is not a terminal** — which is every agent, script and CI invocation. That covers every generated `delete*` subcommand (33 top-level `delete` and `delete-*` entries counted on 8.6.9, plus `orq request DELETE`):

```sh
orq agents delete <id> --force        # required non-interactively
orq request DELETE /v2/agents/<id> --force
```

**What happens without it**, verified on 8.6.9 — exit **1**, nothing on stdout, one line on stderr, and **no request is sent**:

```
Error: refusing to run "orq agents delete <id>" without --force in a non-interactive shell
```

`orq request DELETE` gives the identical message with its own command line in it. That the request is never sent is not an inference: the same nonexistent id *with* `--force` reached the API and came back `HTTP 404: {"message":"Agent not found"}`, while without it there was no HTTP response at all.

So this is a **loud** failure, unlike most of the traps in this skill — it costs a retry, never data. If a script that used to work now exits 1 with that message, the fix is to add `--force` after confirming the id, not to debug auth.

Only DELETE is gated. Reads on the same resource are unaffected (`orq agents retrieve <id>` needs no flag); writes such as `agents update` were not exercised.

Two related 5.0.0 changes make delete safer rather than just noisier. Path parameters are URL-escaped: on 8.6.9 `orq datasets retrieve '../../etc/passwd'` sent `GET /v2/datasets/..%2F..%2Fetc%2Fpasswd`, which the server answered with a `307`, and the CLI printed **nothing and exited 0** — so it no longer traverses, but it does not say 404 either. And an **empty id is rejected before any request** — `orq agents delete ""` used to build a collection URL and hit `/v2/agents`, and now fails with `path parameter agent_key cannot be empty` (re-verified).

Confirm the id resolves to what you think before adding `--force`. The flag removes the only prompt standing between a wrong id and a deleted entity.

## Wiring coding agents

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21,** by reading `--help` and running `orq connect --status` under a scratch `HOME`. Nothing was written to any agent's config, so the write behaviour below dates from 5.1.0.

`connect`, `disconnect`, `setup`, `launch` and `update` are hand-written rather than generated: they **write to local config files** instead of calling the platform, wiring a coding agent (`claude`, `codex`, `opencode`, `kimi`, `kilo`, `pi` for `connect`; `launch` also lists `copilot` and `gemini`) to the orq AI Gateway, MCP server, or skills directory.

**Always start with `orq connect --status` or `--dry-run`** — these edit files the user's other tools depend on. `orq launch <agent>` is the non-persistent option, and it propagates the launched agent's exit status verbatim, so it is the one command whose exit code is not the contract below.

See [resources/coding-agents.md](resources/coding-agents.md) for the capability matrix, the `--local` scoping rule, and the `orq skills` / `skills`-capability name collision.

**Exit codes** everywhere else: `0` success, `1` any failure, `130` SIGINT, `143` SIGTERM. Remember that a typo'd subcommand is a `0` (see Constraints).

## Troubleshooting

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** Rows whose fix depends on the 5.1.0 behaviour say so.

`orq doctor` (or `orq doctor -o json`) is the starting point, with two blind spots worth knowing before you trust it:

- Its `auth.status` says what is *configured*, not what works. It reports `authenticated` for a session file whose refresh token is dead (`whoami` then fails with `Invalid refresh token!`), and for any exported `ORQ_API_KEY` (`source: env:ORQ_API_KEY`, with an empty `user_email`). On 5.1.0 it ignored `ORQ_API_KEY` entirely; that is no longer true.
- It reports where the host came from (`flag`, `env`, `config`, `session`, `default`), but `orq server current` is still the direct answer for the resolved server.

It does reliably report the binary and its `api_version`, the active profile and session path (`config.session_file`, `config.session_host`), the base URLs with their source, credential-file permissions (with `--fix` to repair them), and reachability probes; the coding-agent rows appear when agents are detected. `orq doctor --report` prints a pre-filled GitHub issue URL for filing a bug.

| Symptom | Likely cause | Fix |
|---|---|---|
| `you are not logged in` on `whoami` / `workspace`, but resource commands work | key-only setup; these need a session | `orq auth login`, or accept the limitation |
| `Invalid refresh token!` from `whoami`, yet `doctor` says `authenticated` | the session file exists but its token is dead; `doctor` only checks the file | `orq auth login` again |
| `refusing to run "…" without --force in a non-interactive shell` | 5.0.0 gates DELETE on confirmation; no TTY means refuse. Nothing was sent | add `--force` after confirming the id resolves |
| A typo'd subcommand "succeeds" with no data | unknown subcommands print help to stdout at exit **0** | compare the output against `--help`; do not trust `$?` alone |
| An `agents` read hangs for minutes, then `HTTP 503 upstream connect error` | seen on 5.1.0 only, server-side on the agents detail path; did not reproduce on 8.6.9 | report it rather than tuning flags |
| `--workspace` appears ignored | an API key outranks it | read stderr for the `--workspace has no effect` warning; unset the key or use `--profile` |
| `unknown profile "x"` becomes an unexplained `HTTP 401` | 5.1.0 only: a stray `ORQ_API_KEY` masked the unknown-profile check | upgrade; on 8.6.9 the real error shows either way |
| `orq auth add-profile` / `list-profiles` print group help | renamed to `orq auth profile add` / `list` | use the new names |
| `invalid oql: invalid filter` on an obviously valid filter | OQL has no `=`; equality is `in (…)` | rewrite as `filter f in ("v")` |
| `-j` gives `formatting failed: Invalid type for: <nil>` | projecting a key the envelope lacks (common on OQL) | check `-j 'keys(@)'`; OQL nests results under `search` |
| `invalid filter: unknown field "…"` on `traces search` | the name is not in the registry (5.1.0 returned zero rows instead) | resolve it from `orq traces list-fields`; check its `aliases` |
| `--output-format: "x" is not one of [json, yaml, toon, table]` | 8.x rejects an unknown `-o` value (5.1.0 fell back silently) | use one of the four |
| `npm update -g` says the CLI is current, but it is on `4.x` | a global install is pinned to a caret range | `npm install -g @orq-ai/cli@latest`, or use `orq update` |
| `orq: command not found` right after `install.sh` | the binary is at `~/.orq/bin`, not on `PATH` | run `~/.orq/bin/orq version -o json`; add the dir to `PATH` |
| `install.sh` starts an interactive login you did not want | it runs `orq setup` unless told otherwise | re-run with `--no-setup` (and `--no-modify-path`) |
| `orq update` refuses to act on this binary | a dev build, or an install method it cannot drive | rebuild from source, or reinstall via npm / `install.sh` |
| Empty lists where data should be | wrong workspace, or a projection sent as `--query` full-text search | `orq workspace list`; re-run with `-j`, not `--query` |
| `unknown shorthand flag: 'q'` | there is no `-q` — the projection flag is `-j/--jmespath` | re-run with `-j`; do **not** switch to `--query` |
| `unknown command` | subcommand moved or renamed between releases | `orq <group> --help`; check `orq --version` |
| `jq` fails on `orq --version` | the `--version` flag prints plain text and takes no format | use the subcommand: `orq version -o json` |
| Output is unparseable | TOON when piped | add `-o json` |
| Requests hit the wrong host | `ORQ_SERVER`, a profile-bound host, or a persisted default | `orq server current`; set hosts with `--server` only |
| `warning: --api-base-url is deprecated` | the pre-5.0.0 flag for the auth host | replace it with `--server` — same value, one name |
| Nothing prints and `$?` is 0 after `retrieve` on an odd id | the server answered with a redirect (`307`) | check the id; the CLI does not surface redirects |
| `HTTP 404` on a documented command | endpoint in the spec but not served by this deployment | confirm with `orq request GET <path>`; if that also 404s it is server-side |
| Works locally, fails in CI | OAuth session is not portable | use `ORQ_API_KEY`, and avoid `whoami` / `workspace` in CI |

On 5.1.0, `.env` and `.env.local` in the working directory were loaded automatically, so a stray `ORQ_SERVER`, `ORQ_API_KEY`, `ORQ_WORKSPACE`, or `ORQ_OUTPUT_FORMAT` in a project file could silently change behaviour. **On 8.6.9 a `.env` and a `.env.local` holding `ORQ_API_KEY` were both ignored**, so only the shell environment counts; on an older CLI keep the 5.1.0 hazard in mind. Either way the env var the CLI reads for a key is `ORQ_API_KEY` specifically; a project using a different name (`ORQ_KEY`, say) will not authenticate the CLI.

### Setting the host: `--server`, and nothing else

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** The profile-binds-a-host paragraph at the end is from 5.1.0 and not re-probed.

**`--server <url>` / `ORQ_SERVER` is the only way to point the CLI at a host.** It works on every command, built-in and generated, including `orq auth login --server https://orq.acme.internal`. Use it and stop there.

`--api-base-url` and `ORQ_API_BASE_URL` are the **deprecated** old spellings. Do not write them into anything new, and replace them when you find them — the value is identical, so it is a rename, not a migration:

```sh
orq doctor --api-base-url https://api.orq.ai      # old — deprecated
orq doctor --server       https://api.orq.ai      # new — do this
```

Re-verified on 8.6.9: `orq doctor --api-base-url …` still runs and prints `warning: --api-base-url is deprecated and will be removed in a future release; use --server instead`. Upstream said it would be removed in a following minor and it has outlived several, so do not count on either outcome. It was also never accepted on generated commands — `orq projects list --api-base-url …` gives `Error: unknown flag` (re-verified) — which is exactly the split `--server` exists to end.

Why it matters beyond tidiness: until 5.0.0 these were **two different hosts**, not two names for one. Six built-in commands took `--api-base-url` and rejected `--server`, every generated command did the reverse, and a single run could talk to two hosts at once. Anything you find that sets both is working around that old split and should collapse to one `--server`.

The default host also moved from `https://api.orq.ai` to `https://my.orq.ai` (`orq server current` on 8.6.9 reports `https://my.orq.ai` with an empty `server_override`) — both answer the same routes, but a self-hosted deployment that only allow-listed one name will notice.

(The `config.api_base_url` field in `orq doctor -o json` output is unrelated — that is a response field name, not the flag, and it keeps its spelling.)

A profile now carries its own host as well as its own credentials, and both beat the wider setting: `orq auth login --server <url> --profile acme` binds that host, so `orq --profile acme …` routes there with no flag.

---

## orq.ai Documentation

**CLI:** [orq-cli repository](https://github.com/orq-ai/orq-cli) · [Releases](https://github.com/orq-ai/orq-cli/releases) · [`@orq-ai/cli` on npm](https://www.npmjs.com/package/@orq-ai/cli) · [installer](https://cli.orq.ai/install.sh) · [CHANGELOG](https://github.com/orq-ai/orq-cli/blob/main/CHANGELOG.md)

The upstream CHANGELOG is the authority on behaviour changes between releases — it carries a stability contract and flags breaking changes explicitly. Read it before assuming this skill is current.

**API:** [API reference](https://docs.orq.ai/reference) · [Agents](https://docs.orq.ai/reference/agents)

**Shorthand syntax:** [bartolo shorthand](https://github.com/orq-ai/bartolo/tree/main/shorthand#readme)

### Key Concepts

- A **profile** is a named credential set holding an API key and a server host. Sessions are keyed by **host** (`~/.orq/sessions/my.orq.ai.json`) rather than by profile as on 5.1.0, so a browser login belongs to a server and is selected with `--server`, not `--profile`.
- A **workspace key** is the slug in app URLs; a workspace **id** is a UUID. The CLI accepts the key for `workspace use` and reports both in `workspace list`. Do not put the UUID in an app URL.
- **TOON** is the CLI's human-facing output format (what a piped call prints by default on 8.6.9, although `--help` names `table` as the default). It is not JSON and should never be parsed — upstream states explicitly that TOON is presentation-only and its rendering may change between releases without notice. `-o json` on stdout is the machine contract.
- Generated commands mirror the OpenAPI spec one-to-one, so a command group maps to an API tag and a subcommand maps to an operation. The **Get started** group (`connect`, `disconnect`, `launch`, `setup`, `update`) is hand-written and writes local config instead.
- The **CLI version is not the API version.** They were decoupled at 5.0.0; `orq version -o json` reports `cli`, `api_version` and `install_method` separately. An `8.6.9` CLI built against API `4.14.20` is normal.
- *(5.1.0 CHANGELOG claim, not re-probed.)* The command surface is tracked upstream in `surface.json` and CI fails any uncommitted change to it, so a command or flag cannot vanish silently between releases — but it *can* be removed deliberately after one release's notice. `--help` remains the source of truth.
