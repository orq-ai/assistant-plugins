---
name: orq-link
description: >
  Use when a reply should give the orq.ai Studio link to a workspace entity:
  an agent, dataset, deployment, experiment, prompt, evaluator, knowledge
  base, tool, skill, annotation queue, MCP server or gateway, or a list page
  such as API keys or the provider keys (BYOK) page.
  Triggers on "link to that dataset", "where do I open this agent in orq",
  "send me the URL of experiment X".
  Builds `<host>/<workspace-key>/<route>` from the CLI ids. Do NOT use for
  traces or spans (use orq-trace-format), or for orq documentation pages.
allowed-tools: Read, Skill, Bash(orq auth whoami:*), Bash(orq agents list:*), Bash(orq datasets list:*), Bash(orq deployments list:*), Bash(orq experiments list:*), Bash(orq prompts list:*), Bash(orq evals all:*), Bash(orq knowledge-bases list:*), Bash(orq tools list:*), Bash(orq skills list:*), Bash(orq annotation-queues list:*), Bash(orq mcp-servers list:*), Bash(orq mcp-gateways list:*), Bash(orq models list:*)
metadata:
  verified: "2026-11-24"
  surface: cli
---

# orq Studio links

**An orq entity named to a person comes with its Studio link.** The route table lives in [`studio-routes.md`](../orq-shared/resources/studio-routes.md); this skill finds the id, fills in the row and hands the link over.

## Gotchas (read first)

Probed 2026-11-24 with CLI 12.13.0 on prod, research workspace, project API key.

- **Routes take the id, never the key or name.** `datasets/<display_name>` opens nothing. The id field differs per group; the routes file names it per row.
- **Datasets, experiments, skills and annotation queues have no `key`.** Match the user's name against `display_name`. Names are not unique: two datasets can share one, so ask when the match is not a single row.
- **Lists page.** Pass each group's maximum `--limit` from the [`orq-cli`](../orq-cli/SKILL.md) "Lists truncate silently" table (`deployments` stops at 50); when `has_more` is `true`, page with `--starting-after <last id>` before saying an entity does not exist.
- **A project-scoped key lists only its project.** An entity in another project of the same workspace is missing from the list, not deleted.

## When to use

- The user asks for the link to, or where to open, an entity listed in the routes file
- A reply names an agent, dataset, experiment or other listed entity the user will want to open
- Another skill needs a list page link (API keys, provider keys, a provider page)

## When NOT to use

- A trace or span: use `orq-trace-format`
- orq documentation pages (docs.orq.ai): this skill builds app links only
- Entities with no row in the routes file (projects, teams, roles, routing rules, workflows, memory stores, monitors): say there is no checked route and give the closest list page if one exists

## Constraints

- **NEVER** guess an id or build a route from a name or key.
- **NEVER** build a URL with `null` or an empty workspace key or host.
- **NEVER** expand or print `$ORQ_API_KEY` (see [`orq-cli`](../orq-cli/SKILL.md)).
- **ALWAYS** take the route and the id field from the routes file, not from memory.
- **ALWAYS** mark a link as unverified when its row says so.

## Workflow Checklist

```
- [ ] 1. Read the routes file, find the row
- [ ] 2. Resolve host and workspace key (orq-trace-format step 2)
- [ ] 3. Find the id with the row's list command
- [ ] 4. Build and hand over the link
```

## Steps

1. **Find the row.** Read [`studio-routes.md`](../orq-shared/resources/studio-routes.md). Take the route, the CLI field and the name field. No row: stop and say so (see When NOT to use).
2. **Resolve host and workspace key** once per session, by the rules of [`orq-trace-format`](../orq-trace-format/SKILL.md) step 2: `ORQ_WORKSPACE` and `ORQ_UI_BASE_URL` when set, otherwise `orq auth whoami -o json -j '{key: active_workspace_key, host: urls.api_base_url || server}'`. A `null` key (for example under `ORQ_API_KEY`) means ask the user for the workspace key. If the entity came from a call against another workspace, use that one.
3. **Find the id.** Skip this when the id is already in the conversation from CLI output. Otherwise list and project the id and name fields, for example for a dataset:

   ```sh
   orq datasets list --limit 200 -o json -j '{has_more: has_more, rows: data[].{id: _id, name: display_name}}'
   ```

   Evaluators list with `orq evals all`. More than one match: show them and ask which. No match and `has_more` is `true`: page with `--starting-after`. No match at all: say so; with a project-scoped key, say it may sit in another project.
4. **Build the link** in your reply as `<host>/<workspace-key>/<route>`, with the trailing `/` of the host dropped. Accept only id characters (letters, digits, `-`, `_`); reject `/`, `:`, `?`, `#` and parentheses.

   For a provider page take `<name>` from the `provider` field of `orq models list -o json`. List pages have no id.
5. **Hand it over** as `[<name>](<url>)`, with angle brackets when the route has parentheses. Under it, when the row is unverified: *this route was read from the app source and has not been opened in a browser yet.*

## Done When

- Every link uses an id read from CLI output and a route from the routes file
- No link contains `null`, an empty segment, or a name in place of an id
- Unverified routes are marked as such
- Ambiguous names were resolved by the user, not by picking the first row

## Open in orq.ai

- **The entity:** the link from step 5
- **Traces and spans:** [`orq-trace-format`](../orq-trace-format/SKILL.md)

## Companion skills

- `orq-trace-format`: owns trace and span links and the host and workspace key snippet
- `orq-cli`: list flags, paging and output projection
- `orq-provider-credentials`: uses the provider page route
- `orq-admin`: uses the API key and management key routes

## Documentation & Resolution

**Lookup order: [`doc-resolution.md`](../orq-shared/resources/doc-resolution.md).** Live reads first (`orq <group> list --help`, the list output), then [`studio-routes.md`](../orq-shared/resources/studio-routes.md).
