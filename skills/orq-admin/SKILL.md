---
name: orq-admin
description: >
  Use for orq.ai workspace administration from the CLI: invite or remove
  workspace members and change their role, mint scoped API keys (one project,
  read-only, restricted, expiring), rotate, disable or delete API keys, and
  manage management keys. Triggers on "invite alice@example.com to my orq
  workspace", "create a read-only key for project X", "rotate our production
  API key", "remove Bob from the workspace". Do NOT use to log in to the CLI
  or switch profiles (use orq-cli), or to connect model provider keys (use
  orq-provider-credentials).
allowed-tools: Read, Write, AskUserQuestion, Skill, Bash(orq auth whoami:*), Bash(env -u ORQ_API_KEY orq auth whoami:*), Bash(orq auth profile add:*), Bash(orq auth profile list:*), Bash(orq auth profile clear:*), Bash(orq users list:*), Bash(orq users get:*), Bash(orq users create:*), Bash(orq users update:*), Bash(orq users delete:*), Bash(orq api-keys list:*), Bash(orq api-keys get:*), Bash(orq api-keys create:*), Bash(orq api-keys update:*), Bash(orq api-keys delete:*), Bash(orq api-keys list-capabilities:*), Bash(orq management-keys list:*), Bash(orq management-keys get:*), Bash(orq management-keys create:*), Bash(orq management-keys update:*), Bash(orq management-keys delete:*), Bash(orq management-keys list-capabilities:*), Bash(env -u ORQ_API_KEY orq users:*), Bash(env -u ORQ_API_KEY orq api-keys:*), Bash(env -u ORQ_API_KEY orq management-keys:*)
metadata:
  verified: "2026-11-24"
  surface: cli
---

# Workspace admin

**Every command here needs a management key or a workspace admin login.** Product API keys (workspace, project, read-only) get 403. Every write is confirmed first; deletes and anything that cuts off access are confirmed twice.

## Gotchas (read first)

Probed 2026-11-24 with CLI 12.13.0 on prod: project keys for the 403s, then an admin login for a full API key and management key cycle (create, verify, rotate, disable, delete). `users create`, `update` and `delete` were not run.

- **The 403 messages name the missing permission.** With a project key: `users list` says "requires the user read permission", `management-keys list` "requires the management-key read permission", `api-keys list` "This API key type cannot access this endpoint". `orq auth whoami` exits 0 under any key and cannot tell them apart, so the preflight is the `list` of the group being changed.
- **`users list` returns `{users: [...]}`.** Each row has `id` (the workspace-user UUID that `users update` and `users delete` take), `account_id`, `display_name`, `email`, `team_ids` and `roles`.
- **Roles are opaque ids** (`01J3...`), and no CLI command names them. Ask the user which role id is the admin role; do not guess it from counts.
- **`users create` sends a welcome email** to every new account, and takes a **team UUID** from `team_ids` as its argument, not the workspace. Say so before running it.
- **`users delete` revokes every API key that user owns.** List those keys first: `orq api-keys list -o json -j "[?owner.user_id=='<account_id>' || owner.user_id=='<id>'].{id: id, name: name}"`. `owner.user_id` holds the account id on some keys and the workspace-user id on others, so match both.
- **A new key's raw token is returned once**, in `token`; `get` and `list` only return a masked value. If it is not captured on create, the key has to be made again.
- **`api-keys list` returns a bare array of every key**, with no paging. A one-project scope is `{"mode": "single", "project_id": "<id>"}`.
- **`api-keys create` takes `project_scope`, `permission_mode`, `access`, `constraints.expires_at` and `owner`.** `expiration` and `projects` are legacy; do not use them. With no `owner` the key belongs to a service account and outlives any user; a user-owned key (`{"type": "user", "user_id": ...}`) is revoked with its user. The `get` projection in step 5 is accepted by `create` as is.
- **`management-keys create` takes a different body:** `name`, `permission_mode` (both required), `access` and a top-level `expires_at`, with no `project_scope` or `owner`. The raw secret is in `token`. `management-keys list` wraps rows in `data`, names the id `management_key_id`, and pages with `--limit` (up to 200) and `--starting-after`.
- **Management key enums are prefixed:** `MANAGEMENT_PERMISSION_MODE_*` and `MANAGEMENT_KEY_STATUS_*`. API keys accept `all`, `restricted`, `read_only` and `active`, `disabled`, `revoked`.
- **`revoked` is final** for both kinds of key. Use `disabled` to switch a key off reversibly; a disabled key gets 401 at once.
- **`orq auth profile add` makes the new profile active.** Every later command then runs on the new key until `orq auth profile clear`. Run it right after each add.
- **No CLI call maps the key in use to its id.** Any key could be the one this session runs on.
- **`whoami` under a login names the user** in `user.id`, which is `account_id` in `users list`. Under a management key it returns no user and no workspace key.

## When to use

- Invite, remove or change the role of workspace members
- Create, scope, rotate, disable or delete API keys or management keys
- Audit which keys exist, who owns them and when they expire

## When NOT to use

- Logging in, profiles, which credential the CLI uses: `orq-cli`
- Model provider keys (OpenAI, Azure, Bedrock): `orq-provider-credentials`
- Keys a coding agent mints for itself: [`coding-agents.md`](../orq-cli/resources/coding-agents.md)

## Constraints

- **NEVER** print a token, put one in chat or on a command line, or Read a file that holds one.
- **NEVER** delete a user, remove a role or delete or narrow a key without two confirmations.
- **NEVER** leave the workspace without an admin: before deleting a user or changing a role, ask which role id is admin, count the `users list` rows that hold it, show them, and refuse if this would remove the last one.
- **NEVER** delete the user running this session. Under a login, `env -u ORQ_API_KEY orq auth whoami -o json -j user.id` gives their `account_id`; under a management key there is no user, so ask who they are.
- **ALWAYS** confirm every create and update with AskUserQuestion, showing the exact fields.
- **ALWAYS** send a new token straight into `orq auth profile add <name> --api-key-file -`, or to a file the user names and then `chmod 600`s.
- **ALWAYS** follow the credential rules in [`orq-cli`](../orq-cli/SKILL.md) (never expand `$ORQ_API_KEY`).

## Workflow Checklist

```
- [ ] 1. Preflight: list the group being changed
- [ ] 2. Read the current state (users, keys, capabilities)
- [ ] 3. Confirm, then write
- [ ] 4. Capture any new token without printing it
- [ ] 5. Verify with one read
```

## Steps

1. **Preflight.** Run the `list` of the group you will change: `orq users list -o json` for members, `orq api-keys list -o json` or `orq management-keys list -o json` for keys. On 403, the credential lacks that permission (a product key, or a restricted management key): ask the user for a management key profile (`orq auth profile add <name> --api-key-file -`, then `--profile <name>`) or an admin login (`orq auth login`, then `env -u ORQ_API_KEY orq ...` while the env var is set). Do not go on until that list works.
2. **Read first.** Use `orq users list`, `orq api-keys list` and `orq management-keys list`. For restricted keys, read the domains with `orq api-keys list-capabilities` or `orq management-keys list-capabilities`, and never guess a domain name. Read `--help` and `--example` for each write before building its body.
3. **Members.**
   - **Invite:** `orq users create <team-uuid> --emails <email> --roles <role>`. Say that it sends a welcome email, then confirm.
   - **Change role:** `orq users update <workspace-user-uuid> --role <role>`, after the last-admin check. Confirm.
   - **Remove:** list the keys that user owns, run the last-admin and own-user checks, then confirm twice. Run `orq users delete <workspace-user-uuid> --force`.
4. **New API key.** Write the body file. It holds no secret: `name`, `project_scope`, `permission_mode`, `access` and `constraints.expires_at`. Check with `orq auth profile list` that the profile name is free, and with `orq api-keys list` that the key name is. Confirm, then send the token straight to a profile:

   ```sh
   orq api-keys create --from-file <body.json> -o json -j token --raw | orq auth profile add <profile> --api-key-file -
   ```

   Then run `orq auth profile clear` at once, so the next command runs on the admin credential and not on the new key.

   For a key the user hands to another system, write it to a file they name instead (`orq api-keys create --from-file <body.json> -o json -j token --raw > <path>`) and tell them to `chmod 600` it at once. Then look up the new id by its unique name with `orq api-keys list -o json -j "[?name=='<name>'].id"`. If the pipe failed, the token is lost: tell the user, and disable that key by its id before creating another. Management keys work the same way with `orq management-keys create` and their own body (see Gotchas).
5. **Rotate** (in this order):
   1. `orq api-keys get <old-id> -o json -j '{name: name, project_scope: project_scope, permission_mode: permission_mode, access: access, constraints: constraints, owner: owner}'`.
   2. Write a create body with the same scope fields and owner. If the `get` output does not fit the create body as is, show both and ask before creating.
   3. Create the key as in step 4, into a new profile.
   4. Verify with one read the key's scope allows: `orq models list --profile <new-profile> -o json -j 'length(@)'` for a product key, `orq api-keys list --profile <new-profile> -o json` for an admin key.
   5. Tell the user to switch every consumer to the new key, and wait for them to say it is done.
   6. Disable the old key: `orq api-keys update <old-id> --status disabled`, confirmed.
   7. Delete it only after a second confirmation: `orq api-keys delete <old-id> --force`.

   A management key rotates the same way with `orq management-keys get`, `create`, `update` and `delete`. Its body has no `project_scope` or `owner`, the expiry is the top-level `expires_at`, the id is `management_key_id` in `management-keys list`, and the status value is `MANAGEMENT_KEY_STATUS_DISABLED`. Verify it with `orq users list --profile <new-profile> -o json -j 'length(users)'`.
6. **Disable, narrow or delete a key.** Deletes run with `--force`, since an agent shell has no terminal. Warn that it may be the key this session or a production system runs on, then confirm twice. Prefer `--status disabled` over delete or `revoked`.
7. **Verify.** Re-run the matching `list` and report the changed rows, with names, ids, status and expiry. Never include the token.

## Done When

- The preflight list passed before any write
- Every write was confirmed with its exact fields; deletes, role removals and key narrowing were confirmed twice
- New tokens went only to a profile or to a user-named file the user was told to `chmod 600`
- A rotation ended with the old key disabled, and deleted only after a second yes
- The last-admin and own-user guards held

## Open in orq.ai

From [`studio-routes.md`](../orq-shared/resources/studio-routes.md), built with [`orq-link`](../orq-link/SKILL.md):

- **API keys:** `admin/api-keys`
- **Management keys:** `admin/api-keys/management-keys`

## Companion skills

- `orq-cli`: profiles, `ORQ_API_KEY` precedence, `env -u`, `--api-key-file`
- `orq-link`: the API key and management key page links
- `orq-provider-credentials`: model provider keys, whose writes also need a management key

## Documentation & Resolution

**Lookup order: [`doc-resolution.md`](../orq-shared/resources/doc-resolution.md).** Live reads first (`orq <group> <verb> --help`, `--example`, `list-capabilities`), then the `orq-cli` skill.
