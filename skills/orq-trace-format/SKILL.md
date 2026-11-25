---
name: orq-trace-format
description: >
  Load BEFORE writing any reply that names an orq.ai trace or span, even
  when the trace data is already in the conversation and no tool is needed.
  Answering about pasted `orq traces search` rows, a trace_id, span_id, a
  failing run, the slowest run, a summary of traces, or a deployment
  response's `x-orq-trace-id` / `telemetry.trace_id` all count: each trace
  in the reply must be a clickable Studio link `[<8-char prefix>](<url>)`,
  never a bare 32-hex id, and this skill has the URL shape, workspace key
  and host. Also for PRs, tickets, evaluator runs and experiments that cite
  traces. Do NOT use to query or analyze traces (use orq-analyze-traces or
  orq-cli), or to set up tracing (use orq-setup-observability).
allowed-tools: Bash(orq auth whoami:*), Read
metadata:
  verified: "2026-10-13"
  surface: cli
---

# orq trace links

**A trace or span shown to a person is a link, never a bare id.** A 32-hex id
in a table or a sentence is something the reader has to copy, paste and search
for; a link opens the trace in one click.

- Write the Markdown link syntax itself: `[016de4cd](<https://my.orq.ai/acme/traces/(trace:016de4cd7a2b4c21c707f00ad3185eb8)>)`, or a descriptive label (`[failing turn 3](<url>)`). The label is the bare 8-char prefix: no `…`, no full id. Never `016de4cd (https://…)`: that is an id with a URL pasted after it, and the reader still sees the raw URL.
- A raw id is fine only where a machine reads it: CLI arguments, JSON/YAML front matter, dataset rows, code. Or when the user asks for the id itself.
- No shell (MCP only), or step 2 below fails? Use host `https://my.orq.ai` unless the conversation shows another one, and ask the user once for the workspace key. Fall back to the id only if they do not give it.
- Several traces in a table: put the link in the id column; do not add a second column with the full id.

## URL shape

> **Trace and span routes opened in a browser on 2026-10-13 (RES-1550)**, cold in a
> new tab against live traces in a research workspace, prod only; staging was not opened.
> The `whoami` projections were run on CLI 11.0.2 under both a login session and
> `ORQ_API_KEY`. The experiments route is from CLI 5.1.0 and was not re-probed.

App URLs are workspace-scoped by **key** (the slug), not by id:

```
<host>/<workspace-key>/traces/(trace:<trace_id>)                    # a trace
<host>/<workspace-key>/traces/(trace:<trace_id>//span:<span_id>)    # one span in it
<host>/<workspace-key>/experiments/<experiment-id>                  # unverified, 5.1.0
```

## Steps

1. **Take the right id.** `trace_id` is the 32-hex id, `span_id` the 16-hex one. `traces search` rows carry `trace_id`, `span_id` and `root_span_id`; their `id` is a span id and opens nothing as a trace. `traces list-spans <trace_id>` gives the span ids inside a trace. From a router or deployment call: `x-orq-trace-id` header or `telemetry.trace_id`. A benchmark source id, local `run_id` or response id is a different identifier.
2. **Resolve workspace key and host** once per session:

   ```sh
   # ORQ_WORKSPACE is read by the CLI itself; ORQ_WORKSPACE_SLUG is the evaluatorq name for it.
   workspace_key="${ORQ_WORKSPACE:-${ORQ_WORKSPACE_SLUG:-}}"
   [ -z "$workspace_key" ] && workspace_key="$(orq auth whoami -o json -j active_workspace_key --raw 2>/dev/null)"
   host="${ORQ_UI_BASE_URL:-$(orq auth whoami -o json -j 'urls.api_base_url || server' --raw 2>/dev/null)}"
   host="${host%/}"
   for pair in "workspace_key=$workspace_key" "host=$host"; do
     case "${pair#*=}" in ''|null) echo "${pair%%=*} unknown; run 'orq auth login' or set ORQ_WORKSPACE / ORQ_UI_BASE_URL" >&2 ;; esac
   done
   ```

   If the call that produced the trace used a different workspace or a project-scoped key, use that call's workspace, not the CLI session's.
3. **Build the link.** Accept only URL-safe id characters (hex): reject `/`, `:` and parentheses so an id cannot change the locator.

   ```sh
   echo "${host}/${workspace_key}/traces/(trace:${trace_id})"
   echo "${host}/${workspace_key}/traces/(trace:${trace_id}//span:${span_id})"
   ```

4. **Hand it over** as `[label](<url>)` (angle brackets: the destination has parentheses), with one line under it: *if a link opens on "No spans match your search", switch the Traces project selector to **All projects** and reload.*

## Pitfalls

- **The parentheses and `//` are part of the route.** Quote the URL in a shell. In Markdown write `[label](<url>)`; a bare `(…)` destination with nested parentheses is fragile.
- **The old `traces?query=trace_id:is:<id>` form no longer routes.** Do not emit it.
- **No query string is needed.** The app appends `traces_oql`, `traces_range` and `traces_filters` as you click around; a link without them opens the same view.
- **The host comes from the session**, not a literal `my.orq.ai`: `urls.api_base_url` under a login session, `server` under `ORQ_API_KEY`. That the app is served from the API's host was checked on prod only. Where it differs (staging, self-hosted), `ORQ_UI_BASE_URL` overrides it, the same variable evaluatorq uses for its dashboard links; the CLI itself ignores it.
- **The key does not survive `ORQ_API_KEY`.** With the env var set, `whoami` reports `active_workspace_key` as `null`, so a key read blindly builds `<host>/null/...`. The snippet above rejects that.
- **"No spans match your search" means the user's project filter, not a bad link.** The Traces page keeps a project selection in the browser, and a link to a trace in another project opens that empty panel (a `traces_filters` project in the URL does not override it).
- **Ids from an invocation response are unverified.** Only ids read off `traces search` / `list-spans` were opened in the browser. If a link built from `telemetry.trace_id` / `telemetry.span_id` opens nothing, look the trace up with `orq traces search` and use its `trace_id`.
- **One run can span several traces, and one trace several runs.** An evaluator with several model calls can have several trace ids; link the one you mean and say which turn it is. A multi-turn conversation can share one trace id with a `span.agent` span per turn; link the span.
- **`https://my.orq.ai/v2/traces/<trace-id>/v3spans` is an authenticated JSON API endpoint**, not a Studio page. Never hand it to a person as a link.
- **Library code on a request path should not shell out to the CLI.** Pass the workspace key and host in as configuration.

For CLI authentication or workspace ambiguity, use the `orq-cli` skill.
