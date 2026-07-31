# orq CLI Command Map

Command tree captured from `orq` **v4.12.15**. The CLI is generated from the
orq.ai OpenAPI spec, so this drifts between releases — `orq <group> --help` wins
over anything written here.

To re-derive the tree after a CLI upgrade, instead of editing it by hand
(`bash`, or `zsh` with the `${=…}` splits shown):

```bash
groups=$(orq --help 2>&1 | awk '/Available Commands:/,/^Flags:/' | grep '^  [a-z]' | awk '{print $1}')
for g in $groups; do
  subs=$(orq "$g" --help 2>&1 | awk '/Available Commands:/,/^Flags:/' | grep '^  [a-z]' | awk '{print $1}' | tr '\n' ' ')
  [ -n "$subs" ] && echo "$g: $subs" || echo "$g: (leaf command)"
done
```

Response field names are not in the help text; they come from the spec the CLI
was generated from:

```sh
curl -fsSL https://raw.githubusercontent.com/orq-ai/orq-cli/main/openapi.yaml -o /tmp/orq-openapi.yaml
```

---

## Global flags

Every command accepts these:

| Flag | Effect |
|---|---|
| `--json` | Alias for `-o json` |
| `-o, --output-format` | `json`, `yaml`, or `toon` (default `toon`) |
| `-q, --query` | JMESPath expression applied to the response |
| `--raw` | Emit the query result unquoted instead of as JSON |
| `--profile` | Credential profile (default `default`) |
| `--server` | Override the server URL for this call |
| `--verbose` | Verbose log output |

Each flag has an env-var twin: uppercase, `ORQ_` prefix, underscores for dashes.
`ORQ_VERBOSE=1` equals `--verbose`, `ORQ_PROFILE=ci` equals `--profile ci`.

Config files are read from `~/.orq/config.json` (and `/etc/orq/config.json` on
Unix), using the same key names:

```json
{ "output-format": "json", "verbose": true }
```

`.env` and `.env.local` in the working directory load automatically.

### Environment variables

| Variable | Purpose |
|---|---|
| `ORQ_API_KEY` | API key for headless / CI auth |
| `ORQ_PROFILE` | Default profile |
| `ORQ_SERVER` | Override generated-command base URL |
| `ORQ_API_BASE_URL` | Override the auth endpoint (`auth login`, `whoami`, `workspace`) |
| `ORQ_V1_BASE_URL` | Override the v1 base URL (local dev) |
| `ORQ_PROFILE_BASE_URL` | Override the profile endpoint (local dev) |
| `ORQ_CLI_VERSION` | Version pin for `install.sh` |
| `ORQ_CLI_INSTALL_DIR` | Install directory for `install.sh` |

---

## Built-in commands

These are hand-written, not generated from the spec.

| Command | Purpose |
|---|---|
| `orq auth login` | OAuth device login (interactive, needs a browser) |
| `orq auth logout` | Revoke the refresh token, clear the local session |
| `orq auth whoami` | Current identity, workspaces, resolved URLs (alias: `orq whoami`) |
| `orq auth setup` | Interactive auth configuration |
| `orq auth add-profile apikey <name> <key>` | Save an API-key profile |
| `orq auth list-profiles` | List configured credential profiles |
| `orq workspace list` | List workspaces for the active identity |
| `orq workspace use <key>` | Switch the active workspace (persisted in the session) |
| `orq doctor` | Config, auth, and reachability diagnostics |
| `orq request <method> <path>` | Raw API call using configured auth and server |
| `orq server list \| current \| set \| use \| clear` | Inspect or persist server defaults |
| `orq default-format <json\|yaml\|toon>` | Persist a default output format |
| `orq completion bash\|zsh\|fish\|powershell` | Shell completions |
| `orq help-input` | Request-body syntax reference |
| `orq help-config` | Configuration reference |

### `auth whoami --json` shape

```json
{
  "authenticated": true,
  "session_file": "/Users/you/.orq/sessions/default.json",
  "user": { "id": "...", "email": "you@orq.ai", "display_name": "You" },
  "active_workspace_key": "orq-research",
  "workspaces": [
    { "id": "01J...", "key": "orq-research", "name": "Research", "total_members": 7 }
  ],
  "urls": { }
}
```

`workspace list --json` returns the same workspace objects plus an `active`
boolean.

The underlying session file `~/.orq/sessions/<profile>.json` uses camelCase for
the same data: `activeWorkspaceKey`, `apiBaseUrl`, `v1BaseUrl`, `authBaseUrl`,
`profileBaseUrl`, `workspaces`, `refreshToken`, `bootstrapToken`,
`workspaceTokens`. It also holds live tokens — read it only as a fallback, and
never print it.

### `doctor --json` shape

`doctor` runs without credentials and exits 0 even when unauthenticated, so it is
always safe to run first. Abridged real output from a logged-out v4.12.15:

```json
{
  "binary":  { "name": "orq", "version": "4.12.15" },
  "runtime": { "name": "go", "version": "go1.26.5", "platform": "darwin", "arch": "arm64" },
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

Two things to read carefully:

- Config entries carry a `value` **and** a `source` (`flag`, `session`, `env`,
  `default`, `derived`). The `source` is what tells you why a command is talking
  to the wrong host.
- Reachability checks report `pass` on HTTP 404 and 401. They prove the host
  answered, not that the request would succeed. `auth.status` is the field that
  says whether you are logged in.

Quick unauthenticated triage:

```sh
orq doctor --json -q 'auth.status' --raw            # missing | ok | invalid
orq doctor --json -q "checks[?status!='pass']"      # only the problems
```

---

## Generated resource commands

One group per API tag. Groups marked with a leading `→` are the ones worth
knowing by heart.

```
→ agents             create delete get-response invoke list refresh-agent-card
                     retrieve run stream stream-run update
  agents-responses   create
  api-keys           create delete get list list-capabilities update
  budgets            create delete get get-consumption list reset-consumption update
  chat               create
  chunking           parse
  completions        create
→ datasets           clear create create-datapoint delete delete-datapoint list
                     list-datapoints retrieve retrieve-datapoint update
                     update-datapoint
→ deployments        get-config invoke list stream
  embeddings         create
→ evals              all create delete invoke list-versions update
  feedback           create delete evaluation evaluation-remove
  files              content delete get list update upload
  identities         create delete list retrieve update
  images             edit generate variation
  knowledge-bases    create create-chunks create-datasource delete delete-chunk
                     delete-chunks delete-datasource list list-chunks
                     list-chunks-paginated list-datasources retrieve
                     retrieve-chunk retrieve-datasource search update
                     update-chunk update-datasource
  management-keys    create delete get list list-capabilities update
  memory-stores      create create-document create-memory delete delete-document
                     delete-memory list list-documents list-memories retrieve
                     retrieve-document retrieve-memory update update-document
                     update-memory
  models             list create delete disable enable import-litellm
                     list-litellm update validate create-autorouter
                     create-aws-bedrock create-openai-like create-vertex
                     azure-foundry-deployments update-autorouter
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
  speech             create
  telemetry          query
  tools              create delete get-version list list-versions retrieve update
→ traces             aggregate create delete get get-span list-facet-values
                     list-facets list-fields list-spans query-oql search
  transcriptions     create
  translations       create
  webhooks           count create delete generate-secret get list query update
```

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

# 4. the spec's first generated example, as a starting point
orq traces search --example
```

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

`-q` runs against the parsed response. `--raw` unwraps a single value for shell
capture.

```sh
# active workspace key, bare
orq auth whoami --json -q active_workspace_key --raw

# workspace keys and names
orq workspace list --json -q 'workspaces[].{key: key, name: name}'

# agent id + display name
orq agents list --json -q 'data[].{id: _id, name: display_name}'

# first agent's key, bare
orq agents list --json -q 'data[0].key' --raw

# failed traces in a window, newest field set only
orq traces search --json \
  --from 2026-07-30T00:00:00Z --to 2026-07-31T00:00:00Z --limit 50 \
  --filters '[{"field":"status","op":"eq","values":["error"]}]' \
  -q 'data[].{trace: trace_id, name: name, status: status, ms: duration_ms, cost: cost}'

# spans of one trace, slowest first is not built in — project then sort locally
orq traces list-spans <trace-id> --json \
  -q 'data[].{span: span_id, name: name, type: type, ms: duration_ms, status: status}'

# pagination cursor
orq traces search --json --from ... --to ... -q 'next_page_token' --raw
```

### Comparing against a string in a filter

A bare backtick literal does **not** work. `-q 'checks[?status!=`pass`]'` fails
with `invalid character 'p' looking for beginning of value`, because backticks
delimit a *JSON* literal and `pass` is not valid JSON. Two forms that do work,
both verified against v4.12.15:

```sh
orq doctor --json -q "checks[?status!='pass']"      # raw-string literal, outer double quotes
orq doctor --json -q 'checks[?status!=`"pass"`]'    # JSON literal, note the inner quotes
```

Prefer the first. The second needs backticks to survive the shell, which they do
inside single quotes in `sh`/`bash`/`zsh` but not everywhere.

Response envelopes are consistent: list endpoints return
`{ "object": "list", "data": [...], "has_more": bool }`, and the trace endpoints
add `next_page_token`.

Useful field names, confirmed against the spec:

- **trace summaries** (`traces search`, `traces get`): `trace_id`, `root_span_id`,
  `leading_span_id`, `name`, `operation`, `status`, `started_at`, `ended_at`,
  `duration_ms`, `project_id`, `identity_id`, `session_id`, `thread_id`,
  `product`, `providers`, `models`, `agent`, `usage`, `cost`
- **span summaries** (`traces list-spans`): `trace_id`, `span_id`,
  `parent_span_id`, `name`, `type`, `operation`, `status`, `started_at`,
  `ended_at`, `duration_ms`, `provider`, `model`, `usage`, `cost`, `has_detail`
- **agents** (`agents list`): `_id`, `key`, `display_name`, `status`, `version`,
  `path`, `model`, `role`, `description`, `instructions`, `tools`, `skills`,
  `knowledge_bases`, `memory_stores`, `created`, `updated`

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

`traces aggregate` computes metrics over the same window, and `traces get-span`
returns one span in full (`list-spans` returns summaries, with `has_detail`
flagging which spans have more to fetch).

---

## Building app URLs

App URLs are workspace-scoped by **key** (the slug), not by id:

```
https://my.orq.ai/<workspace-key>/traces?query=trace_id:is:<trace-id>
https://my.orq.ai/<workspace-key>/experiments/<experiment-id>
```

```sh
key="$(orq auth whoami --json -q active_workspace_key --raw)"
echo "https://my.orq.ai/${key}/traces?query=$(printf 'trace_id:is:%s' "$trace_id" | jq -sRr @uri)"
```

Two different hosts are in play, and confusing them produces links that 404:

- `https://my.orq.ai` is the **app** (Studio) host, what a browser opens.
- `https://api.orq.ai` is the CLI's default **API** base, confirmed by
  `orq doctor --json -q 'config.api_base_url'`.

Override the app host with `ORQ_UI_BASE_URL` / `ORQ_BASE_URL`; override the API
host with `--server` or `ORQ_API_BASE_URL`.

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
orq request GET /v2/traces/fields --json
orq request POST /v2/traces/search --json < body.json
```

It reuses the configured profile, auth, and server, so it respects
`--profile` and `--server` like everything else.
