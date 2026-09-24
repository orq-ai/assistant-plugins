# orq CLI Command Map

The CLI is generated from the orq.ai OpenAPI spec, so this drifts between
releases — `orq <group> --help` wins over anything written here.

Every section opens with a `Probed against` line naming the `orq` version it was
last checked on; `grep -n 'Probed against' skills/orq-cli/resources/command-map.md`
lists them, and the oldest is the ceiling on how far to trust the file. Most were
re-probed on **8.6.9** (built against orq API 4.14.20) on 2026-09-21 with an API
key and no live OAuth session, so anything that needs a session says so and
keeps its 5.1.0 stamp.

Since 5.0.0 the CLI's version is its own and no longer tracks the API's; run
`orq version -o json` to read both. The command surface is tracked upstream in
`surface.json`, so it cannot change silently, but it can change deliberately.

To re-derive the tree after a CLI upgrade, instead of editing it by hand. On 8.x
`orq --help` sorts groups into named sections rather than one `Available
Commands:` block, so the script reads either shape. Run it
with `bash` explicitly — under `zsh` (the macOS default) unquoted parameters are
**not** word-split, so `for g in $groups` iterates once with the whole list as a
single value and the script silently produces nothing useful:

```bash
#!/usr/bin/env bash
groups=$(orq --help 2>&1 | awk '/^Usage:/{next} /^[A-Z][A-Za-z ]+:$/{f=1;next} /^Flags:/{f=0} f&&/^  [a-z]/{print $1}')
for g in $groups; do
  subs=$(orq "$g" --help 2>&1 | awk '/Available Commands:/,/^Flags:/' | grep '^  [a-z]' | awk '{print $1}' | tr '\n' ' ')
  [ -n "$subs" ] && echo "$g: $subs" || echo "$g: (leaf command)"
done
```

If you must run it under `zsh`, split explicitly: `for g in ${=groups}`.

Response field names are not in the help text; they come from the spec the CLI
was generated from:

```sh
curl -fsSL https://raw.githubusercontent.com/orq-ai/orq-cli/main/openapi.yaml -o /tmp/orq-openapi.yaml
```

---

## Global flags

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.**

| Flag | Effect |
|---|---|
| `-o, --output-format` | `json`, `yaml`, `toon` or `table`; `--help` says the default is `table`, and a piped call printed TOON (a real terminal was not probed). This is the only way to ask for JSON. `traces thread` takes its own set and refuses `table` |
| `-j, --jmespath` | JMESPath expression applied to the response |
| `--raw` | Emit a string or scalar-list result unquoted, one item per line, instead of serializing it |
| `--columns` | Comma-separated columns for `table` output |
| `--profile` | Credential profile. Documented to outrank `ORQ_API_KEY` when typed explicitly (not observed) |
| `--server` | Override the server URL for this call. Replaces the deprecated `--api-base-url` |
| `--workspace` | Workspace key for this invocation, overriding the session's active one. Does not persist |
| `--project` | Project id, key or name for this invocation, overriding the session's active project |
| `--no-color` | Disable colour (`NO_COLOR` is honoured too) |
| `--no-input` | Never prompt; fail instead of asking |
| `--verbose` | Verbose log output. Writes the whole profile config to stderr; **on 8.6.9 the keys in it are masked (`**HIDDEN**`), on 5.1.0 they were plaintext.** Still not for shared output |
| `-v, --version` | Print the version. Takes no `-o` |

Each flag has an env-var twin, and `orq --help` prints it next to the flag:
`ORQ_OUTPUT_FORMAT`, `ORQ_JMESPATH`, `ORQ_RAW`, `ORQ_COLUMNS`, `ORQ_PROFILE`,
`ORQ_SERVER`, `ORQ_WORKSPACE`, `ORQ_PROJECT`, `ORQ_NO_COLOR`, `ORQ_NO_INPUT`,
`ORQ_VERBOSE`. Only `ORQ_OUTPUT_FORMAT` was exercised (`json` and `yaml` both
took effect on `projects list`); the others are as `--help` lists them. Do not
lean on the environment where an explicit `-o json` is available.

**Enums are checked client-side now.** `-o bogus` fails at exit 1 with
`Error: --output-format: "bogus" is not one of [json, yaml, toon, table]`; on
5.1.0 it silently fell back to JSON.

Two precedence facts. An explicitly typed `--profile` is documented to beat an
exported `ORQ_API_KEY` while `ORQ_PROFILE` does not (env against env has no
statement of intent to break the tie) — *not observed on 8.6.9*. `--workspace`
loses to an API key either way, with `warning: --workspace has no effect because
an explicit API key (ORQ_API_KEY or a credentials profile) is configured and
takes precedence` on stderr — *observed*.

Two deprecated spellings: `--api-base-url` still runs on `doctor` and prints
`warning: --api-base-url is deprecated and will be removed in a future release;
use --server instead`, and is `unknown flag` on generated commands (both
observed). `ORQ_API_BASE_URL` was not probed. Both mean `--server` /
`ORQ_SERVER`.

**There is no `-q`/`--query` global flag.** Muscle memory from other CLIs (or
older orq builds) will produce `Error: unknown shorthand flag: 'q' in -q` on
every command — the projection flag is `-j`. There is no `--json` flag either
(`unknown flag: --json`).

### Body fields do not collide with global flags

Per-field flags are generated from each command's request body. When a body
field would clash with a global flag name, the generator renames the *body*
flag with a `body-` prefix and keeps the global one working — verified:
`images generate` exposes its `output_format` body field as
`--body-output-format`, and its help text says so explicitly. A body field
named `query` simply gets `--query`, because the global projection flag is
`-j/--jmespath`.

**The trap that remains:** on search commands (`traces search`,
`knowledge-bases search`, `webhooks query`, …) `--query` is the body's
**full-text search field**, not a projection. `orq traces search --query
'data[].trace_id'` parses fine, is sent as a full-text search for that literal
string, and returns zero rows at exit 0 — it looks like "no data" rather than
"wrong flag". Project with `-j`; use `--query` only when you mean full-text
search.

Built-in exception: on `auth setup`, `--profile` is the profile being created
or updated, not the global credential-profile selector.

Two related parsing facts, *from 5.1.0 and not re-probed on 8.6.9*:

- **Plain strings are accepted by generated string flags** (`--model`, `--input`,
  …). Only nested objects, arrays of objects, and unions need a JSON string.
- **Body-field enums were not validated client-side on 5.1.0**: an invalid value
  passed flag parsing and the server rejected it. The *global* `-o` is now
  validated (above), so do not assume the same for body fields either way; read
  the error. (`images generate --help` on 8.6.9 still mentions
  `--body-output-format`, so the `body-` rename above holds.)

Config files were read from `~/.orq/config.json` (and `/etc/orq/config.json` on
Unix) on 5.1.0, using the same key names (`{ "output-format": "json" }`); not
re-probed.

**`.env` and `.env.local` no longer autoload.** On 5.1.0 they loaded from the
working directory. On 8.6.9 a `.env` and a `.env.local` holding a valid
`ORQ_API_KEY` were both ignored with the key unset in the shell (`missing API
key`), and a bare `ORQ_KEY=` line changed nothing.

### Environment variables

> **Probed against 8.6.9, 2026-09-21,** for the variables `orq --help` prints
> beside a flag (`ORQ_API_KEY` was used for every probe). The install and dev
> rows (`ORQ_V1_BASE_URL` down) are from the 5.1.0 pass and were not re-probed.

| Variable | Purpose |
|---|---|
| `ORQ_API_KEY` | API key for headless / CI auth. **Resource commands only** — `whoami` / `workspace` exit 1 with `you are not logged in` |
| `ORQ_OUTPUT_FORMAT` | Default `-o`. Verified on `projects list`; `traces thread` states it does not read it |
| `ORQ_PROJECT` | Project for the invocation (twin of `--project`) |
| `ORQ_PROFILE` | Default profile |
| `ORQ_SERVER` | Base URL for **every** command, built-in and generated. The one host name |
| `ORQ_WORKSPACE` | Workspace key for the invocation. Overrides the session's active workspace, persists nothing |
| `ORQ_API_BASE_URL` | **Deprecated** *(5.1.0, not re-probed)*. A spelling of `ORQ_SERVER`; warns on stderr |
| `ORQ_V1_BASE_URL` | Override the v1 base URL (local dev) |
| `ORQ_PROFILE_BASE_URL` | Override the profile endpoint (local dev) |
| `ORQ_NO_UPDATE_CHECK` | Suppress the daily update notice (also skipped when `CI` is set) |
| `ORQ_CLI_VERSION` | Version pin for `install.sh` |
| `ORQ_CLI_CHANNEL` | `rc` installs the pre-release line (`install.sh --channel rc`) |
| `ORQ_CLI_INSTALL_DIR` | Install directory for `install.sh` |

`ORQ_SERVER` and `ORQ_API_BASE_URL` **used to be two different hosts** *(5.0.0 history, from the changelog)*. Until
5.0.0 the six built-in commands took `--api-base-url` and rejected `--server`
while every generated command did the reverse, so a single run could reach two
hosts at once. They are now one value under one name, and the default host moved
from `https://api.orq.ai` to `https://my.orq.ai`. Set `ORQ_SERVER`.

The CLI also reads `ORQ_AUTHORIZATION` and `ORQ_TOKEN` as credential inputs
alongside `ORQ_API_KEY`, plus the generic `ORQ_<FLAG>` twins.

`ORQ_WORKSPACE_SLUG`, `ORQ_UI_BASE_URL`, and `ORQ_BASE_URL` are **not** CLI
variables — those remain evaluatorq conventions, and setting them changes
nothing about `orq` behaviour. `ORQ_WORKSPACE` was in that list until 5.x and no
longer is.

---

## Built-in commands

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** Rows that need a live session were read from `--help` only.

These are hand-written, not generated from the spec. That distinction is not
cosmetic: **the identity built-ins accept only an OAuth session, never
`ORQ_API_KEY`.** `orq auth whoami` and `orq workspace list` both fail with `Error:
you are not logged in` when a valid key is exported. They exit **1** on 8.6.9
(0 on 5.1.0), so the exit code is a usable signal now.

| Command | Purpose |
|---|---|
| `orq auth login` | OAuth device login (interactive, needs a browser) |
| `orq auth logout` | Revoke the refresh token, clear the local session |
| `orq auth whoami` | Current identity, workspaces, resolved URLs |
| `orq status` (alias `orq whoami`) | Active user, workspace, project and credential. New since 5.1.0; `orq whoami` is now this, not `auth whoami` |
| `orq switch [workspace] [project]` | Switch the active workspace and project |
| `orq auth setup` | Interactive auth configuration |
| `orq auth sessions` | List saved logins, one per host |
| `orq auth profile add <name> [<api-key>]` | Save an API-key profile (`--api-key-file <f>`, `-` reads stdin; a positional key is visible via `ps`). Replaces `auth add-profile apikey` |
| `orq auth profile list \| current \| use \| clear` | Manage profiles. Replaces `auth list-profiles`; the old names print the `auth` help at exit 0. With none configured, `auth profile list -o json` returns `{"message": "No profiles configured. Use orq auth setup to add one.", "profiles": []}`; `--profile` precedence was not exercised. |
| `orq workspace list` | List workspaces for the active identity |
| `orq workspace use <key>` | Switch the active workspace (persisted in the session) |
| `orq projects use [project]` | Switch the active project; with no argument on a terminal a picker, otherwise prints the active one |
| `orq doctor` | Config, auth, permissions, agent-wiring, and reachability diagnostics |
| `orq doctor --fix` | Chmod loose credential paths (0600 files, 0700 dirs); exits 1 if a repair fails |
| `orq doctor --report` | Pre-filled GitHub issue URL for a bug report |
| `orq version` | CLI version, orq API version, and install method (`-o json`) |
| `orq update` | Replace the binary via its original install method (`--check` to look only) |
| `orq setup` | Authenticate, mint a gateway key, and wire detected coding agents |
| `orq connect [agent…] [capability…]` | Wire agents. `--status` and `--dry-run` change nothing |
| `orq disconnect` | Remove what `connect` wrote |
| `orq launch <agent>` | Run one coding agent through the AI Router; propagates the agent's exit status. `--help` lists `claude`, `codex`, `copilot`, `gemini`, `kilo`, `kimi`, `opencode`, `pi` |
| `orq orqi` | Run orqi, the orq.ai assistant, installing it on first use *(read from `--help`, not run)* |
| `orq request <method> <path>` | Raw API call using configured auth and server. `DELETE` needs `--force` off a TTY |
| `orq server list \| current \| set \| use \| clear` | Inspect or persist server defaults |
| `orq default-format [json\|yaml\|toon\|table]` | Show or persist the default output format. Verified on 8.6.9: `orq default-format json` prints `output_format: json, persisted: true` and a later piped `orq projects list` came back as JSON without `-o`; `orq default-format table` restores it. It takes at most one argument |
| `orq completion bash\|zsh\|fish\|powershell` | Shell completions |
| `orq help-input` | Request-body syntax reference |
| `orq help-config` | Configuration reference |

### `auth whoami -o json` shape

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21,** against a live session,
> exit 0.

```json
{
  "authenticated": true,
  "active_workspace_key": "orq-research",
  "credential": {
    "scope": "all_projects",
    "source": "session",
    "workspace_id": "624ccbbd-a482-40e2-b3d9-3621e09da1f8"
  },
  "session_file": "/Users/you/.orq/sessions/my.orq.ai.json",
  "urls": {
    "api_base_url": "https://my.orq.ai",
    "auth_base_url": "https://my.orq.ai/v2/auth",
    "profile_base_url": "https://my.orq.ai/v3/rpc/identity/orq.identity.v1.ProfileService/GetProfile",
    "v1_base_url": "https://my.orq.ai/v2/api"
  },
  "user": { "id": "50e3d5b5-...", "email": "you@orq.ai", "display_name": "You" },
  "workspaces": [
    { "id": "624ccbbd-...", "key": "orq-research", "name": "orq-research", "total_members": 19 }
  ]
}
```

`credential` is new since 5.1.0. `credential.source` is `"session"` when no key
is set and `"ORQ_API_KEY"` when one is, which is the quickest way to see what
authenticates your calls. `scope` was `all_projects` in both cases.

Workspace **ids are UUIDs**, not ULIDs — unlike agent and span ids. `name` often
equals `key`. `display_name` may just be the email.

`workspace list -o json` has a **different envelope**: `active_workspace_key` at
the top level, and `workspaces[]` entries carrying an extra `active` boolean.

```json
{
  "active_workspace_key": "orq-research",
  "workspaces": [
    { "active": true,  "id": "624ccbbd-…", "key": "orq-research", "name": "orq-research", "total_members": 20 },
    { "active": false, "id": "11bd7929-…", "key": "port-of-rotterdam", "name": "Port of Rotterdam", "total_members": 1 }
  ]
}
```

Neither command returns a `data[]` envelope — project `workspaces[]`.

The underlying session file `~/.orq/sessions/<host>.json` uses camelCase for
the same data: `activeWorkspaceKey`, `apiBaseUrl`, `v1BaseUrl`, `authBaseUrl`,
`profileBaseUrl`, `workspaces`, `refreshToken`, `bootstrapToken`,
`workspaceTokens`. It also holds live tokens — read it only as a fallback, and
never print it.

### `doctor -o json` shape

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** Observed with only `ORQ_API_KEY` set, under an empty `HOME`.

`doctor` runs without credentials and always exits 0, so it is safe to run first
— and its exit code tells you nothing. Two limits to know before trusting it:

- **`auth.status` says what is configured, not what works.** With `ORQ_API_KEY`
  exported it reads `authenticated` with `source: env:ORQ_API_KEY`, an empty
  `user_email` and `workspace_count: 0` (5.1.0 ignored the key and reported no
  login). With a session file whose refresh token was dead it also read
  `authenticated`, while `orq auth whoami` failed. Run a resource command to
  prove a credential.
- **`orq server current` is the direct answer for the resolved server.** Doctor
  reports each URL's `source`, which is usually enough to explain a wrong host,
  but it does not print the resource-command server as such.

**`auth.status` no longer uses the old vocabulary.** 8.6.9 emitted
`authenticated` (key or session) and `missing` (neither, `source: none`); the
`ok` / `missing` / `invalid` set documented for 4.x is gone. Do not match on a
remembered literal — read the whole block.

Abridged real output (top-level keys are `auth`, `binary`, `checks`, `config`,
`output`, `runtime`):

```json
{
  "binary":  { "name": "orq", "version": "8.6.9", "api_version": "4.14.20" },
  "runtime": { "name": "go", "version": "go1.27.1", "platform": "darwin", "arch": "arm64" },
  "output":  { "default_format": "table", "supported_formats": ["json", "yaml", "toon", "table"] },
  "config": {
    "profile": "",
    "session_file": "/Users/you/.orq/sessions/my.orq.ai.json",
    "session_host": "my.orq.ai",
    "api_base_url":     { "value": "https://my.orq.ai",          "source": "default" },
    "auth_base_url":    { "value": "https://my.orq.ai/v2/auth",  "source": "derived" },
    "v1_base_url":      { "value": "https://my.orq.ai/v2/api",   "source": "derived" },
    "profile_base_url": { "value": "https://my.orq.ai/v3/rpc/identity/orq.identity.v1.ProfileService/GetProfile", "source": "derived" },
    "profile_transport": { "value": "rpc", "source": "default" }
  },
  "auth": {
    "status": "authenticated",
    "source": "env:ORQ_API_KEY",
    "user_email": "",
    "active_workspace_key": null,
    "workspace_count": 0
  },
  "checks": [
    { "id": "session_file", "status": "pass", "message": "No session file (authenticated with an API key)", "details": { "session_file": "…" } },
    { "id": "credential_permissions", "status": "pass", "message": "Credential paths are not accessible by other accounts", "details": { "checked": 1 } },
    { "id": "api_base_url",  "status": "pass", "message": "Reachable", "details": { "http_status": 403, "url": "https://my.orq.ai" } },
    { "id": "auth_base_url", "status": "pass", "message": "Reachable", "details": { "http_status": 204, "url": "https://my.orq.ai/v2/auth" } }
  ]
}
```

Sessions are named for the **host** (`session_host`), not the profile, and
`profile` is empty when none is selected. `profile_base_url` is an RPC endpoint
now and `profile_transport` is new.

With a session and a wired machine the `checks` array had 10 entries, in this
order: `session_file`, `bootstrap_token` (`details.expires_at`),
`coding_agent_<name>` (one per detected agent), `coding_agents`, `skills`, `mcp`,
`credential_permissions`, `api_base_url`, `auth_base_url`, `profile_base_url`.
The three URL checks each carry `details.http_status` and were `pass` even for
`api_base_url` returning **403** and `auth_base_url` returning 204, so "reachable"
means a response came back, not a 2xx. `info` is not a failure: the two
coding-agent rows used it for "detected but not wired". Without a session the
array was shorter. `warn` was not observed on 8.6.9.

With a session, top-level `auth` is `{source: "session-file", status:
"authenticated", user_email, workspace_count, active_workspace_key}`, whether or
not `ORQ_API_KEY` is also set, so it does not say which credential the calls use.
Read `auth whoami` for that.

`credential_permissions` is the one to act on: it flags credential files that
are group- or world-readable, and `orq doctor --fix` applies the `chmod`. On
8.6.9 a clean run reports `pass` with `details.checked`; the flag-and-fix path
was not exercised.

Two things to read carefully:

- Config entries carry a `value` **and** a `source` (`flag`, `session`, `env`,
  `default`, `derived`). The `source` is what tells you why a command is talking
  to the wrong host.
- Reachability checks report `pass` on any HTTP answer (a 403 and a 204 were
  both `pass` on 8.6.9). They prove the host answered, not that the request
  would succeed.

Quick unauthenticated triage:

```sh
orq doctor -o json -j 'auth.status' --raw            # "authenticated" or "missing"; configured, not proven
orq doctor -o json -j 'auth'                         # status + source + workspace, all of it
orq doctor -o json -j "checks[?status!='pass']"      # real problems ('info' rows are not)
```

---

## Generated resource commands

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** The tree below was re-derived with the script at the top; only
the groups called out under it changed.

One group per API tag. Groups marked with a leading `→` are the ones worth
knowing by heart.

```
→ agents             create delete get-response invoke list retrieve run
                     stream stream-run update
  agents-responses   create
  alerts             create delete get list list-trigger-events list-triggers
                     update
  annotation-queues  add-items clear create delete get get-item list
                     query-items remove-items update
  api-keys           create delete get list list-capabilities update
  budgets            create delete get list reset-consumption update
  chat               create
  chunking           parse
  completions        create
→ datasets           clear create create-datapoint delete delete-datapoint list
                     list-datapoints retrieve retrieve-datapoint update
                     update-datapoint
→ deployments        get-config invoke list stream
  embeddings         create
→ evals              all create delete get invoke list-versions update
  feedback           create delete evaluation evaluation-remove
  files              content delete get list update upload
  identities         create delete list retrieve update
  images             edit generate variation
  knowledge-bases    create create-chunks create-datasource delete delete-chunk
                     delete-chunks delete-datasource list list-chunks
                     list-chunks-paginated list-datasources retrieve
                     retrieve-chunk retrieve-datasource retrieve-file-url
                     retrieve-processing-status search toggle-chunk update
                     update-chunk update-datasource get-chunks-count
  logs               aggregate get get-context get-patterns list-facet-values
                     list-facets list-fields list-trace query search
  management-keys    create delete get list list-capabilities update
  mcp-gateways       create delete list list-tools retrieve update
  mcp-servers        create delete list retrieve sync test-tool update
  model-catalog      get list list-offerings
  memory-stores      create create-document create-memory delete delete-document
                     delete-memory list list-documents list-memories retrieve
                     retrieve-document retrieve-memory update update-document
                     update-memory
  models             list create delete disable enable import-litellm
                     list-litellm list-preview update validate
                     create-aws-bedrock create-openai-like create-vertex
                     azure-foundry-deployments
                     update-aws-bedrock update-openai-like validate-aws-bedrock
  moderations        create
  notifiers          create delete get list update
  ocr                ocr
  pii                detect redact restore
→ projects           create delete get list update use
→ prompts            create delete get-version list list-versions retrieve update
  reporting          query
  rerank             create
  responses          create get
  schedules          create delete list retrieve trigger update
→ skills             create delete get list update
  smart-routers      create delete get list update
  speech             create
  tools              create delete get-version list list-versions retrieve update
→ traces             aggregate create delete get get-span list-facet-values
                     list-facets list-fields list-spans query-oql search
                     thread
                     insights-service-* (15 subcommands, see below)
  transcriptions     create
  translations       create
  webhooks           count create delete generate-secret get list query update
  workspace          list use
  workspace-security add-ip-range create-domain delete-domain delete-ip-range
                     get-ip-allowlist list-domains update-ip-allowlist
                     verify-domain
  workspace-settings get update
```

New since 5.1.0: `models list-preview`, `projects use`, `traces thread` and the
`workspace` built-in now shown in the tree. The top-level `status`, `switch` and `orqi` are in the built-ins table.

There is no `experiments` group — experiments are MCP/evaluatorq-only.
`annotation-queues` is the CLI surface for the eval-corrections /
unified-annotation model (annotation review workflows); its subcommands were
listed from `--help` and not called on either version.

`traces insights-service-*` is a family of **15** subcommands on 8.6.9 (the
5.1.0 pass said 18: `…-create-insight`, `…-list-clusters`, `…-run-analysis`,
`…-get-run-artifacts`, and so on) wrapping the trace-insights service.
*[unverified: enumerated from `--help`, not called.]*

**Every group is now listed.** The previous pass excluded several as "not
covered by this skill", and two of the names it listed (`activities`, `people`)
do not exist as groups at all — do not filter them out when re-deriving the tree.

Note `orq evals all` (not `list`) is the evaluator listing command, and
`orq traces create` / `orq traces delete` add and remove **span annotations**,
not traces.

**`evals` details, probed 2026-09-24 on 10.3.1 (API 4.14.20):**

| Claim | Detail |
|-------|--------|
| `orq evals all` is the listing command | `list` does not exist. Pages with `has_more` + `--starting-after <last _id>`; `--limit` is 1 to 200 |
| `--project-id` is unusable | Every project id, including ones holding evaluators, returns `HTTP 404 {"code":5,"message":"Project not found"}`. Filter by `project_id` client side |
| `--search` matches the key only | Not the description |
| `evals get` has no `key` field | The key created with comes back as `display_name`; `evals all` returns it as `key` |
| `evals create` under-reports | The create response has `output_type: null` even for a boolean evaluator; `evals get <id>` shows the stored value |
| `models list` takes no `--limit` | Unprojected it returns hundreds of entries (709 on 2026-09-24). Project it: `-j "[?refId=='openai/gpt-4.1'].refId"` |
| `evals invoke` verdicts | Read `value`. `passed` is the guardrail's decision when there is one, so it reads `passed` on a `false` value otherwise |

---

## Request bodies

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.**

Four input paths, and they compose. Shorthand always applies on top of whatever
base body the other flags produced.

```sh
# 1. generated per-field flags (top-level scalar fields only)
orq traces search --from 2026-07-01T00:00:00Z --to 2026-07-31T00:00:00Z --limit 20

# 2. stdin
echo '{"from":"2026-07-01T00:00:00Z","to":"2026-07-31T00:00:00Z"}' | orq traces search
orq traces search --stdin < body.json      # --stdin *requires* piped input

# 3. a file on disk
orq traces search --from-file body.json

# 4. the spec's first generated example, WHERE ONE EXISTS
orq <group> <cmd> --example
```

`--example` is advertised on every body command but was not populated for all of
them on 5.1.0, where `orq traces search --example` failed with `no generated body
example is available for this command`. On 8.6.9 it works on `traces search` and
prints the required scalars only (`{"from": "2024-01-01T00:00:00Z", "to":
"2024-01-01T00:00:00Z"}`). Use it to confirm field names, not as a working query,
and do not build a workflow around it: coverage of other commands was not probed.
`traces search` no longer requires `from` and `to`; a bare call searches the last
7 days.

Nested objects, arrays of objects, and polymorphic unions are not exposed as
typed flags — pass those as a JSON string:

```sh
orq traces search --from ... --to ... \
  --filters '[{"field":"status","op":"eq","values":["error"]}]'
```

### Shorthand grammar

Extra positional arguments beyond a command's required ones are parsed as
shorthand and merged into the body:

| Form | Result |
|---|---|
| `field: value` | scalar, auto-coerced to bool/int/float |
| `field:~ true` | forced to the string `"true"` |
| `foo.bar{id: 1, count: 5}` | nested object |
| `key: 1, 2, 3` | scalar array |
| `key[]: 1, key[]: 2` | append to array |
| `key[2]: value` | set array index |
| `key: @file.json` | load file contents as the value |
| `key: @%file.bin` | load file as base64 |

Full grammar: `orq help-input`.

---

## JMESPath recipes

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** The two session-only recipes (`whoami`, `workspace list`)
were run against a live session and the shapes match the blocks above.

`-j` runs against the parsed response. `--raw` unwraps a single value for shell
capture.

```sh
# active workspace key, bare — needs an OAuth session, not an API key
orq auth whoami -o json -j active_workspace_key --raw

# workspace keys and names — also session-only
orq workspace list -o json -j 'workspaces[].{key: key, name: name}'

# agent id + display name (agents use _id; deployments use id, projects project_id)
# A bare `agents list` returns every agent on 8.6.9 (142 in 0.8s); --limit is optional
# there, unlike the other lists. On 5.1.0 the bare call hung and 503'd.
orq agents list -o json --limit 200 -j 'data[].{id: _id, name: display_name}'

# first agent's key, bare
orq agents list -o json --limit 1 -j 'data[0].key' --raw

# models have no envelope — project the array directly
orq models list -o json -j '[].id'

# OQL results nest under `search`, so the usual data[] projections miss
orq traces query-oql -o json --from ... --to ... \
  --oql 'fetch traces | filter status in ("error") | sort end_time desc | limit 20' \
  -j 'search.data[].trace_id'
```

**A projection whose key is absent does not always yield `null`.** On some
commands it aborts: `orq logs query … -j '[length(data),has_more]'` gives
`Error: formatting failed: Invalid type for: <nil>, expected:
[]jmespath.jpType{"string", "array", "object"}` (5.1.0 printed it as a `FATAL
logs_commands.go:637` line). Check the envelope with
`-j 'keys(@)'` before projecting into an unfamiliar response.

`-j` works on `traces search` too (remember `--query` there is the body's
full-text field, not a projection). `jq` remains handy for multi-step
transforms:

```sh
# failed traces in a window
orq traces search -o json \
  --from 2026-07-30T00:00:00Z --to 2026-07-31T00:00:00Z --limit 50 \
  --filters '[{"field":"status","op":"eq","values":["error"]}]' \
  | jq '.data[] | {trace: .trace_id, name, status, ms: .duration_ms, cost}'

# pagination cursor
orq traces search -o json --from ... --to ... | jq -r '.next_page_token'
```

Span-level reads go through `traces list-spans` / `traces get-span` (see
[Per-trace drill-down](#per-trace-drill-down)).

### Comparing against a string in a filter

A bare backtick literal does **not** work. `-j 'checks[?status!=`pass`]'` fails
with `invalid character 'p' looking for beginning of value`, because backticks
delimit a *JSON* literal and `pass` is not valid JSON. Two forms that do work,
both verified live:

```sh
orq doctor -o json -j "checks[?status!='pass']"      # raw-string literal, outer double quotes
orq doctor -o json -j 'checks[?status!=`"pass"`]'    # JSON literal, note the inner quotes
```

Prefer the first. The second needs backticks to survive the shell, which they do
inside single quotes in `sh`/`bash`/`zsh` but not everywhere.

Most list endpoints return `{ "object": "list", "data": [...], "has_more": bool }`.
Verified on 8.6.9 for `projects` (`["object","data","has_more"]`) and, by
`length(data)` / `has_more` projections, `deployments`, `prompts`, `datasets`,
`knowledge-bases` and `agents`; `skills` was not re-run. Two exceptions:

- **`models list` returns a bare JSON array**, with no envelope. A `data[]`
  projection yields `null`; project with `[]` instead.
- **`traces search`** adds `meta` and `next_page_token` alongside `data`.

Field names, confirmed against **live responses** unless marked:

- **trace summaries** (`traces search`): `trace_id`, `id`, `span_id`,
  `root_span_id`, `leading_span_id`, `parent_id`, `name`, `operation`, `status`,
  `started_at`, `ended_at`, `start_time`, `end_time`, `duration`, `duration_ms`,
  `project_id`, `identity_id`, `session_id`, `thread_id`, `product`, `providers`,
  `models`, `agent`, `usage`, `cost`, `attributes`, `context`, `object`, `type`.
  Note `id` here is the **root span** id, not the trace id; use `trace_id` for
  the trace.
- **agents** (`agents list`, the union across all 142 on 8.6.9): `_id`, `key`,
  `display_name`, `description`, `role`, `instructions`, `system_prompt`,
  `status`, `version`, `version_hash`, `path`, `type`, `engine`, `source`,
  `model`, `settings`, `variables`, `metrics`, `skills`, `knowledge_bases`,
  `memory_stores`, `team_of_agents`, `project_id`, `workspace_id`,
  `created`, `updated`, `created_by_id`, `updated_by_id`. Not every agent carries
  every key (`display_name` was on 128 of 142), so a projection can yield `null`
  for an agent that simply lacks it.
  There is **no top-level `tools`** field — tools live at `settings.tools` (on 114
  of 142). And `model` is an **object** (`{"id": "google-ai/gemini-2.5-flash"}`),
  not a string.
- **deployments** (`deployments list`): `id`, `key`, `description`, `version`,
  `prompt_config`, `created`, `updated`. Note the identifier is **`id`** — there is no `_id` here, and
  projecting `_id` gives a column of `null` at exit 0.
- **projects** (`projects list`): `project_id`, `workspace_id`, `created_at`,
  `updated_at`, `created_by_id`, `updated_by_id`. Two departures from every other
  resource: the identifier is **`project_id`** (neither `id` nor `_id`), and the
  timestamps are **`created_at` / `updated_at`**, not `created` / `updated`.
- **span summaries** (`traces list-spans`, envelope `object`, `data`, `has_more`,
  `next_page_token`): `trace_id`, `span_id`, `parent_span_id`, `name`, `type`,
  `operation`, `status`, `started_at`, `ended_at`, `duration_ms`, `provider`,
  `model`, `usage`, `cost`, `has_detail`, plus `guardrail_enabled`,
  `leading_span_type`, `level`, `has_children` and `trace_framework`. See
  [Per-trace drill-down](#per-trace-drill-down) for the 404 fallback on pre-4.13
  deployments.

There is no universal identifier convention: `_id` for agents, prompts, datasets
and knowledge-bases; `id` for deployments; `project_id` for projects. Check the
resource before projecting.

---

## Trace querying

`traces search` takes a structured filter contract; `traces query-oql` takes an
OQL string. `query-oql` requires `from` and `to`; `traces search` does not (both are optional there).

```sh
orq traces query-oql -o json \
  --from 2026-07-01T00:00:00Z --to 2026-07-31T00:00:00Z --limit 100 \
  --oql '<oql expression>'
```

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.**

Do not guess filter fields or operators. Ask the API (168 fields on 8.6.9, each
with `operators`, `sortable` and `aliases`):

```sh
orq traces list-fields -o json                    # supported static trace fields
orq traces list-facets -o json                    # facetable fields
orq traces list-facet-values <field> -o json \
  --from 2026-07-01T00:00:00Z --to 2026-07-31T00:00:00Z   # values + counts for one facet
```

### Per-trace drill-down

`traces search` is the bulk read; the per-trace reads are:

```
orq traces get <trace_id>
orq traces list-spans <trace_id>
orq traces get-span <trace_id> <span>
orq traces thread <trace_id> [<span>]
```

`thread` (7.4.0+) is the one to reach for when the question is *what was said*:
it picks the conversational span itself and normalizes Chat Completions, OpenAI
Responses and OpenTelemetry GenAI payloads into one message list. Its `-o` is
its own — `xml` (default), `markdown`, `json`, `yaml`, `toon` — and it neither
reads `ORQ_OUTPUT_FORMAT` nor accepts `table`. See the `traces thread` section
of SKILL.md for span selection and the full flag set. `get-span` remains the
path for span *config* — temperature, tool definitions, `finish_reasons`.

These endpoints are served from the 4.13 platform onward *(5.1.0-era claim, not re-probed; `traces list-spans` answered on 8.6.9)*. If every per-trace
read returns HTTP 404 while `traces search` works, the deployment behind your
server is older than 4.13 — fall back to the search response itself, which
already carries `attributes`, `usage`, `cost`, `status`, and timing per row.
Quick probe: `orq traces get <trace_id> -o json` with an id taken from a
`traces search` response seconds earlier.

### Aggregation

`traces aggregate` handles per-window trace aggregation. There is no
`telemetry` group — for cross-trace analysis use `orq reporting query`
(start from `orq reporting query --help`).

---

## Building app URLs

> **Probed against 5.1.0; not re-probed.** The URL shapes are Studio routes, not
> CLI behaviour, and the `whoami` line needs a live session.

App URLs are workspace-scoped by **key** (the slug), not by id:

```
https://my.orq.ai/<workspace-key>/traces?query=trace_id:is:<trace-id>
https://my.orq.ai/<workspace-key>/experiments/<experiment-id>
```

```sh
key="$(orq auth whoami -o json -j active_workspace_key --raw)"
echo "https://my.orq.ai/${key}/traces?query=$(printf 'trace_id:is:%s' "$trace_id" | jq -sRr @uri)"
```

### Which host am I actually talking to?

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.** The `server current` and `server list` shapes were re-run; the
before/after-login table and the profile-binds-a-host claim need a live session
and are from 5.1.0.

**Do not memorise a host. Ask.** There is now **one** setting — `--server` /
`ORQ_SERVER` — and it moves when you log in:

```sh
orq server current -o json                        # the resolved host
orq doctor -o json -j 'config'                    # every URL, each with its source
```

`config.api_base_url` in doctor's output is a *response field name* and keeps
that spelling; it is not the deprecated `--api-base-url` flag. Reading it is
fine. Setting a host through anything but `--server` / `ORQ_SERVER` is not.

`orq server current` returns `server`, `server_index`, `server_override`, and —
new in 5.0.0 — `profile_server` and `server_default`. `orq server list` dropped
its per-entry `override` field for a top-level `overridden` boolean, so anything
reading the old shape needs updating. Verified on 8.6.9 with no override set
(5.1.0, after a login, showed `server_override` and `selected_server` at
`https://api.orq.ai`):

```json
// orq server current -o json
{"profile_server": "", "server": "https://my.orq.ai", "server_default": "",
 "server_index": 0, "server_override": ""}

// orq server list -o json
{"overridden": false, "selected_server": "https://my.orq.ai",
 "servers": [{"description": "", "index": 0, "selected": true, "url": "https://my.orq.ai"}]}
```

Read `server_override` first: **`orq auth login` persists an override to the host
it authenticated against**, so the resource host is not fixed. A profile also
carries its own host now, and that binding outranks a globally persisted
`orq server set`. Observed on the same machine:

| State | `server` | `server_override` |
|---|---|---|
| Before login (API key only) | `https://my.orq.ai` | `""` (generated default) |
| After `orq auth login` *(5.1.0)* | `https://api.orq.ai` | `https://api.orq.ai` |

That is also how self-hosted works: authenticate against a customer host once and
every later command on that profile follows, with no `--server` needed.

`https://my.orq.ai` is additionally the **Studio/browser** host — the one that
belongs in the app URLs above — regardless of what the CLI is pointed at.

**Override both with `--server` / `ORQ_SERVER` — there is one host name now.**
Until 5.0.0 the resource host and the auth host were separate values under
separate names, so a bogus `ORQ_API_BASE_URL` left `projects list` working while
a bogus `ORQ_SERVER` broke it. `--api-base-url` and `ORQ_API_BASE_URL` are now
deprecated spellings of the same value, hidden from help and warning on stderr
(`--api-base-url` on `doctor` warned on 8.6.9; `ORQ_API_BASE_URL` was not run).
`orq server set` / `use` / `clear` persist or drop the override.

**Do not shell out to `orq` from library code to get the slug.** evaluatorq tried
that and removed it — the subprocess blocks and can 404. Prefer, in order: a URL
the run already has (an `experiment_url` of the form
`{host}/{workspace}/experiments/{id}` carries both host and slug), then an env
var (`ORQ_WORKSPACE` / `ORQ_WORKSPACE_SLUG`), and only then the CLI. The CLI is
the right source for a human at a terminal or a one-shot script, not for a
request path.

---

## Escape hatch

> **Probed against 8.6.9 (API 4.14.20), 2026-09-21.**

When an endpoint has no generated command, or a newer API surfaced after the
installed CLI was built:

```sh
orq request GET /v2/traces/fields -o json </dev/null
orq request POST /v2/traces/search -o json < body.json
```

It reuses the configured profile, auth, and server, so it respects `--profile`
and `--server` like everything else.

**It does not return the bare response body.** `orq request` wraps everything:

```sh
orq request GET /v2/traces/fields -o json -j 'keys(@)' </dev/null
# ["ok", "status", "headers", "body_text", "body"]     (8.6.9; 5.1.0 had no body_text)
```

So a projection carried over from a generated command has to be re-rooted:
`data[]` becomes `body.data[]`. Forgetting this returns `null` at exit 0, which
reads as "empty result" rather than "wrong path".

Redirect stdin from `/dev/null` on GETs. `orq request` could block waiting on an
open stdin when no body was piped *(5.1.0; every 8.6.9 call here redirected
stdin, so the hang was not re-tested)*. `orq request DELETE` without `--force`
off a terminal is refused, as for the generated delete commands.
