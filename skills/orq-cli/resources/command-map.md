# orq CLI Command Map

Verified live against **orq CLI 5.1.0** (built against orq API 4.14.3), last
full pass 2026-08-31. The CLI is
generated from the orq.ai OpenAPI spec, so this drifts between releases —
`orq <group> --help` wins over anything written here.

Since 5.0.0 the CLI's version is its own and no longer tracks the API's; run
`orq version --json` to read both. The command surface is tracked upstream in
`surface.json`, so it cannot change silently, but it can change deliberately.

To re-derive the tree after a CLI upgrade, instead of editing it by hand. Run it
with `bash` explicitly — under `zsh` (the macOS default) unquoted parameters are
**not** word-split, so `for g in $groups` iterates once with the whole list as a
single value and the script silently produces nothing useful:

```bash
#!/usr/bin/env bash
groups=$(orq --help 2>&1 | awk '/Available Commands:/,/^Flags:/' | grep '^  [a-z]' | awk '{print $1}')
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

| Flag | Effect |
|---|---|
| `--json` | Alias for `-o json` |
| `-o, --output-format` | `json`, `yaml`, or `toon` (default `toon`) |
| `-j, --jmespath` | JMESPath expression applied to the response |
| `--raw` | Emit the `--jmespath` result unquoted instead of as JSON |
| `--profile` | Credential profile (default `default`). An explicit one outranks `ORQ_API_KEY` |
| `--server` | Override the server URL for this call. Replaces the deprecated `--api-base-url` |
| `--workspace` | Workspace key for this invocation, overriding the session's active one. Does not persist |
| `--no-color` | Disable colour (`NO_COLOR` is honoured too) |
| `--no-input` | Never prompt; fail instead of asking |
| `--verbose` | Verbose log output. **Prints every stored API key in plaintext on stderr — never use in shared output.** |

Each flag has an env-var twin: uppercase, `ORQ_` prefix, underscores for dashes.
`ORQ_VERBOSE=1` equals `--verbose`, `ORQ_PROFILE=ci` equals `--profile ci`,
`ORQ_JMESPATH='auth.status'` equals `-j 'auth.status'`,
`ORQ_WORKSPACE=orq-research` equals `--workspace orq-research`.

Note the asymmetry: an explicitly typed `--profile` beats an exported
`ORQ_API_KEY`, but `ORQ_PROFILE` does not — env against env has no statement of
intent to break the tie. `--workspace` loses to an API key either way, with a
warning on stderr.

Two deprecated spellings still resolve and warn: `--api-base-url` (six built-in
commands only) and `ORQ_API_BASE_URL`. Both mean `--server` / `ORQ_SERVER`; both
are scheduled for removal.

**There is no `-q`/`--query` global flag.** Muscle memory from other CLIs (or
older orq builds) will produce `Error: unknown shorthand flag: 'q' in -q` on
every command — the projection flag is `-j`.

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

Two related parsing facts, verified live:

- **Plain strings are accepted by generated string flags** (`--model`, `--input`,
  …). Only nested objects, arrays of objects, and unions need a JSON string.
- **There is no client-side enum validation.** Any value — including an invalid
  one — passes flag parsing and is sent to the server, which rejects it there.
  `-o bogus` likewise silently falls back to JSON output. Do not rely on the CLI
  to catch a typo'd enum value; read the server error.

Config files are read from `~/.orq/config.json` (and `/etc/orq/config.json` on
Unix), using the same key names:

```json
{ "output-format": "json", "verbose": true }
```

`.env` and `.env.local` in the working directory load automatically.

### Environment variables

| Variable | Purpose |
|---|---|
| `ORQ_API_KEY` | API key for headless / CI auth. **Resource commands only** — not `whoami` / `workspace` |
| `ORQ_PROFILE` | Default profile |
| `ORQ_SERVER` | Base URL for **every** command, built-in and generated. The one host name |
| `ORQ_WORKSPACE` | Workspace key for the invocation. Overrides the session's active workspace, persists nothing |
| `ORQ_API_BASE_URL` | **Deprecated.** Now just a spelling of `ORQ_SERVER`; warns on stderr, will be removed |
| `ORQ_V1_BASE_URL` | Override the v1 base URL (local dev) |
| `ORQ_PROFILE_BASE_URL` | Override the profile endpoint (local dev) |
| `ORQ_NO_UPDATE_CHECK` | Suppress the daily update notice (also skipped when `CI` is set) |
| `ORQ_CLI_VERSION` | Version pin for `install.sh` |
| `ORQ_CLI_CHANNEL` | `rc` installs the pre-release line (`install.sh --channel rc`) |
| `ORQ_CLI_INSTALL_DIR` | Install directory for `install.sh` |

`ORQ_SERVER` and `ORQ_API_BASE_URL` **used to be two different hosts.** Until
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

These are hand-written, not generated from the spec. That distinction is not
cosmetic: **the built-ins accept only an OAuth session, never `ORQ_API_KEY`.**
`orq auth whoami` and `orq workspace list` both fail with `Error: you are not
logged in` when a valid key is exported, and they exit **0** while doing it.

| Command | Purpose |
|---|---|
| `orq auth login` | OAuth device login (interactive, needs a browser) |
| `orq auth logout` | Revoke the refresh token, clear the local session |
| `orq auth whoami` | Current identity, workspaces, resolved URLs (alias: `orq whoami`) |
| `orq auth setup` | Interactive auth configuration |
| `orq auth add-profile apikey <name> --api-key-file <f>` | Save an API-key profile (`-` reads stdin; a positional key is visible via `ps`) |
| `orq auth list-profiles` | List credential profiles. Keys are **masked** since 5.0.0, and it honours `--json` / `-o` |
| `orq workspace list` | List workspaces for the active identity |
| `orq workspace use <key>` | Switch the active workspace (persisted in the session) |
| `orq doctor` | Config, auth, permissions, agent-wiring, and reachability diagnostics |
| `orq doctor --fix` | Chmod loose credential paths (0600 files, 0700 dirs); exits 1 if a repair fails |
| `orq doctor --report` | Pre-filled GitHub issue URL for a bug report |
| `orq version` | CLI version, orq API version, and install method (`--json`) |
| `orq update` | Replace the binary via its original install method (`--check` to look only) |
| `orq setup` | Authenticate, mint a gateway key, and wire detected coding agents |
| `orq connect [agent…] [capability…]` | Wire agents. `--status` and `--dry-run` change nothing |
| `orq disconnect` | Remove what `connect` wrote |
| `orq launch <agent>` | Run one coding agent through the AI Router; propagates the agent's exit status |
| `orq request <method> <path>` | Raw API call using configured auth and server. `DELETE` needs `--force` off a TTY |
| `orq server list \| current \| set \| use \| clear` | Inspect or persist server defaults |
| `orq default-format <json\|yaml\|toon>` | Persist a default output format |
| `orq completion bash\|zsh\|fish\|powershell` | Shell completions |
| `orq help-input` | Request-body syntax reference |
| `orq help-config` | Configuration reference |

### `auth whoami --json` shape

Observed against a live session:

```json
{
  "authenticated": true,
  "session_file": "/Users/you/.orq/sessions/default.json",
  "user": {
    "id": "50e3d5b5-9cbe-4c48-9270-e564fcaf2b8d",
    "email": "you@orq.ai",
    "display_name": "you@orq.ai"
  },
  "active_workspace_key": "orq-research",
  "workspaces": [
    {
      "id": "624ccbbd-a482-40e2-b3d9-3621e09da1f8",
      "key": "orq-research",
      "name": "orq-research",
      "total_members": 20
    }
  ],
  "urls": {
    "api_base_url": "https://api.orq.ai",
    "auth_base_url": "https://api.orq.ai/v2/auth",
    "profile_base_url": "https://api.orq.ai/v2/api/me",
    "v1_base_url": "https://api.orq.ai/v2/api"
  }
}
```

Workspace **ids are UUIDs**, not ULIDs — unlike agent and span ids. `name` often
equals `key`. `display_name` may just be the email.

`workspace list --json` has a **different envelope**: `active_workspace_key` at
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

The underlying session file `~/.orq/sessions/<profile>.json` uses camelCase for
the same data: `activeWorkspaceKey`, `apiBaseUrl`, `v1BaseUrl`, `authBaseUrl`,
`profileBaseUrl`, `workspaces`, `refreshToken`, `bootstrapToken`,
`workspaceTokens`. It also holds live tokens — read it only as a fallback, and
never print it.

### `doctor --json` shape

`doctor` runs without credentials and always exits 0, so it is safe to run first
— and its exit code tells you nothing. Two limits to know before trusting it:

- **It ignores `ORQ_API_KEY`.** With a valid key exported and resource commands
  returning real data, `doctor`'s auth block still reports no login. It only
  understands OAuth sessions.
- **`orq server current` is the direct answer for the resolved server.** Doctor
  reports each URL's `source`, which is usually enough to explain a wrong host,
  but it does not print the resource-command server as such.

**`auth.status` no longer uses the old vocabulary.** It reads `authenticated`
with a session; the `ok` / `missing` / `invalid` set documented for 4.x is gone.
Do not match on a remembered literal — read the whole block.

Abridged real output from a logged-out session (field names verified on 5.1.0;
`binary` now carries `api_version` alongside `version`):

```json
{
  "binary":  { "name": "orq", "version": "5.1.0", "api_version": "4.14.3" },
  "runtime": { "name": "go", "version": "go1.27.0", "platform": "darwin", "arch": "arm64" },
  "output":  { "default_format": "toon", "supported_formats": ["json", "yaml", "toon"] },
  "config": {
    "profile": "default",
    "session_file": "/Users/you/.orq/sessions/default.json",
    "api_base_url":     { "value": "https://api.orq.ai",             "source": "default" },
    "auth_base_url":    { "value": "https://api.orq.ai/v2/auth",     "source": "derived" },
    "v1_base_url":      { "value": "https://api.orq.ai/v2/api",      "source": "derived" },
    "profile_base_url": { "value": "https://api.orq.ai/v2/api/me",   "source": "derived" }
  },
  "auth": {
    "status": "missing",
    "source": "none",
    "user_email": "",
    "active_workspace_key": null,
    "workspace_count": 0
  },
  "checks": [
    { "id": "session_file",     "status": "warn", "message": "No local session file found" },
    { "id": "api_base_url",     "status": "pass", "message": "Reachable (HTTP 404)" },
    { "id": "profile_base_url", "status": "pass", "message": "Reachable (HTTP 401)" }
  ]
}
```

The `checks` array grew a lot in 5.x. Observed ids on a healthy 5.1.0 machine,
in order: `session_file`, `bootstrap_token`, `coding_agent_<name>` (one per
detected agent), `coding_agents`, `skills`, `mcp`, `gateway_key_expiry`,
`credential_permissions`, `api_base_url`, `auth_base_url`, `profile_base_url`.
Check `status` values are `pass`, `warn`, and `info` — **`info` is not a
failure**, and the coding-agent rows use it for "detected but not wired".

`credential_permissions` is the one to act on: it flags credential files that
are group- or world-readable, names the `chmod` that fixes each, and `orq doctor
--fix` applies them. A clean run says nothing at all, so silence there is good.

Two things to read carefully:

- Config entries carry a `value` **and** a `source` (`flag`, `session`, `env`,
  `default`, `derived`). The `source` is what tells you why a command is talking
  to the wrong host.
- Reachability checks report `pass` on HTTP 404 and 401. They prove the host
  answered, not that the request would succeed. `auth.status` is the field that
  says whether you are logged in.

Quick unauthenticated triage:

```sh
orq doctor --json -j 'auth.status' --raw            # "authenticated" with a session
orq doctor --json -j 'auth'                         # status + source + workspace, all of it
orq doctor --json -j "checks[?status=='warn']"      # real problems ('info' rows are not)
```

---

## Generated resource commands

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
                     list-litellm update validate
                     create-aws-bedrock create-openai-like create-vertex
                     azure-foundry-deployments
                     update-aws-bedrock update-openai-like validate-aws-bedrock
  moderations        create
  notifiers          create delete get list update
  ocr                ocr
  pii                detect redact restore
→ projects           create delete get list update
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
                     insights-service-* (18 subcommands, see below)
  transcriptions     create
  translations       create
  webhooks           count create delete generate-secret get list query update
  workspace-security add-ip-range create-domain delete-domain delete-ip-range
                     get-ip-allowlist list-domains update-ip-allowlist
                     verify-domain
  workspace-settings get update
```

There is no `experiments` group — experiments are MCP/evaluatorq-only.
`annotation-queues` is the CLI surface for the eval-corrections /
unified-annotation model (annotation review workflows); its subcommands parse
correctly but have not yet been exercised against production data.

`traces insights-service-*` is a family of 18 subcommands (`…-create-insight`,
`…-list-clusters`, `…-run-analysis`, `…-get-run-artifacts`, and so on) wrapping
the trace-insights service. *[unverified: enumerated from `--help`, not called.]*

**Every group is now listed.** The previous pass excluded several as "not
covered by this skill", and two of the names it listed (`activities`, `people`)
do not exist as groups at all — do not filter them out when re-deriving the tree.

Note `orq evals all` (not `list`) is the evaluator listing command, and
`orq traces create` / `orq traces delete` add and remove **span annotations**,
not traces.

---

## Request bodies

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

`--example` is advertised on every body command but is not populated for all of
them. `orq traces search --example` fails with `no generated body example is
available for this command`. Do not build a workflow around it.

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

`-j` runs against the parsed response. `--raw` unwraps a single value for shell
capture.

```sh
# active workspace key, bare — needs an OAuth session, not an API key
orq auth whoami --json -j active_workspace_key --raw

# workspace keys and names — also session-only
orq workspace list --json -j 'workspaces[].{key: key, name: name}'

# agent id + display name (agents use _id; deployments use id, projects project_id)
# --limit is REQUIRED here: a bare `agents list` blocks for minutes, then 503s.
# (A single `agents retrieve <valid-key>` did the same, so the 503 is upstream,
#  not a pagination fault — see SKILL.md "Lists truncate silently".)
orq agents list --json --limit 200 -j 'data[].{id: _id, name: display_name}'

# first agent's key, bare
orq agents list --json --limit 1 -j 'data[0].key' --raw

# models have no envelope — project the array directly
orq models list --json -j '[].id'

# OQL results nest under `search`, so the usual data[] projections miss
orq traces query-oql --json --from ... --to ... \
  --oql 'fetch traces | filter status in ("error") | sort end_time desc | limit 20' \
  -j 'search.data[].trace_id'
```

**A projection whose key is absent does not always yield `null`.** On some
commands it aborts: `orq logs query … -j '[length(data),has_more]'` gives
`FATAL logs_commands.go:637 formatting failed Invalid type for: <nil>, expected:
[]jmespath.jpType{"string","array","object"}`. Check the envelope with
`-j 'keys(@)'` before projecting into an unfamiliar response.

`-j` works on `traces search` too (remember `--query` there is the body's
full-text field, not a projection). `jq` remains handy for multi-step
transforms:

```sh
# failed traces in a window
orq traces search --json \
  --from 2026-07-30T00:00:00Z --to 2026-07-31T00:00:00Z --limit 50 \
  --filters '[{"field":"status","op":"eq","values":["error"]}]' \
  | jq '.data[] | {trace: .trace_id, name, status, ms: .duration_ms, cost}'

# pagination cursor
orq traces search --json --from ... --to ... | jq -r '.next_page_token'
```

Span-level reads go through `traces list-spans` / `traces get-span` (see
[Per-trace drill-down](#per-trace-drill-down)).

### Comparing against a string in a filter

A bare backtick literal does **not** work. `-j 'checks[?status!=`pass`]'` fails
with `invalid character 'p' looking for beginning of value`, because backticks
delimit a *JSON* literal and `pass` is not valid JSON. Two forms that do work,
both verified live:

```sh
orq doctor --json -j "checks[?status!='pass']"      # raw-string literal, outer double quotes
orq doctor --json -j 'checks[?status!=`"pass"`]'    # JSON literal, note the inner quotes
```

Prefer the first. The second needs backticks to survive the shell, which they do
inside single quotes in `sh`/`bash`/`zsh` but not everywhere.

Most list endpoints return `{ "object": "list", "data": [...], "has_more": bool }`.
Verified on `deployments`, `prompts`, `skills`, `projects`, `datasets`, and
`knowledge-bases`. Two exceptions:

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
- **agents** (`agents list`): `_id`, `key`, `display_name`, `description`,
  `role`, `instructions`, `status`, `version`, `path`, `type`, `engine`, `model`,
  `settings`, `variables`, `metrics`, `skills`, `knowledge_bases`,
  `memory_stores`, `team_of_agents`, `project_id`, `workspace_id`,
  `created`, `updated`, `created_by_id`, `updated_by_id`.
  There is **no top-level `tools`** field — tools live at `settings.tools`. And
  `model` is an **object** (`{"id": "google-ai/gemini-2.5-flash"}`), not a string.
- **deployments** (`deployments list`): `id`, `created`, `updated`, plus the
  deployment body. Note the identifier is **`id`** — there is no `_id` here, and
  projecting `_id` gives a column of `null` at exit 0.
- **projects** (`projects list`): `project_id`, `workspace_id`, `created_at`,
  `updated_at`, `created_by_id`, `updated_by_id`. Two departures from every other
  resource: the identifier is **`project_id`** (neither `id` nor `_id`), and the
  timestamps are **`created_at` / `updated_at`**, not `created` / `updated`.
- **span summaries** (`traces list-spans`): `trace_id`, `span_id`,
  `parent_span_id`, `name`, `type`, `operation`, `status`, `started_at`,
  `ended_at`, `duration_ms`, `provider`, `model`, `usage`, `cost`, `has_detail`.
  Field names from the spec — see [Per-trace drill-down](#per-trace-drill-down)
  for the 404 fallback on pre-4.13 deployments.

There is no universal identifier convention: `_id` for agents, prompts, datasets
and knowledge-bases; `id` for deployments; `project_id` for projects. Check the
resource before projecting.

---

## Trace querying

`traces search` takes a structured filter contract; `traces query-oql` takes an
OQL string. Both require `from` and `to`.

```sh
orq traces query-oql --json \
  --from 2026-07-01T00:00:00Z --to 2026-07-31T00:00:00Z --limit 100 \
  --oql '<oql expression>'
```

Do not guess filter fields or operators. Ask the API:

```sh
orq traces list-fields --json                    # supported static trace fields
orq traces list-facets --json                    # facetable fields
orq traces list-facet-values <field> --json \
  --from 2026-07-01T00:00:00Z --to 2026-07-31T00:00:00Z   # values + counts for one facet
```

### Per-trace drill-down

`traces search` is the bulk read; the per-trace reads are:

```
orq traces get <trace_id>
orq traces list-spans <trace_id>
orq traces get-span <trace_id> <span>
```

These endpoints are served from the 4.13 platform onward. If every per-trace
read returns HTTP 404 while `traces search` works, the deployment behind your
server is older than 4.13 — fall back to the search response itself, which
already carries `attributes`, `usage`, `cost`, `status`, and timing per row.
Quick probe: `orq traces get <trace_id> --json` with an id taken from a
`traces search` response seconds earlier.

### Aggregation

`traces aggregate` handles per-window trace aggregation. There is no
`telemetry` group — for cross-trace analysis use `orq reporting query`
(start from `orq reporting query --help`).

---

## Building app URLs

App URLs are workspace-scoped by **key** (the slug), not by id:

```
https://my.orq.ai/<workspace-key>/traces?query=trace_id:is:<trace-id>
https://my.orq.ai/<workspace-key>/experiments/<experiment-id>
```

```sh
key="$(orq auth whoami --json -j active_workspace_key --raw)"
echo "https://my.orq.ai/${key}/traces?query=$(printf 'trace_id:is:%s' "$trace_id" | jq -sRr @uri)"
```

### Which host am I actually talking to?

**Do not memorise a host. Ask.** There is now **one** setting — `--server` /
`ORQ_SERVER` — and it moves when you log in:

```sh
orq server current --json                        # the resolved host
orq doctor --json -j 'config'                    # every URL, each with its source
```

`config.api_base_url` in doctor's output is a *response field name* and keeps
that spelling; it is not the deprecated `--api-base-url` flag. Reading it is
fine. Setting a host through anything but `--server` / `ORQ_SERVER` is not.

`orq server current` returns `server`, `server_index`, `server_override`, and —
new in 5.0.0 — `profile_server` and `server_default`. `orq server list` dropped
its per-entry `override` field for a top-level `overridden` boolean, so anything
reading the old shape needs updating. Verified on 5.1.0:

```json
// orq server current --json
{"profile_server": "", "server": "https://api.orq.ai", "server_default": "",
 "server_index": 0, "server_override": "https://api.orq.ai"}

// orq server list --json
{"overridden": true, "selected_server": "https://api.orq.ai",
 "servers": [{"description": "", "index": 0, "selected": false, "url": "https://my.orq.ai"}]}
```

Read `server_override` first: **`orq auth login` persists an override to the host
it authenticated against**, so the resource host is not fixed. A profile also
carries its own host now, and that binding outranks a globally persisted
`orq server set`. Observed on the same machine:

| State | `server` | `server_override` |
|---|---|---|
| Before login (API key only) | `https://my.orq.ai` | `""` (generated default) |
| After `orq auth login` | `https://api.orq.ai` | `https://api.orq.ai` |

That is also how self-hosted works: authenticate against a customer host once and
every later command on that profile follows, with no `--server` needed.

`https://my.orq.ai` is additionally the **Studio/browser** host — the one that
belongs in the app URLs above — regardless of what the CLI is pointed at.

**Override both with `--server` / `ORQ_SERVER` — there is one host name now.**
Until 5.0.0 the resource host and the auth host were separate values under
separate names, so a bogus `ORQ_API_BASE_URL` left `projects list` working while
a bogus `ORQ_SERVER` broke it. `--api-base-url` and `ORQ_API_BASE_URL` are now
deprecated spellings of the same value, hidden from help and warning on stderr.
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

When an endpoint has no generated command, or a newer API surfaced after the
installed CLI was built:

```sh
orq request GET /v2/traces/fields --json </dev/null
orq request POST /v2/traces/search --json < body.json
```

It reuses the configured profile, auth, and server, so it respects `--profile`
and `--server` like everything else.

**It does not return the bare response body.** `orq request` wraps everything:

```sh
orq request GET /v2/traces/fields --json -j 'keys(@)' </dev/null
# [ "body", "ok", "status", "headers" ]
```

So a projection carried over from a generated command has to be re-rooted:
`data[]` becomes `body.data[]`. Forgetting this returns `null` at exit 0, which
reads as "empty result" rather than "wrong path".

Redirect stdin from `/dev/null` on GETs. `orq request` can block waiting on an
open stdin when no body is piped.
