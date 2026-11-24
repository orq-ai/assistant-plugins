# Studio routes

> Routes from orquesta-web `libs/stores/src/finder/utils/get-route-for-finder-item.ts`
> at `9e91829658` (origin/main, 2026-11-24, RES-1809), each opened in a new tab on prod
> (research workspace, ids from CLI 12.13.0) on 2026-11-24. A row that says `unverified`
> has not been opened; say so when you hand out its link.

Every route sits under the workspace **key** (the slug), not its id:

```
<host>/<workspace-key>/<route>
```

Host and workspace key are resolved as in [`orq-trace-format`](../../orq-trace-format/SKILL.md) step 2. Traces and spans are also owned there: `traces/(trace:<trace_id>)`.

## Entity pages

Each route takes the entity's **id**, not its key. The CLI field column names the field that holds the id in `orq <group> list -o json`; the name field is what to match a name the user gives against.

| Entity | Route | CLI field | Name field | Status |
|---|---|---|---|---|
| Agent | `agents/<id>` | `_id` | `key`, `display_name` | verified |
| Dataset | `datasets/<id>` | `_id` | `display_name` | verified |
| Deployment | `deployments/<id>` | `id` | `key` | verified |
| Experiment | `experiments/<id>` | `experiment_id` | `display_name` | verified |
| Prompt | `prompts/<id>` | `_id` | `display_name` (`key` is null) | verified |
| Evaluator | `resources/evaluators/<id>` | `_id` (`orq evals all`) | `key` | verified |
| Guardrail evaluator (AI Gateway) | `gateway/guardrails/<id>` | `_id` (`orq evals all`) | `key` | unverified |
| Knowledge base | `knowledge/<id>` | `_id` | `key` | verified |
| Tool | `tools/<id>` | `_id` | `key`, `display_name` | verified |
| Skill | `skills/<id>` | `skill_id` | `display_name`, `path` | verified |
| Annotation queue | `annotation-queues/<id>` | `_id` | `display_name` | verified |
| MCP server | `mcp-portal/servers/<id>` | `_id` | `key`, `display_name` | verified |
| MCP gateway | `mcp-portal/gateways/<id>` | `_id` | `key`, `display_name` | verified |

Some routes redirect on load: a deployment opens its current version in the Prompt Studio, a knowledge base its data sources, an experiment adds `?runId=`. The link still works.

## List pages (no id)

| Page | Route | Status |
|---|---|---|
| API keys | `admin/api-keys` | verified |
| Management keys | `admin/api-keys/management-keys` | verified |
| Provider keys (BYOK) | `router/providers` | verified |

## Provider page

```
<host>/<workspace-key>/router/providers/(provider:<name>)
```

`<name>` is the provider id as `orq models list -o json` reports it in `provider` (for example `openai`, `azure`, `google-ai`; `google` is a separate provider). Status: verified.

## Not here

`projects/<id>` is not a project page: it switches the Studio's active project and lands on Activity, so projects have no row. `admin/integrations` also lands on Activity; provider keys, AWS Bedrock included, are on `router/providers`.

Entities with a route in the source but no CLI group to list their ids (teams, roles, routing rules, guardrail rules, workflows) or no instance to read the id field from (memory stores, file systems, monitors, alerts) have no row. Add one when both exist.
