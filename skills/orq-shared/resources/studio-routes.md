# Studio routes

> **Read from the app's router, not yet opened in a browser.** Routes from orquesta-web
> `libs/stores/src/finder/utils/get-route-for-finder-item.ts` at `9e91829658`
> (origin/main, 2026-11-24, RES-1809). Sample ids from the research workspace on prod
> with CLI 12.13.0. A row says `unverified` until it has been opened cold in a new tab;
> say so when you hand out an unverified link.

Every route sits under the workspace **key** (the slug), not its id:

```
<host>/<workspace-key>/<route>
```

Host and workspace key are resolved as in [`orq-trace-format`](../../orq-trace-format/SKILL.md) step 2. Traces and spans are also owned there: `traces/(trace:<trace_id>)`.

## Entity pages

Each route takes the entity's **id**, not its key. The CLI field column names the field that holds the id in `orq <group> list -o json`; the name field is what to match a name the user gives against.

| Entity | Route | CLI field | Name field | Status |
|---|---|---|---|---|
| Agent | `agents/<id>` | `_id` | `key`, `display_name` | unverified |
| Dataset | `datasets/<id>` | `_id` | `display_name` | unverified |
| Deployment | `deployments/<id>` | `id` | `key` | unverified |
| Experiment | `experiments/<id>` | `experiment_id` | `display_name` | unverified |
| Prompt | `prompts/<id>` | not checked | not checked | unverified, none in the research workspace |
| Evaluator | `resources/evaluators/<id>` | `_id` (`orq evals all`) | `key` | unverified |
| Guardrail evaluator (AI Gateway) | `gateway/guardrails/<id>` | `_id` (`orq evals all`) | `key` | unverified |
| Knowledge base | `knowledge/<id>` | not checked | not checked | unverified, none in the research workspace |
| Memory store | `memory-store/<id>` | not checked | not checked | unverified, none in the research workspace |
| Tool | `tools/<id>` | `_id` | `key`, `display_name` | unverified |
| Skill | `skills/<id>` | `skill_id` | `display_name`, `path` | unverified |
| Annotation queue | `annotation-queues/<id>` | `_id` | `display_name` | unverified |
| Project | `projects/<id>` | `project_id` | `key`, `name` | unverified |
| MCP server | `mcps/<id>` | `_id` | `key`, `display_name` | unverified |
| MCP gateway | `mcp-portal/gateways/<id>` | `_id` | `key`, `display_name` | unverified |
| File system | `file-systems/<id>` | not checked | not checked | unverified, `file-systems list` is 403 in the research workspace |
| Monitor | `traces/monitors/<id>` | not checked | not checked | unverified, none in the research workspace |
| Alert | `traces/alerts/<id>` | not checked | not checked | unverified, none in the research workspace |

The source also has `gateway/` list pages (`gateway/smart-router`, `gateway/mcp-portal/gateways`), so a route above may have moved there.

## List pages (no id)

| Page | Route | Status |
|---|---|---|
| API keys | `admin/api-keys` | unverified |
| Management keys | `admin/api-keys/management-keys` | unverified |
| Integrations | `admin/integrations` | unverified |
| Budgets | `admin/budgets` | unverified |
| Notifiers | `admin/notifiers` | unverified |
| Webhooks | `admin/webhooks` | unverified |
| Identities | `identities` | unverified |
| Smart router | `router/smart-router` | unverified |

## Provider page

```
<host>/<workspace-key>/router/providers/(provider:<name>)
```

`<name>` is the provider id as `orq models list -o json` reports it in `provider` (for example `openai`, `azure`, `google-ai`; `google` is a separate provider). Status: unverified.

## Not here

Teams, roles, routing rules, guardrail rules, workflows, sheets, playgrounds, remote configs and variable collections have routes in the source file but no `orq` CLI group to list their ids, so a skill cannot fill them in. Add a row when the CLI gains the group.
